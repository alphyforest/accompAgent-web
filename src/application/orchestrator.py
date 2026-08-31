"""ConversationOrchestrator（PLAN-010 R4，SPEC-010 §5/§7 + SPEC-050 v1.0）。

- 产出规范 UIEvent：schema_version 1.0 / event_id / source / timestamp / sequence
- 支持 dialogue / tool / entertainment unavailable；每个 request 唯一终态 completed/cancelled/failed
- deadline（asyncio.timeout）与 cancel 占位通道

职责边界（STD-010 §2）：
- 编排层只依赖三个模块的公开 Port，不依赖模块内部类；
- 不修改 ToolResult 的事实/副作用字段，呈现交给 DialogueServicePort.present_result_stream。
"""

import asyncio
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Callable, Dict, Optional, cast
from uuid import uuid4

from src.application.contracts import (
    CapabilitySnapshot,
    DialogueEvent,
    DialogueRequest,
    DialogueServicePort,
    EntertainmentRequest,
    EntertainmentServicePort,
    PresentationRequest,
    RequestContext,
    RouteDecision,
    ToolExecutionResult,
    ToolRequest,
    ToolServicePort,
    UIEvent,
    UnavailableEntertainmentService,
)
from src.application.routing import CapabilityRouter
from src.utils.logger import logger

# 事件 type 前缀 -> source（SPEC-050 §3）
_SOURCE_BY_PREFIX: Dict[str, str] = {
    "message": "dialogue",
    "emotion": "dialogue",
    "mood": "dialogue",
    "tool": "tool",
    "entertainment": "entertainment",
    "request": "orchestrator",
}

Emitter = Callable[[str, str, Dict[str, Any]], UIEvent]


