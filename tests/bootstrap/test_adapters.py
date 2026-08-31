"""R2：bootstrap/adapters.py 适配器单测（Fake 引擎 / Fake ToolLoop / 真实 Registry）。"""

from typing import AsyncIterator, Dict, List, Optional

from src.application.contracts import (
    DialogueRequest,
    PresentationRequest,
    RequestContext,
    ToolExecutionResult,
    ToolRequest,
)
from src.bootstrap.adapters import DialogueServiceAdapter, ToolServiceAdapter
from src.core.tools.registry import ToolRegistry
from src.core.tools.spec import ToolSpec


def _ctx(session: str = "s1") -> RequestContext:
    return RequestContext(request_id="r1", trace_id="t1", session_id=session, character_id="elysia")


class FakeEngine:
    """DialogueEngine 接口 Fake（适配器用到的成员）。"""

    def __init__(self) -> None:
        self.prepared: List[tuple[str, str]] = []
        self.stored: List[tuple[str, str, str]] = []
        self.chunks = ["[[EMOTION:happy]]", "你好呀"]

    async def chat_stream(self, user_input: str, session_id: str) -> AsyncIterator[str]:
        for chunk in self.chunks:
            yield chunk

    async def prepare_turn(self, user_input: str, session_id: str) -> None:
        self.prepared.append((user_input, session_id))

    async def build_messages(self, user_input: str, session_id: str) -> List[Dict[str, str]]:
        return [{"role": "system", "content": "s"}, {"role": "user", "content": user_input}]

    async def memory_add(self, session_id: str, role: str, content: str, source: str = "reply") -> None:
        self.stored.append((session_id, role, content))

    def parse_response(self, text: str) -> tuple[str, str]:
        if "[[EMOTION:" in text:
            return "happy", "正文"
        return "idle", text


class FakeToolLoop:
    def __init__(self, final: Optional[str] = None, error: Optional[Exception] = None) -> None:
        self.final = final
        self.error = error
        self.runs: List[List[Dict[str, str]]] = []

    async def run(self, messages: List[Dict[str, str]]) -> Optional[str]:
        self.runs.append(messages)
        if self.error is not None:
            raise self.error
        return self.final


def _registry() -> ToolRegistry:
    async def execute(args: Dict[str, object]) -> Dict[str, object]:
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="list_events",
            description="查询日程。",
            input_schema={"type": "object"},
            executable=execute,
        )
    )
    return registry


async def test_dialogue_reply_stream_passes_chunks():
    engine = FakeEngine()
    adapter = DialogueServiceAdapter(engine)  # type: ignore[arg-type]
    events = [
        e async for e in adapter.reply_stream(DialogueRequest(user_text="你好", session_id="s1", context=_ctx()))
    ]
    assert [e.type for e in events] == ["message.started", "message.delta", "message.delta", "message.completed"]
    assert [e.content for e in events if e.type == "message.delta"] == engine.chunks


async def test_dialogue_build_messages_prepares_turn():
    engine = FakeEngine()
    adapter = DialogueServiceAdapter(engine)  # type: ignore[arg-type]
    messages = await adapter.build_messages(_ctx(), "查日程")
    assert engine.prepared == [("查日程", "s1")]
    assert messages[-1] == {"role": "user", "content": "查日程"}


async def test_dialogue_present_result_emits_emotion_protocol_and_saves_body():
    engine = FakeEngine()
    adapter = DialogueServiceAdapter(engine)  # type: ignore[arg-type]
    result = ToolExecutionResult(tool_name="list_events", user_message="[[EMOTION:happy]]正文")
    events = [
        e async for e in adapter.present_result_stream(PresentationRequest(result=result, context=_ctx()))
    ]
    deltas = [e.content for e in events if e.type == "message.delta"]
    assert deltas == ["[[EMOTION:happy]]", "正文"]
    assert engine.stored[-1][1:] == ("assistant", "正文")


async def test_tool_can_handle_matches_enabled_tools():
    service = ToolServiceAdapter(FakeToolLoop(), _registry())  # type: ignore[arg-type]
    match = await service.can_handle(ToolRequest(user_text="查日程", context=_ctx(), capabilities=["calendar.read"]))
    assert match.matched is True
    assert "list_events" in match.tool_names


async def test_tool_execute_completed_carries_final_text():
    loop = FakeToolLoop(final="[[情绪:happy]查到了")
    service = ToolServiceAdapter(loop, _registry())  # type: ignore[arg-type]
    events = [
        e
        async for e in service.execute(
            ToolRequest(
                user_text="查日程",
                context=_ctx(),
                capabilities=["calendar.read"],
                messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "查日程"}],
            )
        )
    ]
    types = [e.type for e in events]
    assert types == ["tool.selected", "tool.started", "tool.completed"]
    assert events[-1].result is not None
    assert events[-1].result.user_message == "[[情绪:happy]查到了"
    # 时间上下文注入到 system 消息（SPEC-030 §10，引擎不再注入）
    assert "[当前时间]" in loop.runs[0][0]["content"]


async def test_tool_execute_failed_when_loop_returns_none():
    service = ToolServiceAdapter(FakeToolLoop(), _registry())  # type: ignore[arg-type]
    events = [
        e
        async for e in service.execute(
            ToolRequest(
                user_text="查日程",
                context=_ctx(),
                capabilities=["calendar.read"],
                messages=[{"role": "user", "content": "查日程"}],
            )
        )
    ]
    assert events[-1].type == "tool.failed"
    assert events[-1].error is not None
    assert events[-1].error.code == "tool_unavailable"


async def test_tool_execute_failed_without_context():
    service = ToolServiceAdapter(FakeToolLoop(final="x"), _registry())  # type: ignore[arg-type]
    events = [
        e
        async for e in service.execute(
            ToolRequest(user_text="查日程", context=_ctx(), capabilities=["calendar.read"])
        )
    ]
    assert events[-1].type == "tool.failed"
    assert events[-1].error is not None
    assert events[-1].error.code == "empty_context"


async def test_tool_cancel_is_noop():
    service = ToolServiceAdapter(FakeToolLoop(), _registry())  # type: ignore[arg-type]
    await service.cancel("r1")  # 占位：不抛即可
