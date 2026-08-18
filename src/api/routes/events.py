"""事件触发路由。"""

from typing import Any, Dict

from fastapi import APIRouter, Depends

from src.api.dependencies import get_events
from src.api.schemas import EventTriggerRequest
from src.core.agent.event import EventSystem

router = APIRouter()


@router.post("/event/trigger")
async def trigger_event(request: EventTriggerRequest, events: EventSystem = Depends(get_events)) -> Dict[str, Any]:
    """强制触发事件（按节点 id）。"""
    node = events.force_trigger(request.keyword)
    if node is None:
        return {"detail": "未找到匹配的事件"}
    return {
        "node_id": node.id,
        "name": node.name,
        "emotion": node.emotion,
        "prompt": node.prompt,
    }
