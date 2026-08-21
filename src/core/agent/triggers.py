"""主动说话触发器：条件匹配 + 概率 + 冷却（内存态，重启清零）。

触发器列表来自角色卡 ``character.json`` 的 ``initiative_triggers``，
条件为"气氛区间 + 可选表达式"，配合概率与冷却记录（doc/04-专项设计 §改动四）。
"""

import random
import time
from typing import Any, Dict, List, Optional

from src.core.character.card import InitiativeTrigger
from src.utils.logger import logger

# 触发器表达式仅允许的变量白名单（rules.md §15.2）；新增变量须先在 doc/ 登记并同步此处
EXPRESSION_ALLOWED_VARS = ("mood", "silence_seconds", "message_count")


class InitiativeTriggerMatcher:
    """按条件匹配触发器，并维护冷却记录（内存）。"""

    def __init__(self, triggers: List[InitiativeTrigger]):
        self.triggers = triggers
        self._last_trigger: Dict[str, float] = {}

    @property
    def empty(self) -> bool:
        """是否无可用触发器。"""
        return not self.triggers

    def check(self, context: Dict[str, Any]) -> Optional[InitiativeTrigger]:
        """按条件/概率/冷却匹配，返回应触发的触发器（无则 None）。

        ``context`` 提供给条件与表达式判定的局部变量（如 mood / silence_seconds）。
        条件满足、未在冷却期、且概率命中则记录冷却并返回该触发器。
        """
        now = time.time()
        for trigger in self.triggers:
            if not self._condition_satisfied(trigger, context):
                continue
            last = self._last_trigger.get(trigger.id, 0.0)
            if trigger.cooldown_minutes > 0 and now - last < trigger.cooldown_minutes * 60:
                continue
            if random.random() > trigger.probability:
                continue
            self._last_trigger[trigger.id] = now
            return trigger
        return None

    def reset_cooldowns(self) -> None:
        """清空冷却记录（重置状态时调用）。"""
        self._last_trigger.clear()

    def _condition_satisfied(self, trigger: InitiativeTrigger, context: Dict[str, Any]) -> bool:
        """判定触发器条件：气氛区间 + 可选表达式。"""
        condition = trigger.condition
        mood = int(context.get("mood", 0))
        if condition.mood_min is not None and mood < condition.mood_min:
            return False
        if condition.mood_max is not None and mood > condition.mood_max:
            return False
        if condition.expression:
            # 简单 eval（蓝图允许）：仅暴露白名单变量（rules.md §15.2），未登记变量缺失即求值失败视为不满足
            locals_ = {
                "mood": int(context.get("mood", 0)),
                "silence_seconds": float(context.get("silence_seconds", 0.0)),
                "message_count": int(context.get("message_count", 0)),
            }
            try:
                if not eval(condition.expression, {"__builtins__": {}}, locals_):  # noqa: S307 - 白名单变量，见 rules.md §15.2
                    return False
            except Exception as exc:  # noqa: BLE001 - 表达式异常视为不满足
                logger.warning("触发器表达式求值失败 trigger={} err={}", trigger.id, exc)
                return False
        return True
