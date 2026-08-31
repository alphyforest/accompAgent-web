"""ToolPolicy（SPEC-030 §6）：执行前策略检查（R5 v1）。

- 只读/disabled 直接判定；确认策略与风险等级驱动 confirmation
- Agenda v1 特例（ADR-004）：本机受审查 Agenda 来源写操作可自动执行；
  其他来源不继承该特例（STD-010 §9 关闭 Tool 不影响对话）
"""

from dataclasses import dataclass
from typing import Optional

from src.core.tools.spec import ToolSpec

# ADR-004：可自动执行的受审查来源集合（本地 Agenda）
AUTO_EXECUTE_SOURCES = ("agenda",)


@dataclass(frozen=True)
class PolicyDecision:
    """策略判定结果。"""

    allow: bool
    require_confirmation: bool = False
    reason: str = ""


class ToolPolicy:
    """工具执行策略：allow / deny / require_confirmation。"""

    def decide(self, spec: ToolSpec) -> PolicyDecision:
        if spec.disabled:
            return PolicyDecision(allow=False, reason="tool_disabled")
        if spec.read_only:
            return PolicyDecision(allow=True, reason="read_only")
        if spec.source_id in AUTO_EXECUTE_SOURCES:
            # ADR-004 特例：受审查 Agenda 来源可自动执行（写前查重等约束由工具语义保证）
            return PolicyDecision(allow=True, reason="auto_allowed")
        if spec.risk_level == "high":
            # 本地策略覆盖外部来源的宽松声明：高风险一律先确认
            return PolicyDecision(allow=True, require_confirmation=True, reason="high_risk_confirmation")
        if spec.confirmation_policy in {"always", "conditional"}:
            # v1：conditional 视为需确认（R6+ 提供参数级条件判定）
            return PolicyDecision(allow=True, require_confirmation=True, reason="confirmation_required")
        return PolicyDecision(allow=True, reason="allowed")


def first_confirmation(specs: "list[ToolSpec]") -> Optional[ToolSpec]:
    """返回首个需要用户确认的工具（无则 None）。"""
    policy = ToolPolicy()
    for spec in specs:
        decision = policy.decide(spec)
        if not decision.allow or decision.require_confirmation:
            return spec
    return None
