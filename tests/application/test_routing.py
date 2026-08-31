"""CapabilityRouter 路由算法测试（SPEC-010 §3 测试矩阵子集）。"""

from src.application.contracts import CapabilitySnapshot, RequestContext
from src.application.routing import CapabilityRouter

router = CapabilityRouter()


def _ctx(mode: str = "auto") -> RequestContext:
    return RequestContext(
        request_id="r1",
        trace_id="t1",
        session_id="s1",
        character_id="elysia",
        requested_mode=mode,
    )


def _caps(capabilities=None, active_entertainment: bool = False) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        available_capabilities=capabilities or [],
        active_entertainment=active_entertainment,
    )


def test_default_greeting_without_tools_routes_dialogue():
    decision = router.route(_ctx(), "你好", _caps())
    assert decision.target == "dialogue"
    assert decision.reason_code == "default_dialogue"


def test_tools_present_but_greeting_still_dialogue():
    decision = router.route(_ctx(), "你好", _caps(["calendar.read"]))
    assert decision.target == "dialogue"


def test_calendar_keyword_routes_tool():
    decision = router.route(_ctx(), "帮我查一下明天的日程", _caps(["calendar.read", "calendar.write"]))
    assert decision.target == "tool"
    assert decision.reason_code == "matched_tool_capability"
    assert decision.selected_capabilities == ["calendar.read", "calendar.write"]


def test_calendar_keyword_without_capability_falls_back_dialogue():
    decision = router.route(_ctx(), "帮我查一下明天的日程", _caps())
    assert decision.target == "dialogue"


def test_explicit_companion_mode_wins_over_tool_keyword():
    decision = router.route(_ctx("companion"), "帮我查一下明天的日程", _caps(["calendar.read"]))
    assert decision.target == "dialogue"
    assert decision.reason_code == "explicit_companion_mode"


def test_explicit_office_mode_routes_tool_when_available():
    decision = router.route(_ctx("office"), "你好", _caps(["calendar.read"]))
    assert decision.target == "tool"
    assert decision.reason_code == "explicit_office_mode"


def test_explicit_office_without_tools_routes_dialogue():
    decision = router.route(_ctx("office"), "你好", _caps())
    assert decision.target == "dialogue"


def test_active_entertainment_has_priority():
    decision = router.route(_ctx(), "你好", _caps(active_entertainment=True))
    assert decision.target == "entertainment"
    assert decision.reason_code == "active_entertainment_session"


def test_companion_mode_bypasses_entertainment():
    decision = router.route(_ctx("companion"), "你好", _caps(active_entertainment=True))
    assert decision.target == "dialogue"
