"""API 请求/响应模型。"""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """对话请求（R6：移除遗留 history 字段，前端不传、引擎不读）。"""

    input: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(default="default", max_length=64)


class EventTriggerRequest(BaseModel):
    """事件触发请求。"""

    keyword: str = Field(..., min_length=1, max_length=64)


class MoodResponse(BaseModel):
    """气氛值响应。"""

    mood: int
    label: str


class StatusResponse(BaseModel):
    """状态响应（R6：移除 EventSystem 专属字段 active_chain/cooldown）。"""

    mood: int
    mood_label: str


class ResetResponse(BaseModel):
    """重置响应。"""

    status: str


class ResetRequest(BaseModel):
    """三档清除请求（第二阶段）。"""

    level: Literal["session", "history", "all"] = "session"
    session_id: str = Field(default="default", max_length=64)


class MemoryItem(BaseModel):
    """单条用户记忆。"""

    id: int
    category: str
    key: str
    value: str
    importance: int
    confirmed: int
    source_session: Optional[str]
    created_at: str
    updated_at: str


class MemoryListResponse(BaseModel):
    """记忆列表（按 category 分组）。"""

    user_id: str
    groups: Dict[str, List[MemoryItem]]


class MemoryCorrectRequest(BaseModel):
    """纠正记忆请求。"""

    value: str = Field(..., min_length=1, max_length=500)


class SummaryItem(BaseModel):
    """历史会话摘要。"""

    session_id: str
    topics: List[str]
    open_plans: List[str]
    emotional_state: Optional[str]
    created_at: str


class CharacterMeta(BaseModel):
    """角色基础信息。"""

    id: str
    name: str
    description: str


class CharacterInitState(BaseModel):
    """角色初始状态（初始气氛 / 初始情绪）。"""

    mood: int = 0
    emotion: str = "idle"


class CharacterResponse(BaseModel):
    """角色卡下发（改动三：立绘映射/默认情绪由后端从角色卡读出，前端只展示）。"""

    meta: CharacterMeta
    portrait_map: Dict[str, str]
    default_emotion: str
    init_state: CharacterInitState
