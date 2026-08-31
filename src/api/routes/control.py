"""状态查询、重置与记忆管理路由（PLAN-010 R1：控制面全部经应用层 Application Service）。"""

from datetime import UTC, datetime
from typing import Dict, List
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import (
    ApplicationDependencyUnavailable,
    get_character_query_service,
    get_initiative_query_service,
    get_memory_application_service,
    get_mood,
    get_reset_application_service,
)
from src.api.schemas import (
    CharacterInitState,
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
from src.application.contracts import MemoryItemView, SummaryView, UIEvent
from src.application.control_service import MemoryApplicationService, ResetApplicationService
from src.application.query_service import CharacterQueryService, InitiativeQueryService
from src.core.agent.mood import MoodSystem

router = APIRouter()


def _memory_item(view: MemoryItemView) -> MemoryItem:
    """视图 -> API 模型（字段已逐字段对齐，直接映射）。"""
    return MemoryItem.model_validate(view.model_dump())


def _summary_item(view: SummaryView) -> SummaryItem:
    """视图 -> API 模型。"""
    return SummaryItem.model_validate(view.model_dump())


@router.get("/status", response_model=StatusResponse)
async def get_status(mood: MoodSystem = Depends(get_mood)) -> StatusResponse:
    """查询服务状态（R6：仅保留气氛快照，事件系统字段已退场）。"""
    return StatusResponse(mood=mood.mood, mood_label=mood.get_label())


@router.post("/reset", response_model=ResetResponse)
async def reset(
    request: ResetRequest = ResetRequest(),  # 无 body 时默认档1（session），兼容旧前端
    service: ResetApplicationService = Depends(get_reset_application_service),
) -> ResetResponse:
    """三档清除并整体复位（应用层用例）。"""
    await service.reset(request.level, request.session_id)
    return ResetResponse(status="ok")


@router.get("/character", response_model=CharacterResponse)
async def get_character(service: CharacterQueryService = Depends(get_character_query_service)) -> CharacterResponse:
    """下发角色卡关键信息（应用层查询服务）。"""
    try:
        view = service.get_character()
    except ApplicationDependencyUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return CharacterResponse(
        meta=CharacterMeta(id=view.character_id, name=view.name, description=view.description),
        portrait_map=view.portrait_map,
        default_emotion=view.default_emotion,
        init_state=CharacterInitState(mood=view.init_mood, emotion=view.init_emotion),
    )


@router.get("/initiative", response_model=List[UIEvent])
async def get_initiative(service: InitiativeQueryService = Depends(get_initiative_query_service)) -> List[UIEvent]:
    """取出积压的主动发言（R4：轮询响应转换为 UIEvent 数组，SPEC-050 §8）。

    客户端用 event_id 去重；message_source=initiative。
    """
    rows = await service.collect()
    now = datetime.now(UTC)
    events: List[UIEvent] = []
    for i, text in enumerate(rows, start=1):
        events.append(
            UIEvent(
                event_id=uuid4().hex,
                request_id="initiative",
                trace_id="initiative",
                session_id="default",
                type="message.completed",
                source="initiative",
                timestamp=now,
                sequence=i,
                payload={
                    "message_id": f"msg_init_{uuid4().hex[:8]}",
                    "role": "assistant",
                    "message_source": "initiative",
                    "text": text,
                },
            )
        )
    return events


@router.get("/memory", response_model=MemoryListResponse)
async def list_memory(
    service: MemoryApplicationService = Depends(get_memory_application_service),
) -> MemoryListResponse:
    """列出全部记忆（按 category 分组；应用层用例）。"""
    try:
        result = await service.list_grouped()
    except ApplicationDependencyUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    groups: Dict[str, List[MemoryItem]] = {
        category: [_memory_item(item) for item in items] for category, items in result.groups.items()
    }
    return MemoryListResponse(user_id=result.user_id, groups=groups)


@router.delete("/memory/{memory_id}")
async def delete_memory(
    memory_id: int,
    service: MemoryApplicationService = Depends(get_memory_application_service),
) -> Dict[str, str]:
    """单条删除记忆（应用层用例）。"""
    try:
        deleted = await service.delete(memory_id)
    except ApplicationDependencyUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"status": "ok"}


@router.post("/memory/{memory_id}/correct", response_model=MemoryItem)
async def correct_memory(
    memory_id: int,
    request: MemoryCorrectRequest,
    service: MemoryApplicationService = Depends(get_memory_application_service),
) -> MemoryItem:
    """纠正记忆：更新 value 并标记 confirmed=1（应用层用例）。"""
    try:
        view = await service.correct(memory_id, request.value)
    except ApplicationDependencyUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if view is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return _memory_item(view)


@router.get("/summaries", response_model=List[SummaryItem])
async def list_summaries(
    service: MemoryApplicationService = Depends(get_memory_application_service),
) -> List[SummaryItem]:
    """列出历史会话摘要（新会话在前；应用层用例）。"""
    try:
        views = await service.list_summaries()
    except ApplicationDependencyUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [_summary_item(view) for view in views]
