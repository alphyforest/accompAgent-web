"""控制类应用服务（PLAN-010 R1）：memory / reset。

同样只依赖 Port；R1 提供骨架与 Fake 测试，路由切换在后续小阶段。
"""

from typing import List, Optional

from src.application.contracts import MemoryItemView, MemoryListResult, MemoryPort, SummaryView


class MemoryApplicationService:
    """记忆管理：GET /api/memory、DELETE /api/memory/{id}、POST /api/memory/{id}/correct、GET /api/summaries。"""

    def __init__(self, memory: MemoryPort) -> None:
        self._memory = memory

    async def list_grouped(self) -> MemoryListResult:
        return await self._memory.list_grouped()

    async def delete(self, memory_id: int) -> bool:
        return await self._memory.delete(memory_id)

    async def correct(self, memory_id: int, value: str) -> Optional[MemoryItemView]:
        return await self._memory.correct(memory_id, value)

    async def list_summaries(self) -> List[SummaryView]:
        return await self._memory.list_summaries()


class ResetApplicationService:
    """三档重置：POST /api/reset。"""

    def __init__(self, memory: MemoryPort) -> None:
        self._memory = memory

    async def reset(self, level: str, session_id: str) -> None:
        await self._memory.reset(level, session_id)
