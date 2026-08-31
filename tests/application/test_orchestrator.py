"""ConversationOrchestrator 编排测试（SPEC-010 §10 测试矩阵；Fake 模块，不连真实实现）。"""

import asyncio
from typing import AsyncIterator, List, Optional

from src.application.contracts import (
    CapabilityMatch,
    CapabilitySnapshot,
    DialogueEvent,
    DialogueRequest,
    EntertainmentRequest,
    EntertainmentResult,
    EntertainmentSession,
    PresentationRequest,
    RequestContext,
    ToolEvent,
    ToolExecutionResult,
    ToolRequest,
    UIEvent,
)
from src.application.orchestrator import ConversationOrchestrator
from src.application.routing import CapabilityRouter


class FakeDialogueService:
    """按脚本产出对话事件；可注入延迟用于超时测试。"""

    def __init__(
        self,
        reply_events: Optional[List[DialogueEvent]] = None,
        present_events: Optional[List[DialogueEvent]] = None,
        reply_delay: float = 0.0,
    ) -> None:
        self.reply_events = reply_events or [
            DialogueEvent(type="message.started"),
            DialogueEvent(type="message.delta", content="你好"),
            DialogueEvent(type="message.completed", content="你好"),
        ]
        self.present_events = present_events or [
            DialogueEvent(type="message.started"),
            DialogueEvent(type="message.delta", content="已处理：日程已创建"),
            DialogueEvent(type="message.completed", content="已处理：日程已创建"),
        ]
        self.reply_delay = reply_delay
        self.presented: List[PresentationRequest] = []

    async def reply_stream(self, request: DialogueRequest) -> AsyncIterator[DialogueEvent]:
        if self.reply_delay:
            await asyncio.sleep(self.reply_delay)
        for event in self.reply_events:
            yield event

    async def present_result_stream(self, request: PresentationRequest) -> AsyncIterator[DialogueEvent]:
        self.presented.append(request)
        for event in self.present_events:
            yield event

    async def build_messages(self, context, user_text: str) -> list:
        return [{"role": "system", "content": "s"}, {"role": "user", "content": user_text}]


class FakeToolService:
    """按脚本产出工具事件；matched=False 模拟能力不匹配。"""

    def __init__(self, matched: bool = True, events: Optional[List[ToolEvent]] = None) -> None:
        self.matched = matched
        self.events = events or [
            ToolEvent(type="tool.selected", tool_name="calendar.create"),
            ToolEvent(type="tool.started", tool_name="calendar.create"),
            ToolEvent(
                type="tool.completed",
                tool_name="calendar.create",
                result=ToolExecutionResult(
                    tool_name="calendar.create",
                    data={"ok": True},
                    side_effects=["created:1"],
                ),
            ),
        ]
        self.requests: List[ToolRequest] = []

    async def can_handle(self, request: ToolRequest) -> CapabilityMatch:
        self.requests.append(request)
        return CapabilityMatch(
            matched=self.matched,
            tool_names=[event.tool_name or "" for event in self.events if event.tool_name],
        )

    async def execute(self, request: ToolRequest) -> AsyncIterator[ToolEvent]:
        for event in self.events:
            yield event

    async def cancel(self, request_id: str) -> None:
        return None


class FakeEntertainmentService:
    """可注入会话状态；None 模拟未启用/无活跃会话。"""

    def __init__(self, session: Optional[EntertainmentSession]) -> None:
        self.session = session

    async def active_session(self, session_id: str) -> Optional[EntertainmentSession]:
        return self.session

    async def handle(self, request: EntertainmentRequest) -> EntertainmentResult:
        return EntertainmentResult(available=True, state_changed=True, narration_prompt="欢迎回来")


def _ctx(deadline_ms: Optional[int] = None, mode: str = "auto") -> RequestContext:
    return RequestContext(
        request_id="req-1",
        trace_id="trace-1",
        session_id="s1",
        character_id="elysia",
        requested_mode=mode,
        deadline_ms=deadline_ms,
    )


async def _collect(
    orchestrator: ConversationOrchestrator,
    context: RequestContext,
    text: str,
    caps: CapabilitySnapshot,
) -> List[UIEvent]:
    return [event async for event in orchestrator.handle(context, text, caps)]


