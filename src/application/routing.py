"""CapabilityRouter：确定性路由 v1（SPEC-010 §3），不引入 LLM/Planner。

规则只选择能力类别（如 calendar），不直接构造最终工具参数；
具体工具和参数选择留给 ToolService（SPEC-010 §3.1）。
"""

from typing import List

from src.application.contracts import CapabilitySnapshot, RequestContext, RouteDecision

# 确定性工具规则（SPEC-010 §3.1）：日历类能力的关键词（reason_code 稳定为 matched_tool_capability）
_CALENDAR_KEYWORDS: List[str] = ["日程", "课表", "会议", "安排", "预约", "提醒", "排期", "请假", "出差"]


class CapabilityRouter:
    """第一版路由：显式模式 / 活跃娱乐会话 / 确定性工具关键词 / 默认对话。"""

    def route(
        self,
        request: RequestContext,
        user_text: str,
        capabilities: CapabilitySnapshot,
    ) -> RouteDecision:
        # 1. 活跃娱乐会话优先（除非显式 companion）
        if capabilities.active_entertainment and request.requested_mode != "companion":
            return RouteDecision(
                target="entertainment",
                reason_code="active_entertainment_session",
                confidence=1.0,
            )

        # 2. 显式模式
        if request.requested_mode == "companion":
            return RouteDecision(
                target="dialogue",
                reason_code="explicit_companion_mode",
                confidence=1.0,
            )
        if request.requested_mode == "office":
            return RouteDecision(
                target="tool" if capabilities.available_capabilities else "dialogue",
                reason_code="explicit_office_mode",
                confidence=1.0,
                selected_capabilities=capabilities.available_capabilities,
            )

        # 3. 确定性工具规则：日历类关键词 + 可用能力匹配
        calendar_caps: List[str] = [cap for cap in capabilities.available_capabilities if cap.startswith("calendar")]
        if calendar_caps and any(keyword in user_text for keyword in _CALENDAR_KEYWORDS):
            return RouteDecision(
                target="tool",
                reason_code="matched_tool_capability",
                confidence=0.9,
                selected_capabilities=calendar_caps,
            )

        # 4. 默认对话（低置信度处理：不自动执行写工具，SPEC-010 §3.2）
        return RouteDecision(
            target="dialogue",
            reason_code="default_dialogue",
            confidence=0.6,
        )
