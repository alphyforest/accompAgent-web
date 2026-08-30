"""内置演示工具：now（获取当前本地时间）。

阶段 1 验收的一部分，同时为规格 §8 时间策略铺路：模型无"现在"概念，
日历类场景必须能取得绝对时间。
"""

from datetime import datetime
from typing import Any, Dict

from src.core.tools.spec import ToolSpec

_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


async def _now_execute(args: Dict[str, Any]) -> Dict[str, Any]:
    """返回当前本地时间（ISO 8601 带时区偏移 + 星期 + 时区）。"""
    now = datetime.now().astimezone()
    return {
        "now": now.isoformat(timespec="seconds"),
        "weekday": _WEEKDAYS[now.weekday()],
        "timezone": str(now.tzinfo),
        "utc_offset": now.strftime("%z"),
    }


def build_now_tool() -> ToolSpec:
    """构建 demo 工具 now（只读，无参数）。"""
    return ToolSpec(
        name="now",
        description=(
            "获取当前本地时间：返回 ISO 8601 带时区偏移的当前时间（如 2026-08-29T09:00:00+08:00）、"
            "星期与时区。模型没有实时时钟，需要知道'现在几点/今天几号/周几'时必须调用本工具。"
            "示例：用户问'现在几点了'，先调用本工具再回答。"
        ),
        input_schema={"type": "object", "properties": {}},
        executable=_now_execute,
        read_only=True,
    )
