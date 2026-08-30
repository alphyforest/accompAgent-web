"""主动触发器匹配器测试：条件、概率、冷却、表达式。"""

from typing import Optional

from src.core.agent.triggers import InitiativeTriggerMatcher
from src.core.character.card import InitiativeTrigger, TriggerCondition


def _trigger(
    tid: str = "t",
    mood_min: Optional[int] = None,
    mood_max: Optional[int] = None,
    expr: str = "",
    prob: float = 1.0,
    cooldown: float = 0.0,
    emotion: str = "",
) -> InitiativeTrigger:
    return InitiativeTrigger(
        id=tid,
        condition=TriggerCondition(mood_min=mood_min, mood_max=mood_max, expression=expr or None),
        probability=prob,
        cooldown_minutes=cooldown,
        prompt="提示",
        emotion=emotion,
    )


def test_empty_matcher():
    assert InitiativeTriggerMatcher([]).check({"mood": 0}) is None


def test_mood_range_match():
    m = InitiativeTriggerMatcher([_trigger(mood_min=50, mood_max=100)])
    assert m.check({"mood": 80}) is not None


def test_mood_range_out_of_range():
    m = InitiativeTriggerMatcher([_trigger(mood_min=50, mood_max=100)])
    assert m.check({"mood": 10}) is None


def test_probability_miss(monkeypatch):
    monkeypatch.setattr("src.core.agent.triggers.random.random", lambda: 1.0)
    m = InitiativeTriggerMatcher([_trigger(prob=0.5)])
    assert m.check({"mood": 0}) is None


def test_cooldown_blocks_and_reset(monkeypatch):
    monkeypatch.setattr("src.core.agent.triggers.random.random", lambda: 0.0)
    m = InitiativeTriggerMatcher([_trigger(cooldown=10)])
    assert m.check({"mood": 0}) is not None  # 首次触发
    assert m.check({"mood": 0}) is None  # 冷却期屏蔽
    m.reset_cooldowns()
    assert m.check({"mood": 0}) is not None  # 复位后可再次触发


def test_expression_eval():
    m = InitiativeTriggerMatcher([_trigger(expr="mood > 50 and silence_seconds > 5")])
    assert m.check({"mood": 60, "silence_seconds": 10}) is not None
    assert m.check({"mood": 40, "silence_seconds": 10}) is None
    assert m.check({"mood": 60, "silence_seconds": 1}) is None


def test_expression_error_is_false():
    m = InitiativeTriggerMatcher([_trigger(expr="mood + 'x'")])
    assert m.check({"mood": 0}) is None


def test_expression_whitelist_only():
    """仅白名单变量（rules.md §15.2）：非白名单变量求值失败视为不满足，不 crash。"""
    m = InitiativeTriggerMatcher([_trigger(expr="mood == 0")])
    assert m.check({"mood": 0}) is not None
    # 非白名单变量 -> NameError -> 视为不满足
    m2 = InitiativeTriggerMatcher([_trigger(expr="evil_var > 0")])
    assert m2.check({"mood": 0}) is None
    # 白名单变量未提供时默认 0
    m3 = InitiativeTriggerMatcher([_trigger(expr="message_count == 0")])
    assert m3.check({}) is not None
