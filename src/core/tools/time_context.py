"""时间上下文（规格 §8）：system prompt 动态注入当前时间。

LLM 无"现在"概念，工具可用时每次组装 prompt 动态生成，
格式一律 ISO 8601 带时区偏移（如 2026-08-29T09:00:00+08:00）。
"""

from datetime import datetime
from typing import Optional

_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def current_time_context(now: Optional[datetime] = None) -> str:
    """生成当前时间上下文文本（ISO 8601 带偏移 + 星期 + 时区）。

    now 参数仅用于测试注入固定时间；生产缺省取本地当前时间。
    """
    current = (now or datetime.now()).astimezone()
    weekday = _WEEKDAYS[current.weekday()]
    tz_name = current.tzname() or "unknown"
    offset = current.strftime("%z") or "+0000"
    return (
        f"当前时间: {current.isoformat(timespec='seconds')}"
        f"（{weekday}，时区 {tz_name} UTC{offset}）"
    )
