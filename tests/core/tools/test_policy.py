"""ToolPolicy 测试（SPEC-030 §6 + ADR-004 Agenda 特例）。"""

from typing import Any, Dict

from src.core.tools.policy import ToolPolicy
from src.core.tools.spec import ToolSpec


async def _ok(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True}


def _spec(
    source_id: str = "unknown",
    read_only: bool = False,
    confirmation_policy: str = "never",
    risk_level: str = "low",
    disabled: bool = False,
) -> ToolSpec:
    return ToolSpec(
        name="demo_tool",
        description="演示工具。",
        input_schema={"type": "object"},
        executable=_ok,
        source_id=source_id,
        read_only=read_only,
        confirmation_policy=confirmation_policy,
        risk_level=risk_level,
        disabled=disabled,
    )


def test_read_only_allowed():
    decision = ToolPolicy().decide(_spec(read_only=True))
    assert decision.allow is True
    assert decision.require_confirmation is False


def test_disabled_denied():
    decision = ToolPolicy().decide(_spec(disabled=True))
    assert decision.allow is False


def test_agenda_write_auto_execute():
    """ADR-004：受审查 Agenda 来源写工具可自动执行。"""
    decision = ToolPolicy().decide(_spec(source_id="agenda", confirmation_policy="conditional"))
    assert decision.allow is True
    assert decision.require_confirmation is False
    assert decision.reason == "auto_allowed"


def test_condidtional_write_requires_confirmation():
    decision = ToolPolicy().decide(_spec(source_id="other", confirmation_policy="conditional"))
    assert decision.allow is True
    assert decision.require_confirmation is True


def test_high_risk_requires_confirmation():
    decision = ToolPolicy().decide(_spec(source_id="other", confirmation_policy="never", risk_level="high"))
    assert decision.require_confirmation is True
