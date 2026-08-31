"""R2 适配层：把现有 DialogueEngine / ToolLoop 装配为应用层 Port 的实现。

- DialogueServiceAdapter：对话主链（chat_stream 透传）+ 工具上下文准备（prepare_turn + build_messages）
- ToolServiceAdapter：工具任务执行（can_handle / execute / cancel），不生成情绪前缀（STD-010 §4）

v1 简化说明（R3/R5 补齐）：execute 事件粒度为工具批次级（selected/started/completed）；
ToolExecutionResult.user_message 承载模型最终文本；capability 匹配用「有可用工具」近似，
R5 引入 ToolSpec capability 元数据后精确匹配。

本模块属于 Composition Root 边界（bootstrap），允许直接依赖 core 实现。
"""

import time
from typing import AsyncIterator, Dict, List, Optional

from src.application.contracts import (
    CapabilityError,
    CapabilityMatch,
    DialogueEvent,
    DialogueRequest,
    PresentationRequest,
    RequestContext,
    ToolEvent,
    ToolExecutionResult,
    ToolRequest,
)
from src.core.agent.dialogue import EMOTION_MARK_PREFIX, DialogueEngine
from src.core.tools.catalog import ToolCatalog
from src.core.tools.policy import ToolPolicy
from src.core.tools.registry import ToolRegistry
from src.core.tools.runtime import ToolRuntime
from src.core.tools.spec import ToolSpec
from src.core.tools.time_context import current_time_context
from src.core.tools.tool_loop import ToolLoop
from src.utils.logger import logger


class DialogueServiceAdapter:
    """DialogueServicePort 实现：包装 DialogueEngine（R2 兼容适配，不移动实现）。"""

    def __init__(self, engine: DialogueEngine) -> None:
        self._engine = engine

    async def build_messages(self, context: RequestContext, user_text: str) -> List[Dict[str, str]]:
        """工具路径的消息组装：先走一轮请求前奏（气氛/记忆），再构建模型消息。"""
        await self._engine.prepare_turn(user_text, context.session_id)
        return await self._engine.build_messages(user_text, context.session_id)

    async def reply_stream(self, request: DialogueRequest) -> AsyncIterator[DialogueEvent]:
        """普通对话（R4 事件语义）：情绪标记帧 + 正文增量 + emotion/mood 独立事件。

        message_source=reply；emotion_mark 帧供 legacy 文本协议重组，UIEvent 客户端跳过。
        """
        yield DialogueEvent(type="message.started", payload={"message_source": "reply"})
        emotion: Optional[str] = None
        body = ""
        async for chunk in self._engine.chat_stream(request.user_text, request.session_id):
            if chunk.startswith(EMOTION_MARK_PREFIX) and chunk.endswith("]]") and emotion is None:
                emotion = chunk[len(EMOTION_MARK_PREFIX) : -2]
                yield DialogueEvent(
                    type="message.delta",
                    content=chunk,
                    payload={"message_source": "reply", "emotion_mark": True},
                )
            else:
                body += chunk
                yield DialogueEvent(type="message.delta", content=chunk, payload={"message_source": "reply"})
        if emotion is None:
            emotion = self._engine.init_emotion
        yield DialogueEvent(type="emotion.changed", payload={"emotion": emotion, "portrait_id": emotion})
        yield DialogueEvent(
            type="mood.changed",
            payload={"value": self._engine.mood.mood, "label": self._engine.mood.get_label()},
        )
        yield DialogueEvent(
            type="message.completed",
            content=body,
            emotion=emotion,
            payload={"message_source": "reply"},
        )

    async def present_result_stream(self, request: PresentationRequest) -> AsyncIterator[DialogueEvent]:
        """工具结果呈现（R4 事件语义）：message_source=tool_presentation + 情绪独立事件。"""
        text = request.result.user_message
        if not text:
            return
        emotion, body = self._engine.parse_response(text)
        yield DialogueEvent(type="message.started", payload={"message_source": "tool_presentation"})
        yield DialogueEvent(
            type="message.delta",
            content=f"{EMOTION_MARK_PREFIX}{emotion}]]",
            payload={"message_source": "tool_presentation", "emotion_mark": True},
        )
        yield DialogueEvent(
            type="message.delta",
            content=body,
            payload={"message_source": "tool_presentation"},
        )
        yield DialogueEvent(type="emotion.changed", payload={"emotion": emotion, "portrait_id": emotion})
        yield DialogueEvent(
            type="message.completed",
            content=body,
            emotion=emotion,
            payload={"message_source": "tool_presentation"},
        )
        await self._engine.memory_add(request.context.session_id, "assistant", body)


