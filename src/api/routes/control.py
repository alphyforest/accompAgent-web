"""状态查询、重置与记忆管理路由。"""

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import get_engine, get_events, get_mood, get_scheduler
from src.api.schemas import (
    CharacterMeta,
    CharacterResponse,
    MemoryCorrectRequest,
    MemoryItem,
    MemoryListResponse,
    ResetRequest,
    ResetResponse,
    StatusResponse,
    SummaryItem,
)
from src.core.agent.dialogue import DialogueEngine
from src.core.agent.event import EventSystem
from src.core.agent.initiative_scheduler import InitiativeScheduler
from src.core.agent.mood import MoodSystem
from src.core.memory.long_term import LongTermMemory, MemoryRecord, SummaryRecord

router = APIRouter()


def _memory_of(engine: DialogueEngine) -> LongTermMemory:
    """取长期记忆仓储，未启用时返回 503。"""
    if engine.long_term is None:
        raise HTTPException(status_code=503, detail="长期记忆未启用")
    return engine.long_term


def _to_item(record: MemoryRecord) -> MemoryItem:
    """MemoryRecord -> MemoryItem。"""
    return MemoryItem(
        id=record.id,
        category=record.category,
        key=record.key,
        value=record.value,
        importance=record.importance,
        confirmed=record.confirmed,
        source_session=record.source_session,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_summary(record: SummaryRecord) -> SummaryItem:
    """SummaryRecord -> SummaryItem。"""
    return SummaryItem(
        session_id=record.session_id,
        topics=record.topics,
        open_plans=record.open_plans,
        emotional_state=record.emotional_state,
        created_at=record.created_at,
    )


@router.get("/status", response_model=StatusResponse)
async def get_status(mood: MoodSystem = Depends(get_mood), events: EventSystem = Depends(get_events)) -> StatusResponse:
    """查询服务状态。"""
    return StatusResponse(
        mood=mood.mood,
        mood_label=mood.get_label(),
        active_chain=events.active_node,
        cooldown=events.cooldown,
    )


@router.post("/reset", response_model=ResetResponse)
async def reset(
    request: ResetRequest = ResetRequest(),  # 无 body 时默认档1（session），兼容旧前端
    engine: DialogueEngine = Depends(get_engine),
) -> ResetResponse:
    """三档清除并整体复位（改动五收拢：仅转发到 engine.reset_all，无业务逻辑）。"""
    await engine.reset_all(request.level, request.session_id)
    return ResetResponse(status="ok")


@router.get("/character", response_model=CharacterResponse)
async def get_character(engine: DialogueEngine = Depends(get_engine)) -> CharacterResponse:
    """下发角色卡关键信息（改动三：立绘映射/默认情绪，供前端查表展示）。"""
    card = engine.card
    if card is None:
        raise HTTPException(status_code=503, detail="角色卡未加载")
    return CharacterResponse(
        meta=CharacterMeta(id=card.meta.id, name=card.meta.name, description=card.meta.description),
        portrait_map=card.portrait_map,
        default_emotion=card.output_protocol.default_emotion,
    )


@router.get("/initiative")
async def get_initiative(scheduler: InitiativeScheduler = Depends(get_scheduler)) -> List[str]:
    """取出积压的主动发言（前端轮询展示，改动四·第二步）。"""
    return await scheduler.collect()


@router.get("/memory", response_model=MemoryListResponse)
async def list_memory(engine: DialogueEngine = Depends(get_engine)) -> MemoryListResponse:
    """列出全部记忆（按 category 分组）。"""
    memory = _memory_of(engine)
    rows = await memory.list_memory(engine.user_id)
    groups: Dict[str, List[MemoryItem]] = {}
    for row in rows:
        groups.setdefault(row.category, []).append(_to_item(row))
    return MemoryListResponse(user_id=engine.user_id, groups=groups)


@router.delete("/memory/{memory_id}")
async def delete_memory(memory_id: int, engine: DialogueEngine = Depends(get_engine)) -> Dict[str, str]:
    """单条删除记忆（部分清除）。"""
    memory = _memory_of(engine)
    deleted = await memory.delete_memory(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"status": "ok"}


@router.post("/memory/{memory_id}/correct", response_model=MemoryItem)
async def correct_memory(
    memory_id: int, request: MemoryCorrectRequest, engine: DialogueEngine = Depends(get_engine)
) -> MemoryItem:
    """纠正记忆：更新 value 并标记 confirmed=1。"""
    memory = _memory_of(engine)
    record = await memory.correct_memory(memory_id, request.value)
    if record is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return _to_item(record)


@router.get("/summaries", response_model=List[SummaryItem])
async def list_summaries(engine: DialogueEngine = Depends(get_engine)) -> List[SummaryItem]:
    """列出历史会话摘要（新会话在前）。"""
    memory = _memory_of(engine)
    summaries = await memory.list_summaries()
    return [_to_summary(record) for record in summaries]
