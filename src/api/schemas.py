"""API 请求/响应模型。"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """对话请求。"""

    input: str = Field(..., min_length=1, max_length=2000)
    history: Optional[List[Dict[str, str]]] = Field(default_factory=list)
    session_id: str = Field(default="default", max_length=64)


class EventTriggerRequest(BaseModel):
    """事件触发请求。"""

    keyword: str = Field(..., min_length=1, max_length=64)


class MoodResponse(BaseModel):
    """气氛值响应。"""

    mood: int
    label: str


class StatusResponse(BaseModel):
    """状态响应。"""

    mood: int
    mood_label: str
    active_chain: Optional[str]
    cooldown: int


class ResetResponse(BaseModel):
    """重置响应。"""

    status: str