class ConversationOrchestrator:
    """会话型请求的确定性编排入口（R4：产出 SPEC-050 v1.0 UIEvent 流）。"""

    def __init__(
        self,
        router: CapabilityRouter,
        dialogue: DialogueServicePort,
        tool: ToolServicePort,
        entertainment: Optional[EntertainmentServicePort] = None,
    ) -> None:
        self._router = router
        self._dialogue = dialogue
        self._tool = tool
        self._entertainment = entertainment if entertainment is not None else UnavailableEntertainmentService()
        self._cancelled: set[str] = set()

    def cancel(self, request_id: str) -> None:
        """取消占位通道：标记取消；handle 在下一个事件边界检查并给出唯一终态。"""
        self._cancelled.add(request_id)

    async def handle(
        self,
        context: RequestContext,
        user_text: str,
        capabilities: CapabilitySnapshot,
    ) -> AsyncIterator[UIEvent]:
        """处理一次会话型请求，产出 SPEC-050 UIEvent 序列（唯一终态）。"""
        seq = 0

        def emit(source: str, event_type: str, payload: Dict[str, Any]) -> UIEvent:
            nonlocal seq
            seq += 1
            return UIEvent(
                schema_version="1.0",
                event_id=uuid4().hex,
                request_id=context.request_id,
                trace_id=context.trace_id,
                session_id=context.session_id,
                type=event_type,
                source=cast(Any, source),
                timestamp=datetime.now(UTC),
                sequence=seq,
                payload=payload,
            )

        yield emit("orchestrator", "request.accepted", {"mode": context.requested_mode, "input_accepted": True})
        decision = self._router.route(context, user_text, capabilities)
        yield emit(
            "orchestrator",
            "route.decided",
            {
                "target": decision.target,
                "reason_code": decision.reason_code,
                "confidence": decision.confidence,
                "selected_capabilities": list(decision.selected_capabilities),
            },
        )

        try:
            flow = self._run_flow(emit, context, user_text, decision)
            deadline = context.deadline_ms / 1000.0 if context.deadline_ms is not None else None
            if deadline is None:
                async for event in flow:
                    yield event
            else:
                async with asyncio.timeout(deadline):
                    async for event in flow:
                        yield event
        except TimeoutError:
            yield emit(
                "orchestrator",
                "request.failed",
                {"error_code": "deadline_exceeded", "user_message": "处理超时，请稍后重试", "retryable": True},
            )
        except Exception as exc:  # noqa: BLE001 - 编排层兜底，不向 UI 泄露内部错误
            logger.warning("ConversationOrchestrator 请求处理失败 request_id={} err={}", context.request_id, exc)
            yield emit(
                "orchestrator",
                "request.failed",
                {"error_code": "internal_error", "user_message": "服务器内部错误", "retryable": False},
            )

    # ---------------------------------------------------------------- 流量控制

    async def _run_flow(
        self,
        emit: Emitter,
        context: RequestContext,
        user_text: str,
        decision: RouteDecision,
    ) -> AsyncIterator[UIEvent]:
        """执行目标流并给出唯一终态：cancelled 优先于 completed。"""
        async for event in self._run_target(emit, context, user_text, decision):
            yield event
            if context.request_id in self._cancelled:
                yield emit(
                    "orchestrator",
                    "request.cancelled",
                    {"cancelled_by": "user", "side_effects_already_committed": False},
                )
                return
        yield emit("orchestrator", "request.completed", {"status": "success"})

    async def _run_target(
        self,
        emit: Emitter,
        context: RequestContext,
        user_text: str,
        decision: RouteDecision,
    ) -> AsyncIterator[UIEvent]:
        if decision.target == "tool":
            async for event in self._run_tool(emit, context, user_text, decision):
                yield event
            return
        if decision.target == "entertainment":
            async for event in self._run_entertainment(emit, context, user_text):
                yield event
            return
        async for event in self._run_dialogue(emit, context, user_text):
            yield event

    # ---------------------------------------------------------------- 目标执行

    async def _run_dialogue(
        self,
        emit: Emitter,
        context: RequestContext,
        user_text: str,
    ) -> AsyncIterator[UIEvent]:
        message_id = f"msg_{uuid4().hex[:12]}"
        request = DialogueRequest(user_text=user_text, session_id=context.session_id, context=context)
        async for event in self._dialogue.reply_stream(request):
            yield self._to_ui(emit, event, message_id)

    async def _run_tool(
        self,
        emit: Emitter,
        context: RequestContext,
        user_text: str,
        decision: RouteDecision,
    ) -> AsyncIterator[UIEvent]:
        tool_request = ToolRequest(
            user_text=user_text,
            context=context,
            capabilities=list(decision.selected_capabilities),
        )
        match = await self._tool.can_handle(tool_request)
        if not match.matched:
            # SPEC-010 §3.2 低置信度：转普通对话（v1 不注入额外提示）
            async for event in self._run_dialogue(emit, context, user_text):
                yield event
            return

        # 工具模块不依赖对话短期记忆：上下文快照由 DialogueService 准备好传入（STD-010 §4）
        messages = await self._dialogue.build_messages(context, user_text)
        tool_request = ToolRequest(
            user_text=user_text,
            context=context,
            capabilities=list(decision.selected_capabilities),
            messages=messages,
        )
        call_id = f"call_{uuid4().hex[:12]}"
        message_id = f"msg_{uuid4().hex[:12]}"
        async for tool_event in self._tool.execute(tool_request):
            payload: Dict[str, Any] = dict(tool_event.model_dump(exclude_none=True))
            if tool_event.type in {"tool.started", "tool.completed", "tool.failed"}:
                payload["call_id"] = call_id
            yield emit("tool", tool_event.type, payload)
            result = tool_event.result
            if result is None and tool_event.type == "tool.failed" and tool_event.error is not None:
                # 失败也要呈现（SPEC-010 §5.2/§8）：不得让失败静默或说成成功
                result = ToolExecutionResult(
                    tool_name=tool_event.tool_name or "tool_task",
                    status="failed",
                    error_code=tool_event.error.code,
                    user_message=tool_event.error.user_message,
                )
            if result is not None:
                presentation = PresentationRequest(result=result, context=context)
                async for d_event in self._dialogue.present_result_stream(presentation):
                    yield self._to_ui(emit, d_event, message_id)

    async def _run_entertainment(
        self,
        emit: Emitter,
        context: RequestContext,
        user_text: str,
    ) -> AsyncIterator[UIEvent]:
        session = await self._entertainment.active_session(context.session_id)
        if session is None or not session.active:
            yield emit("entertainment", "entertainment.unavailable", {"reason": "no_active_session"})
            async for event in self._run_dialogue(emit, context, user_text):
                yield event
            return
        message_id = f"msg_{uuid4().hex[:12]}"
        request = EntertainmentRequest(session_id=context.session_id, context=context)
        result = await self._entertainment.handle(request)
        payload: Dict[str, Any] = dict(result.model_dump(exclude_none=True))
        yield emit("entertainment", "entertainment.state_changed", payload)
        if result.narration_prompt:
            yield emit(
                "dialogue",
                "message.delta",
                {
                    "message_id": message_id,
                    "text": result.narration_prompt,
                    "message_source": "entertainment_presentation",
                },
            )

    # ---------------------------------------------------------------- 事件映射

    def _to_ui(self, emit: Emitter, event: DialogueEvent, message_id: str) -> UIEvent:
        """DialogueEvent -> UIEvent（SPEC-050 §5 payload 整形）。"""
        payload: Dict[str, Any] = dict(event.payload)
        if event.type == "message.started":
            payload["message_id"] = message_id
            payload.setdefault("role", "assistant")
            payload.setdefault("message_source", "reply")
        elif event.type == "message.delta":
            payload["message_id"] = message_id
            payload["text"] = event.content
        elif event.type == "message.completed":
            payload["message_id"] = message_id
            payload["text"] = event.content
            if event.emotion:
                payload["emotion"] = event.emotion
        elif event.type == "emotion.changed":
            if not payload:
                payload = {"emotion": event.emotion, "portrait_id": event.emotion}
        elif event.type == "mood.changed":
            pass  # payload 由对话模块提供
        else:
            payload.setdefault("content", event.content)
        source = _SOURCE_BY_PREFIX.get(event.type.split(".", 1)[0], "dialogue")
        return emit(source, event.type, payload)