class ToolServiceAdapter:
    """ToolServicePort 实现：包装 ToolLoop / ToolCatalog / ToolPolicy（R5）。

    - capability 子集选择：只把与请求能力相关的工具暴露给模型（SPEC-030 §4/§5）
    - 执行前策略检查：Agenda 自动执行（ADR-004）；其余需确认写工具先发 confirmation_required
    - 审计：执行时长/状态/错误码经结构化日志记录（带 source_id）
    """

    def __init__(
        self,
        loop: ToolLoop,
        registry: ToolRegistry,
        runtime: Optional[ToolRuntime] = None,
        catalog: Optional[ToolCatalog] = None,
        policy: Optional[ToolPolicy] = None,
    ) -> None:
        self._loop = loop
        self._registry = registry
        self._runtime = runtime
        self._catalog = catalog or ToolCatalog(registry)
        self._policy = policy or ToolPolicy()

    async def sync(self) -> None:
        """同步工具来源（懒连接；失败仅降级，不抛）。"""
        if self._runtime is None:
            return
        try:
            await self._runtime.sync()
        except Exception as exc:  # noqa: BLE001 - 来源同步失败不阻塞对话
            logger.warning("ToolService 来源同步失败 err={}", exc)

    def _capability_tools(self, request: ToolRequest) -> List[ToolSpec]:
        """按请求能力筛选工具子集（generic 兜底），返回 ToolSpec 列表。"""
        caps = set(request.capabilities)
        snapshot = self._catalog.snapshot()
        if not caps:
            return snapshot.all()
        return [
            spec
            for spec in snapshot.all()
            if spec.capability in caps or spec.capability == "generic"
        ]

    async def can_handle(self, request: ToolRequest) -> CapabilityMatch:
        tools = self._capability_tools(request)
        names = [spec.name for spec in tools]
        return CapabilityMatch(matched=bool(names), tool_names=names)

    async def execute(self, request: ToolRequest) -> AsyncIterator[ToolEvent]:
        tools = self._capability_tools(request)
        names = [spec.name for spec in tools]
        batch = ",".join(names) if names else None
        if not request.messages:
            yield ToolEvent(
                type="tool.failed",
                error=CapabilityError(code="empty_context", user_message="工具上下文缺失，请重试"),
            )
            return
        if not tools:
            yield ToolEvent(
                type="tool.failed",
                error=CapabilityError(code="no_tool_matches", user_message="没有可用的工具执行本次请求"),
            )
            return

        # 策略检查（SPEC-030 §6）：deny -> failed；require_confirmation -> confirmation_required
        for spec in tools:
            decision = self._policy.decide(spec)
            if not decision.allow:
                yield ToolEvent(
                    type="tool.failed",
                    tool_name=spec.name,
                    error=CapabilityError(code="tool_disabled", user_message=f"工具 {spec.name} 当前不可用"),
                )
                return
            if decision.require_confirmation:
                yield ToolEvent(
                    type="tool.confirmation_required",
                    tool_name=spec.name,
                    error=CapabilityError(
                        code="confirmation_required",
                        user_message=f"工具 {spec.name} 需要用户确认后才能执行",
                    ),
                )
                return

        yield ToolEvent(type="tool.selected", tool_name=batch)
        yield ToolEvent(type="tool.started", tool_name=batch)
        start = time.perf_counter()
        error_code: Optional[str] = None
        try:
            final = await self._loop.run(self._with_time_context(request.messages), tools=tools)
        except Exception as exc:  # noqa: BLE001 - 工具循环异常归一化为失败事件
            error_code = "tool_loop_error"
            logger.warning("ToolService 工具循环异常 err={}", exc)
            final = None
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "tool_exec source={} tools={} status={} duration_ms={} error_code={}",
            ",".join(sorted({spec.source_id for spec in tools})) or "?",
            batch,
            "success" if final is not None and error_code is None else "failed",
            duration_ms,
            error_code or "",
        )
        if final is None:
            key = "tool_unavailable" if error_code is None else error_code
            yield ToolEvent(
                type="tool.failed",
                tool_name=batch,
                error=CapabilityError(code=key, user_message="工具当前不可用，已转为普通对话方式"),
            )
            return
        yield ToolEvent(
            type="tool.completed",
            tool_name=batch,
            result=ToolExecutionResult(
                tool_name=batch or "tool_task",
                user_message=final,
                duration_ms=duration_ms,
            ),
        )

    async def cancel(self, request_id: str) -> None:
        """取消占位通道（R5b 引入真实取消传播）。"""
        return None

    def _with_time_context(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """SPEC-030 §10：ToolService 构造模型上下文时注入当前时间（引擎不再注入）。"""
        output: List[Dict[str, str]] = [dict(msg) for msg in messages]
        for msg in output:
            if msg.get("role") == "system":
                msg["content"] = f"{msg['content']}\n\n[当前时间] {current_time_context()}"
                break
        return output