async def test_dialogue_flow_event_sequence():
    orch = ConversationOrchestrator(CapabilityRouter(), FakeDialogueService(), FakeToolService(matched=False))
    events = await _collect(orch, _ctx(), "你好", CapabilitySnapshot())
    types = [e.type for e in events]
    assert types == [
        "request.accepted",
        "route.decided",
        "message.started",
        "message.delta",
        "message.completed",
        "request.completed",
    ]
    assert all(e.request_id == "req-1" and e.trace_id == "trace-1" for e in events)


async def test_tool_flow_presents_result_with_facts():
    dialogue = FakeDialogueService()
    tool = FakeToolService(matched=True)
    orch = ConversationOrchestrator(CapabilityRouter(), dialogue, tool)
    caps = CapabilitySnapshot(available_capabilities=["calendar.read"])
    events = await _collect(orch, _ctx(), "帮我查一下明天的日程", caps)
    types = [e.type for e in events]
    assert "tool.selected" in types
    assert "tool.completed" in types
    assert "message.started" in types
    assert "request.completed" in types
    assert types.index("route.decided") < types.index("tool.selected") < types.index("request.completed")
    # 编排层把 ToolResult 原样交给呈现端口（不改事实/副作用）
    assert len(dialogue.presented) == 1
    presented = dialogue.presented[0]
    assert presented.result.data == {"ok": True}
    assert presented.result.side_effects == ["created:1"]


async def test_tool_unmatched_falls_back_to_dialogue():
    orch = ConversationOrchestrator(CapabilityRouter(), FakeDialogueService(), FakeToolService(matched=False))
    caps = CapabilitySnapshot(available_capabilities=["calendar.read"])
    events = await _collect(orch, _ctx(), "帮我查一下明天的日程", caps)
    types = [e.type for e in events]
    assert "tool.selected" not in types
    assert any(t.startswith("message.") for t in types)
    assert types[-1] == "request.completed"


async def test_entertainment_unavailable_falls_back_to_dialogue():
    orch = ConversationOrchestrator(
        CapabilityRouter(),
        FakeDialogueService(),
        FakeToolService(),
        entertainment=FakeEntertainmentService(None),
    )
    events = await _collect(orch, _ctx(), "你好", CapabilitySnapshot(active_entertainment=True))
    types = [e.type for e in events]
    assert "entertainment.unavailable" in types
    assert any(t.startswith("message.") for t in types)


async def test_cancel_emits_unique_terminal_event():
    orch = ConversationOrchestrator(CapabilityRouter(), FakeDialogueService(), FakeToolService(matched=False))
    orch.cancel("req-1")
    events = await _collect(orch, _ctx(), "你好", CapabilitySnapshot())
    types = [e.type for e in events]
    assert "request.cancelled" in types
    assert "request.completed" not in types
    assert types[-1] == "request.cancelled"
    assert types[-2] == "message.started"  # 取消在最靠近的事件边界生效


async def test_deadline_exceeded_emits_error():
    slow = FakeDialogueService(reply_delay=0.2)
    orch = ConversationOrchestrator(CapabilityRouter(), slow, FakeToolService(matched=False))
    events = await _collect(orch, _ctx(deadline_ms=20), "你好", CapabilitySnapshot())
    types = [e.type for e in events]
    assert types[-1] == "request.error"
    assert events[-1].payload["code"] == "deadline_exceeded"
    assert "request.completed" not in types


async def test_contracts_serialize_json_round_trip():
    import json

    ctx = _ctx(deadline_ms=100)
    encoded = ctx.model_dump_json()
    assert json.loads(encoded)["request_id"] == "req-1"
    assert RequestContext.model_validate_json(encoded) == ctx

    result = ToolExecutionResult(tool_name="calendar.create", data={"ok": True}, side_effects=["created:1"])
    assert json.loads(result.model_dump_json())["status"] == "success"

    event = UIEvent(type="message.completed", request_id="req-1", trace_id="trace-1", payload={"content": "hi"})
    assert event.model_dump()["type"] == "message.completed"
