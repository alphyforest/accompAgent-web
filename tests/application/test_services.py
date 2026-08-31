"""查询/控制应用服务骨架测试：只验证委托 Port（Fake）。"""

from typing import List, Optional

from src.application.contracts import (
    CharacterView,
    MemoryItemView,
    MemoryListResult,
    MoodSnapshot,
    SummaryView,
)
from src.application.control_service import MemoryApplicationService, ResetApplicationService
from src.application.query_service import CharacterQueryService, DialogueQueryService, InitiativeQueryService


class FakeDialogueState:
    def get_mood(self) -> MoodSnapshot:
        return MoodSnapshot(mood=15, label="happy")


class FakeCharacter:
    def get(self) -> CharacterView:
        return CharacterView(
            character_id="elysia",
            name="爱莉希雅",
            description="测试角色",
            portrait_map={"idle": "idle.png"},
            default_emotion="idle",
        )


class FakeInitiative:
    async def collect(self) -> List[str]:
        return ["[主动] 你好呀"]


class FakeMemory:
    def __init__(self) -> None:
        self.deleted: List[int] = []
        self.resets: List[tuple[str, str]] = []

    async def list_grouped(self) -> MemoryListResult:
        return MemoryListResult(
            user_id="default",
            groups={
                "profile": [
                    MemoryItemView(id=1, category="profile", key="name", value="A", importance=5, confirmed=0)
                ]
            },
        )

    async def delete(self, memory_id: int) -> bool:
        self.deleted.append(memory_id)
        return True

    async def correct(self, memory_id: int, value: str) -> Optional[MemoryItemView]:
        return MemoryItemView(id=memory_id, category="profile", key="name", value=value, importance=5, confirmed=1)

    async def list_summaries(self) -> List[SummaryView]:
        return []

    async def reset(self, level: str, session_id: str) -> None:
        self.resets.append((level, session_id))


def test_dialogue_query_service_delegates_mood():
    service = DialogueQueryService(FakeDialogueState())
    snapshot = service.get_mood()
    assert snapshot.mood == 15
    assert snapshot.label == "happy"


def test_character_query_service_delegates_character():
    service = CharacterQueryService(FakeCharacter())
    view = service.get_character()
    assert view.character_id == "elysia"
    assert view.portrait_map == {"idle": "idle.png"}


async def test_initiative_query_service_delegates_collect():
    service = InitiativeQueryService(FakeInitiative())
    assert await service.collect() == ["[主动] 你好呀"]


async def test_memory_service_delegates_crud():
    memory = FakeMemory()
    service = MemoryApplicationService(memory)
    result = await service.list_grouped()
    assert result.user_id == "default"
    assert result.groups["profile"][0].value == "A"
    assert await service.delete(7) is True
    assert memory.deleted == [7]
    corrected = await service.correct(3, "B")
    assert corrected is not None and corrected.confirmed == 1
    assert await service.list_summaries() == []


async def test_reset_service_delegates_level_and_session():
    memory = FakeMemory()
    service = ResetApplicationService(memory)
    await service.reset("history", "s9")
    assert memory.resets == [("history", "s9")]
