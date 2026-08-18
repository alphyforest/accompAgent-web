"""状态查询与重置路由。"""

from fastapi import APIRouter, Depends

from src.api.dependencies import get_engine, get_events, get_mood
from src.api.schemas import ResetResponse, StatusResponse
from src.core.agent.dialogue import DialogueEngine
from src.core.agent.event import EventSystem
from src.core.agent.mood import MoodSystem

router = APIRouter()


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
    engine: DialogueEngine = Depends(get_engine),
    mood: MoodSystem = Depends(get_mood),
    events: EventSystem = Depends(get_events),
) -> ResetResponse:
    """重置气氛值与事件状态。"""
    mood.reset()
    events.reset()
    return ResetResponse(status="ok")
