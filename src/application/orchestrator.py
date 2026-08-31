"""ConversationOrchestrator（PLAN-010 R1，SPEC-010 §5/§7）。

第一版只支持：dialogue / tool / entertainment unavailable；
透传 request/trace id；提供 deadline（asyncio.timeout）与 cancel 占位通道。

职责边界（STD-010 §2）：
- 编排层只依赖三个模块的公开 Port，不依赖模块内部类；
- 不修改 ToolResult 的事实/副作用字段，呈现交给 DialogueServicePort.present_result_stream。
"""

import asyncio
from typing import Any, AsyncIterator, Dict, Optional

from src.application.contracts import (
    CapabilitySnapshot,
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


class ConversationOrchestrator:
    """会话型请求的确定性编排入口。"""

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
        """处理一次会话型请求，产出 UIEvent 序列（accepted -> ... -> completed/cancelled/error）。"""
        yield self._emit(context, "request.accepted", {"mode": context.requested_mode})
        decision = self._router.route(context, user_text, capabilities)
        yield self._emit(
            context,
            "route.decided",
            {
                "target": decision.target,
                "reason_code": decision.reason_code,
                "confidence": decision.confidence,
                "selected_capabilities": list(decision.selected_capabilities),
            },
        )

        try:
            flow = self._run_flow(context, user_text, decision)
            deadline = context.deadline_ms / 1000.0 if context.deadline_ms is not None else None
            if deadline is None:
                async for event in flow:
                    yield event
            else:
                async with asyncio.timeout(deadline):
                    async for event in flow:
                        yield event
        except TimeoutError:
            yield self._emit(context, "request.error", {"code": "deadline_exceeded"})
        except Exception as exc:  # noqa: BLE001 - 编排层兜底，不向 UI 泄露内部错误
            logger.warning("ConversationOrchestrator 请求处理失败 request_id={} err={}", context.request_id, exc)
            yield self._emit(context, "request.error", {"code": "internal_error"})

    async def _run_flow(
        self,
        context: RequestContext,
        user_text: str,
        decision: RouteDecision,
    ) -> AsyncIterator[UIEvent]:
        """执行目标流并给出唯一终态：cancelled 优先于 completed。"""
        async for event in self._run_target(context, user_text, decision):
            yield event
            if context.request_id in self._cancelled:
                yield self._emit(context, "request.cancelled", {})
                return
        yield self._emit(context, "request.completed", {})

    # ---------------------------------------------------------------- 目标执行

    async def _run_target(
        self,
        context: RequestContext,
        user_text: str,
        decision: RouteDecision,
    ) -> AsyncIterator[UIEvent]:
        if decision.target == "tool":
            async for event in self._run_tool(context, user_text, decision):
                yield event
            return
        if decision.target == "entertainment":
            async for event in self._run_entertainment(context, user_text):
                yield event
            return
        async for event in self._run_dialogue(context, user_text):
            yield event

    async def _run_dialogue(self, context: RequestContext, user_text: str) -> AsyncIterator[UIEvent]:
        request = DialogueRequest(user_text=user_text, session_id=context.session_id, context=context)
        async for event in self._dialogue.reply_stream(request):
            yield self._emit(context, event.type, dict(event.model_dump(exclude_none=True)))

    async def _run_tool(
        self,
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
            async for event in self._run_dialogue(context, user_text):
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
        async for tool_event in self._tool.execute(tool_request):
            yield self._emit(context, tool_event.type, dict(tool_event.model_dump(exclude_none=True)))
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
                    yield self._emit(context, d_event.type, dict(d_event.model_dump(exclude_none=True)))

    async def _run_entertainment(self, context: RequestContext, user_text: str) -> AsyncIterator[UIEvent]:
        session = await self._entertainment.active_session(context.session_id)
        if session is None or not session.active:
            yield self._emit(context, "entertainment.unavailable", {"reason": "no_active_session"})
            async for event in self._run_dialogue(context, user_text):
                yield event
            return
        request = EntertainmentRequest(session_id=context.session_id, context=context)
        result = await self._entertainment.handle(request)
        payload: Dict[str, Any] = dict(result.model_dump(exclude_none=True))
        yield self._emit(context, "entertainment.state_changed", payload)
        if result.narration_prompt:
            yield self._emit(context, "message.delta", {"content": result.narration_prompt})

    # ---------------------------------------------------------------- 工具方法

    def _emit(self, context: RequestContext, event_type: str, payload: Dict[str, Any]) -> UIEvent:
        return UIEvent(
            type=event_type,
            request_id=context.request_id,
            trace_id=context.trace_id,
            session_id=context.session_id,
            payload=payload,
        )
