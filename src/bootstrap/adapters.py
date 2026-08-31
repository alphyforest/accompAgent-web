"""R2 适配层：把现有 DialogueEngine / ToolLoop 装配为应用层 Port 的实现。

- DialogueServiceAdapter：对话主链（chat_stream 透传）+ 工具上下文准备（prepare_turn + build_messages）
- ToolServiceAdapter：工具任务执行（can_handle / execute / cancel），不生成情绪前缀（STD-010 §4）

v1 简化说明（R3/R5 补齐）：execute 事件粒度为工具批次级（selected/started/completed）；
ToolExecutionResult.user_message 承载模型最终文本；capability 匹配用「有可用工具」近似，
R5 引入 ToolSpec capability 元数据后精确匹配。

本模块属于 Composition Root 边界（bootstrap），允许直接依赖 core 实现。
"""

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
from src.core.agent.dialogue import DialogueEngine
from src.core.tools.registry import ToolRegistry
from src.core.tools.runtime import ToolRuntime
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
        """普通对话：引擎 chunk 原样转成 message.delta 事件（对外协议不变）。"""
        yield DialogueEvent(type="message.started")
        async for chunk in self._engine.chat_stream(request.user_text, request.session_id):
            yield DialogueEvent(type="message.delta", content=chunk)
        yield DialogueEvent(type="message.completed")

    async def present_result_stream(self, request: PresentationRequest) -> AsyncIterator[DialogueEvent]:
        """工具结果呈现：按既有 [[EMOTION:..]] + 正文 协议输出并落库正文。"""
        text = request.result.user_message
        if not text:
            return
        emotion, body = self._engine.parse_response(text)
        yield DialogueEvent(type="message.started")
        yield DialogueEvent(type="message.delta", content=f"[[EMOTION:{emotion}]]")
        yield DialogueEvent(type="message.delta", content=body)
        yield DialogueEvent(type="message.completed")
        await self._engine.memory_add(request.context.session_id, "assistant", body)


class ToolServiceAdapter:
    """ToolServicePort 实现：包装 ToolLoop / ToolRegistry / ToolRuntime（R2 v1）。"""

    def __init__(
        self,
        loop: ToolLoop,
        registry: ToolRegistry,
        runtime: Optional[ToolRuntime] = None,
    ) -> None:
        self._loop = loop
        self._registry = registry
        self._runtime = runtime

    async def sync(self) -> None:
        """同步工具来源（懒连接；失败仅降级，不抛）。"""
        if self._runtime is None:
            return
        try:
            await self._runtime.sync()
        except Exception as exc:  # noqa: BLE001 - 来源同步失败不阻塞对话
            logger.warning("ToolService 来源同步失败 err={}", exc)

    async def can_handle(self, request: ToolRequest) -> CapabilityMatch:
        names = [spec.name for spec in self._registry.list() if not spec.disabled]
        matched = bool(request.capabilities) and bool(names)
        return CapabilityMatch(matched=matched, tool_names=names if matched else [])

    async def execute(self, request: ToolRequest) -> AsyncIterator[ToolEvent]:
        names = [spec.name for spec in self._registry.list() if not spec.disabled]
        batch = ",".join(names) if names else None
        if not request.messages:
            yield ToolEvent(
                type="tool.failed",
                error=CapabilityError(code="empty_context", user_message="工具上下文缺失，请重试"),
            )
            return
        yield ToolEvent(type="tool.selected", tool_name=batch)
        yield ToolEvent(type="tool.started", tool_name=batch)
        try:
            final = await self._loop.run(self._with_time_context(request.messages))
        except Exception as exc:  # noqa: BLE001 - 工具循环异常归一化为失败事件
            logger.warning("ToolService 工具循环异常 err={}", exc)
            yield ToolEvent(
                type="tool.failed",
                error=CapabilityError(code="tool_loop_error", user_message="工具处理失败，请稍后重试"),
            )
            return
        if final is None:
            yield ToolEvent(
                type="tool.failed",
                error=CapabilityError(code="tool_unavailable", user_message="工具当前不可用，已转为普通对话方式"),
            )
            return
        yield ToolEvent(
            type="tool.completed",
            tool_name=batch,
            result=ToolExecutionResult(tool_name=batch or "tool_task", user_message=final),
        )

    async def cancel(self, request_id: str) -> None:
        """取消占位通道（R5 引入真实取消传播）。"""
        return None

    def _with_time_context(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """SPEC-030 §10：ToolService 构造模型上下文时注入当前时间（引擎不再注入）。"""
        output: List[Dict[str, str]] = [dict(msg) for msg in messages]
        for msg in output:
            if msg.get("role") == "system":
                msg["content"] = f"{msg['content']}\n\n[当前时间] {current_time_context()}"
                break
        return output
