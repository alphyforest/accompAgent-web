"""应用层契约（PLAN-010 R1，SPEC-010 §2/§4/§8）。

- 只包含可序列化模型与 Port（Protocol），不依赖 FastAPI / 具体引擎 / 具体 LLM 客户端
- UIEvent 先提供 R1 最小骨架（type + 请求标识 + payload）；R4 再实现 SPEC-050 全量事件集
- Port 签名与 SPEC-010 §4 对齐；R1 阶段端口由 Fake 实现测试，API 切换在后续小阶段完成
"""

from datetime import datetime
from typing import AsyncIterator, Dict, List, Literal, Optional, Protocol

from pydantic import BaseModel, Field


class RequestContext(BaseModel):
    """单次请求上下文（SPEC-010 §2.1）：request_id 唯一标识一次请求；复合流程共享 trace_id。"""

    request_id: str
    trace_id: str
    session_id: str
    user_id: str = "default"
    character_id: str
    client_id: Optional[str] = None
    requested_mode: Literal["companion", "office", "auto"] = "auto"
    deadline_ms: Optional[int] = None


class CapabilityRequest(BaseModel):
    """能力请求（路由/编排输入；R2 扩展路由时使用）。"""

    user_text: str = Field(..., min_length=1, max_length=2000)
    requested_mode: Literal["companion", "office", "auto"] = "auto"


class CapabilitySnapshot(BaseModel):
    """请求开始时的只读能力快照（SPEC-010 §2.2；避免编排期间注册表变化）。"""

    available_capabilities: List[str] = Field(default_factory=list)
    active_entertainment: bool = False


class RouteDecision(BaseModel):
    """路由结果（SPEC-010 §2.2）：target / 稳定 reason_code / 置信度 / 选中能力。"""

    target: Literal["dialogue", "tool", "entertainment", "composite"]
    reason_code: str
    confidence: float = Field(ge=0.0, le=1.0)
    selected_capabilities: List[str] = Field(default_factory=list)


class DialogueResult(BaseModel):
    """对话模块结构化结果（R2 起由 DialogueService 产出）。"""

    text: str
    emotion: Optional[str] = None


class ToolExecutionResult(BaseModel):
    """工具执行结果（SPEC-030 §8 的 R1 子集；R5 扩展 duration_ms 等审计字段）。"""

    tool_name: str
    status: Literal["success", "failed", "cancelled"] = "success"
    data: Dict[str, object] = Field(default_factory=dict)
    side_effects: List[str] = Field(default_factory=list)
    user_message: str = ""
    error_code: Optional[str] = None
    duration_ms: int = 0


class EntertainmentSession(BaseModel):
    """娱乐会话占位（本阶段仅用于 unavailable 判定）。"""

    session_id: str
    active: bool = True


class EntertainmentResult(BaseModel):
    """娱乐模块占位契约（本阶段恒不可用）。"""

    available: bool = False
    state_changed: bool = False
    narration_prompt: str = ""


class CapabilityError(BaseModel):
    """结构化错误（SPEC-010 §8）：code / user_message / retryable / source。"""

    code: str
    user_message: str
    retryable: bool = False
    source: str = "unknown"


class UIEvent(BaseModel):
    """统一 UI 事件（SPEC-050 v1.0 Envelope）。

    - sequence 在单 request 内从 1 单调递增；客户端不得假定跨 request 全局有序
    - 每个 request 只能有一个终态：completed / cancelled / failed
    """

    schema_version: Literal["1.0"] = "1.0"
    event_id: str
    request_id: str
    trace_id: str
    session_id: str = "default"
    type: str
    source: Literal[
        "orchestrator",
        "dialogue",
        "tool",
        "entertainment",
        "initiative",
        "system",
    ] = "orchestrator"
    timestamp: datetime
    sequence: int = 1
    payload: Dict[str, object] = Field(default_factory=dict)


# ---------------------------------------------------------------- ModelPort（SPEC-010 §4）


class DialogueModelRequest(BaseModel):
    """对话模型请求（流式回复）。"""

    messages: List[Dict[str, str]]
    max_tokens: Optional[int] = None


class ExtractRequest(BaseModel):
    """结构化抽取请求。"""

    messages: List[Dict[str, str]]


class ExtractResult(BaseModel):
    """结构化抽取结果。"""

    data: Optional[Dict[str, object]] = None


class ToolModelRequest(BaseModel):
    """带工具的函数调用请求。"""

    messages: List[Dict[str, str]]
    tools: List[Dict[str, object]] = Field(default_factory=list)


class ToolModelResult(BaseModel):
    """带工具请求的结果（tool_calls 规整化：id/name/arguments）。"""

    content: str = ""
    tool_calls: List[Dict[str, object]] = Field(default_factory=list)
    finish_reason: Optional[str] = None


class DialogueModelPort(Protocol):
    """对话模块的模型端口（SPEC-010 §4）。"""

    def stream_reply(self, request: DialogueModelRequest) -> AsyncIterator[str]: ...

    async def structured_extract(self, request: ExtractRequest) -> ExtractResult: ...


class ToolModelPort(Protocol):
    """工具模块的模型端口（SPEC-010 §4）。"""

    async def complete_with_tools(self, request: ToolModelRequest) -> ToolModelResult: ...


