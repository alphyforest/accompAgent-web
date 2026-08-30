"""主动说话调度器测试：冷启动/未交互时不误触发（问题 1 修复回归）。"""

import time
from typing import List, Tuple

import pytest
from src.core.agent.initiative_scheduler import InitiativeScheduler
from src.core.agent.triggers import InitiativeTriggerMatcher
from src.core.character.card import InitiativeTrigger, TriggerCondition


class _StubEngine:
    """最小引擎桩：仅实现调度器依赖的 trigger_context / mood / generate_initiative。"""

    def __init__(self) -> None:
        self.mood = type("Mood", (), {"mood": 0})()
        self._activity: dict = {}
        self.calls: List[Tuple[str, str]] = []

    def last_activity(self, session_id: str) -> float:
        # 与 DialogueEngine.last_activity 修复后一致：无记录视为刚活跃
        return self._activity.get(session_id, time.time())

    def trigger_context(self, session_id: str) -> dict:
        silence = max(0.0, time.time() - self.last_activity(session_id))
        return {"mood": self.mood.mood, "silence_seconds": silence, "message_count": 0}

    async def generate_initiative(self, trigger: InitiativeTrigger, session_id: str) -> str:
        self.calls.append((trigger.id, session_id))
        return "[[EMOTION:idle]]你好"


def _comfort_matcher() -> InitiativeTriggerMatcher:
    """mood=0 即命中的安慰触发器（对应冷启动场景）。"""
    return InitiativeTriggerMatcher(
        [
            InitiativeTrigger(
                id="comfort",
                condition=TriggerCondition(mood_min=-100, mood_max=10, expression=None),
                probability=1.0,
                cooldown_minutes=0.0,
                prompt="安慰",
                emotion="sad",
            )
        ]
    )


@pytest.mark.asyncio
async def test_cold_start_no_fire():
    """从未交互（无活动记录）时视为刚活跃，调度器不误触发主动发言。"""
    engine = _StubEngine()
    scheduler = InitiativeScheduler(engine=engine, matcher=_comfort_matcher(), min_silence_seconds=30.0)
    await scheduler._tick()
    assert await scheduler.collect() == []
    assert engine.calls == []


@pytest.mark.asyncio
async def test_fires_after_engagement_and_silence():
    """用户互动过并静默超过阈值后才触发。"""
    engine = _StubEngine()
    engine._activity["default"] = time.time() - 100  # 已静默 100s
    scheduler = InitiativeScheduler(engine=engine, matcher=_comfort_matcher(), min_silence_seconds=30.0)
    await scheduler._tick()
    items = await scheduler.collect()
    assert len(items) == 1
    assert engine.calls and engine.calls[0] == ("comfort", "default")
