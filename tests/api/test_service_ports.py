"""R1 收尾：api/dependencies.py 的 Port 适配器单测（Fake 引擎/记忆/调度器，不连真实实现）。"""

from typing import List, Optional

import pytest
from src.api.dependencies import (
    ApplicationDependencyUnavailable,
    EngineCharacterPort,
    EngineDialogueStatePort,
    EngineMemoryPort,
    SchedulerInitiativeSourcePort,
)
from src.core.agent.mood import MoodSystem
from src.core.memory.long_term import MemoryRecord, SummaryRecord


class FakeMeta:
    id = "elysia"
    name = "爱莉希雅"
    description = "测试"


class FakeProtocol:
    default_emotion = "idle"


class FakeInitState:
    mood = 0
    emotion = "idle"


class FakeCard:
    meta = FakeMeta()
    portrait_map = {"idle": "idle.png", "happy": "happy.png"}
    output_protocol = FakeProtocol()
    init_state = FakeInitState()


class FakeMemoryBackend:
    """LongTermMemory 接口的 Fake（适配器用到的三个方法）。"""

    def __init__(
        self,
        rows: Optional[List[MemoryRecord]] = None,
        summaries: Optional[List[SummaryRecord]] = None,
    ) -> None:
        self.rows = rows or []
        self.summaries = summaries or []
        self.deleted: List[int] = []
        self.corrected: List[tuple[int, str]] = []

    async def list_memory(self, user_id: str) -> List[MemoryRecord]:
        return self.rows

    async def delete_memory(self, memory_id: int) -> bool:
        self.deleted.append(memory_id)
        return True

    async def correct_memory(self, memory_id: int, value: str) -> Optional[MemoryRecord]:
        self.corrected.append((memory_id, value))
        if memory_id == 404:
            return None
        return MemoryRecord(
            id=memory_id,
            user_id="default",
            category="fact",
            key="city",
            value=value,
            importance=5,
            confirmed=1,
            source_session=None,
            created_at="t0",
            updated_at="t1",
        )

    async def list_summaries(self) -> List[SummaryRecord]:
        return self.summaries


class FakeEngine:
    """DialogueEngine 接口的 Fake（适配器只读 card/mood/long_term/user_id + reset_all）。"""

    def __init__(self, long_term, card=None, mood=None) -> None:
        self.card = card
        self.long_term = long_term
        self.mood = mood or MoodSystem()
        self.user_id = "default"
        self.resets: List[tuple[str, str]] = []

    async def reset_all(self, level: str, session_id: str) -> None:
        self.resets.append((level, session_id))


def _record(memory_id: int = 1) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        user_id="default",
        category="fact",
        key="city",
        value="上海",
        importance=6,
        confirmed=0,
        source_session="s1",
        created_at="t0",
        updated_at="t1",
    )


def test_dialogue_state_port_maps_mood():
    mood = MoodSystem(initial_mood=15)
    snapshot = EngineDialogueStatePort(mood).get_mood()
    assert snapshot.mood == 15
    assert snapshot.label == mood.get_label()


def test_character_port_maps_card_including_init_state():
    port = EngineCharacterPort(FakeEngine(long_term=None, card=FakeCard()))
    view = port.get()
    assert view.character_id == "elysia"
    assert view.portrait_map == {"idle": "idle.png", "happy": "happy.png"}
    assert view.default_emotion == "idle"
    assert view.init_mood == 0
    assert view.init_emotion == "idle"


def test_character_port_raises_when_card_missing():
    port = EngineCharacterPort(FakeEngine(long_term=None, card=None))
    with pytest.raises(ApplicationDependencyUnavailable):
        port.get()


async def test_memory_port_groups_and_maps_records():
    backend = FakeMemoryBackend(rows=[_record()])
    port = EngineMemoryPort(FakeEngine(long_term=backend))
    result = await port.list_grouped()
    assert result.user_id == "default"
    assert "fact" in result.groups
    item = result.groups["fact"][0]
    assert item.key == "city"
    assert item.value == "上海"
    assert item.source_session == "s1"
    assert item.created_at == "t0"
    assert item.updated_at == "t1"


async def test_memory_port_delete_and_correct():
    backend = FakeMemoryBackend(rows=[_record()])
    port = EngineMemoryPort(FakeEngine(long_term=backend))
    assert await port.delete(7) is True
    assert backend.deleted == [7]
    view = await port.correct(5, "北京")
    assert view is not None
    assert view.value == "北京"
    assert view.confirmed == 1
    assert await port.correct(404, "x") is None


async def test_memory_port_lists_summaries():
    summary = SummaryRecord(
        session_id="s1",
        topics=["数学"],
        open_plans=["公园"],
        emotional_state="有点累",
        created_at="t",
    )
    port = EngineMemoryPort(FakeEngine(long_term=FakeMemoryBackend(summaries=[summary])))
    views = await port.list_summaries()
    assert views[0].session_id == "s1"
    assert views[0].emotional_state == "有点累"


async def test_memory_port_unavailable_raises():
    port = EngineMemoryPort(FakeEngine(long_term=None))
    with pytest.raises(ApplicationDependencyUnavailable):
        await port.list_grouped()


async def test_memory_port_reset_delegates_engine():
    engine = FakeEngine(long_term=FakeMemoryBackend())
    port = EngineMemoryPort(engine)
    await port.reset("all", "s9")
    assert engine.resets == [("all", "s9")]


async def test_initiative_source_port_collects():
    class FakeScheduler:
        async def collect(self) -> List[str]:
            return ["msg1"]

    source = SchedulerInitiativeSourcePort(FakeScheduler())  # type: ignore[arg-type]
    assert await source.collect() == ["msg1"]
