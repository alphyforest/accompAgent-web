"""查询类应用服务（PLAN-010 R1）：mood / character / initiative。

只依赖 Port，不直接接触 DialogueEngine / EventSystem / Scheduler 实现
（STD-010 §2；R1 先建骨架与 Fake 测试，API 切换在后续小阶段完成）。
"""

from typing import List

from src.application.contracts import (
    CharacterPort,
    CharacterView,
    DialogueStatePort,
    InitiativeSourcePort,
    MoodSnapshot,
)


class DialogueQueryService:
    """气氛查询：GET /api/mood 的应用用例。"""

    def __init__(self, state: DialogueStatePort) -> None:
        self._state = state

    def get_mood(self) -> MoodSnapshot:
        return self._state.get_mood()


class CharacterQueryService:
    """角色查询：GET /api/character 的应用用例。"""

    def __init__(self, character: CharacterPort) -> None:
        self._character = character

    def get_character(self) -> CharacterView:
        return self._character.get()


class InitiativeQueryService:
    """主动发言查询：GET /api/initiative 的应用用例。"""

    def __init__(self, source: InitiativeSourcePort) -> None:
        self._source = source

    async def collect(self) -> List[str]:
        return await self._source.collect()