# ---------------------------------------------------------------- 模块 Port（SPEC-010 §4.1~4.3）


class DialogueEvent(BaseModel):
    """对话模块事件（SPEC-050 §5）：message.* / emotion.changed / mood.changed。

    payload 可携带结构化字段（如 message_source、emotion/portrait_id、mood value/label）。
    """

    type: str
    content: str = ""
    emotion: Optional[str] = None
    payload: Dict[str, object] = Field(default_factory=dict)


class DialogueRequest(BaseModel):
    """对话请求。"""

    user_text: str
    session_id: str
    context: RequestContext


class PresentationRequest(BaseModel):
    """工具结果呈现请求（SPEC-010 §5.2：事实源保真）。"""

    result: ToolExecutionResult
    context: RequestContext


class DialogueServicePort(Protocol):
    """对话服务端口（SPEC-010 §4.1）。"""

    def reply_stream(self, request: DialogueRequest) -> AsyncIterator[DialogueEvent]: ...

    def present_result_stream(self, request: PresentationRequest) -> AsyncIterator[DialogueEvent]: ...

    async def build_messages(self, context: RequestContext, user_text: str) -> List[Dict[str, str]]: ...


class ToolEvent(BaseModel):
    """工具模块事件（SPEC-030 §12：tool.selected/started/completed/failed）。"""

    type: str
    tool_name: Optional[str] = None
    result: Optional[ToolExecutionResult] = None
    error: Optional[CapabilityError] = None


class ToolRequest(BaseModel):
    """工具请求。

    messages 为 Orchestrator 经 DialogueService 准备好的模型上下文快照
    （Tool 模块不依赖对话短期记忆实现，STD-010 §4）。
    """

    user_text: str
    context: RequestContext
    capabilities: List[str] = Field(default_factory=list)
    messages: List[Dict[str, str]] = Field(default_factory=list)


class CapabilityMatch(BaseModel):
    """工具能力匹配结果。"""

    matched: bool
    tool_names: List[str] = Field(default_factory=list)


class ToolServicePort(Protocol):
    """工具服务端口（SPEC-010 §4.2）。"""

    async def can_handle(self, request: ToolRequest) -> CapabilityMatch: ...

    def execute(self, request: ToolRequest) -> AsyncIterator[ToolEvent]: ...

    async def cancel(self, request_id: str) -> None: ...


class EntertainmentRequest(BaseModel):
    """娱乐请求（本阶段占位）。"""

    session_id: str
    context: RequestContext


class EntertainmentServicePort(Protocol):
    """娱乐服务端口（SPEC-010 §4.3）。"""

    async def active_session(self, session_id: str) -> Optional[EntertainmentSession]: ...

    async def handle(self, request: EntertainmentRequest) -> EntertainmentResult: ...


class UnavailableEntertainmentService:
    """未启用娱乐模块时的注入替身（SPEC-010 §4.3：避免 Orchestrator 散落 None 判断）。"""

    async def active_session(self, session_id: str) -> Optional[EntertainmentSession]:
        return None

    async def handle(self, request: EntertainmentRequest) -> EntertainmentResult:
        return EntertainmentResult(available=False)


# ---------------------------------------------------------------- 查询/控制 Port（STD-010 §2）


class MoodSnapshot(BaseModel):
    """气氛快照：GET /api/mood。"""

    mood: int
    label: str


class CharacterView(BaseModel):
    """角色视图：GET /api/character。"""

    character_id: str
    name: str
    description: str
    portrait_map: Dict[str, str]
    default_emotion: str
    init_mood: int = 0
    init_emotion: str = "idle"


class MemoryItemView(BaseModel):
    """记忆条目视图（管理 API 用，与 /api/memory 响应逐字段对齐）。"""

    id: int
    category: str
    key: str
    value: str
    importance: int
    confirmed: int
    source_session: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


class SummaryView(BaseModel):
    """会话摘要视图：GET /api/summaries。"""

    session_id: str
    topics: List[str]
    open_plans: List[str]
    emotional_state: Optional[str] = None
    created_at: str = ""


class DialogueStatePort(Protocol):
    """气氛状态的只读端口。"""

    def get_mood(self) -> MoodSnapshot: ...


class CharacterPort(Protocol):
    """角色卡只读端口。"""

    def get(self) -> CharacterView: ...


class InitiativeSourcePort(Protocol):
    """主动发言来源端口（前端轮询）。"""

    async def collect(self) -> List[str]: ...


class MemoryListResult(BaseModel):
    """记忆分组结果（含 user_id，供 GET /api/memory 直接透出）。"""

    user_id: str
    groups: Dict[str, List[MemoryItemView]]


class MemoryPort(Protocol):
    """长期记忆管理端口（list/delete/correct/summaries/reset）。"""

    async def list_grouped(self) -> MemoryListResult: ...

    async def delete(self, memory_id: int) -> bool: ...

    async def correct(self, memory_id: int, value: str) -> Optional[MemoryItemView]: ...

    async def list_summaries(self) -> List[SummaryView]: ...

    async def reset(self, level: str, session_id: str) -> None: ...
