"""依赖注入：构建并共享对话引擎、气氛值、事件系统、主动调度器等单例。"""

from functools import lru_cache
from typing import Dict, List, Literal, Optional
from uuid import uuid4

from src.application.contracts import (
    CapabilitySnapshot,
    CharacterView,
    MemoryItemView,
    MemoryListResult,
    MoodSnapshot,
    RequestContext,
    SummaryView,
)
from src.application.control_service import MemoryApplicationService, ResetApplicationService
from src.application.orchestrator import ConversationOrchestrator
from src.application.query_service import CharacterQueryService, DialogueQueryService, InitiativeQueryService
from src.application.routing import CapabilityRouter
from src.bootstrap.adapters import DialogueServiceAdapter, ToolServiceAdapter
from src.config.settings import settings
from src.core.agent.dialogue import DialogueEngine
from src.core.agent.event import EventSystem
from src.core.agent.initiative_scheduler import InitiativeScheduler
from src.core.agent.mood import MoodSystem
from src.core.agent.triggers import InitiativeTriggerMatcher
from src.core.character.card import load_character_card
from src.core.character.corpus import load_corpus
from src.core.character.loader import load_system_prompt
from src.core.llm.client import LLMClient
from src.core.memory.long_term import LongTermMemory, MemoryRecord, SummaryRecord
from src.core.memory.short_term import ShortTermMemory
from src.core.tools.builtin import build_now_tool
from src.core.tools.registry import ToolRegistry
from src.core.tools.runtime import ToolRuntime
from src.core.tools.sources.mcp import McpToolSource
from src.core.tools.tool_loop import ToolLoop
from src.utils.paths import resolve_user_path, resource_path, user_backup_path


@lru_cache(maxsize=1)
def get_engine() -> DialogueEngine:
    """构建对话引擎单例（角色卡驱动，事件系统已解耦为独立单例）。"""
    llm = LLMClient(settings)
    memory = ShortTermMemory(settings.max_history)

    config_dir = str(resource_path(settings.config_dir))
    card = load_character_card(config_dir)
    # 用角色卡 init_state 初始化气氛值（蓝图 §3.1：切换角色时按初始状态复位）
    mood = MoodSystem(initial_mood=card.init_state.mood)
    long_term = LongTermMemory(str(resolve_user_path(settings.memory_db_path, "data")))

    system_prompt = load_system_prompt(config_dir, card.system_prompt_file)
    # 语料位于角色配置目录下的 phrases 子目录
    corpus = load_corpus(str(resource_path(settings.config_dir) / "phrases"))

    return DialogueEngine(
        llm_client=llm,
        memory=memory,
        mood=mood,
        card=card,
        system_prompt=system_prompt,
        corpus=corpus,
        long_term=long_term,
        idle_timeout=float(settings.memory_idle_timeout_minutes * 60),
        segment_max=settings.memory_segment_max_messages,
        inject_top_k=settings.memory_inject_top_k,
        forget_days=settings.memory_forget_days,
        forget_decay=settings.memory_forget_decay,
        instant_enabled=settings.memory_instant_enabled,
        instant_keywords=settings.memory_instant_keywords,
    )


@lru_cache(maxsize=1)
def get_mood() -> MoodSystem:
    """获取气氛值系统单例。"""
    return get_engine().mood


@lru_cache(maxsize=1)
def get_events() -> EventSystem:
    """获取事件系统单例（与引擎解耦，保留调试口：/api/event/trigger、/api/status.active_chain）。"""
    return EventSystem(str(resource_path(settings.config_dir) / "events.json"))


@lru_cache(maxsize=1)
def get_tool_runtime() -> ToolRuntime:
    """构建工具运行时单例：内置 now 工具 + 可选 Agenda MCP 来源。

    懒连接：McpToolSource 真实连接在首次对话同步时触发（lifespan 负责关闭）；
    进程级故障经 on_unavailable 回调把该来源工具整体标记不可用（规格 §7）。
    """
    registry = ToolRegistry()
    registry.register(build_now_tool())
    sources = []
    if settings.agenda_mcp_enabled and settings.agenda_mcp_command and settings.agenda_mcp_args:
        sources.append(
            McpToolSource(
                command=settings.agenda_mcp_command,
                args=settings.agenda_mcp_args,
                env={"AGENDA_DATA_PATH": settings.agenda_data_path} if settings.agenda_data_path else None,
                name="agenda",
            )
        )
    runtime = ToolRuntime(
        registry,
        sources,
        backup_data_path=settings.agenda_data_path,
        backup_dir=settings.agenda_data_backup_dir or str(user_backup_path()),
    )
    for source in sources:
        source._on_unavailable = lambda names: registry.disable_names(names)
    return runtime


@lru_cache(maxsize=1)
def get_scheduler() -> InitiativeScheduler:
    """获取主动说话调度器单例（复用引擎内置触发器匹配器）。"""
    engine = get_engine()
    matcher = engine.matcher or InitiativeTriggerMatcher([])
    return InitiativeScheduler(engine=engine, matcher=matcher, session_id="default")


# ---------------------------------------------------------------- R1：应用服务 Port 适配器


class ApplicationDependencyUnavailable(Exception):
    """应用服务底层依赖未启用（适配器层异常；路由映射为 503，不进应用契约）。"""


def _memory_item_view(record: MemoryRecord) -> MemoryItemView:
    """MemoryRecord -> MemoryItemView（字段与 /api/memory 响应逐字段对齐）。"""
    return MemoryItemView(
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


def _summary_view(record: SummaryRecord) -> SummaryView:
    """SummaryRecord -> SummaryView。"""
    return SummaryView(
        session_id=record.session_id,
        topics=record.topics,
        open_plans=record.open_plans,
        emotional_state=record.emotional_state,
        created_at=record.created_at,
    )


class EngineDialogueStatePort:
    """DialogueStatePort 适配器：包装 MoodSystem。"""

    def __init__(self, mood: MoodSystem) -> None:
        self._mood = mood

    def get_mood(self) -> MoodSnapshot:
        return MoodSnapshot(mood=self._mood.mood, label=self._mood.get_label())


class EngineCharacterPort:
    """CharacterPort 适配器：包装 DialogueEngine.card（只读）。"""

    def __init__(self, engine: DialogueEngine) -> None:
        self._engine = engine

    def get(self) -> CharacterView:
        card = self._engine.card
        if card is None:
            raise ApplicationDependencyUnavailable("角色卡未加载")
        return CharacterView(
            character_id=card.meta.id,
            name=card.meta.name,
            description=card.meta.description,
            portrait_map=card.portrait_map,
            default_emotion=card.output_protocol.default_emotion,
            init_mood=card.init_state.mood,
            init_emotion=card.init_state.emotion,
        )


class EngineMemoryPort:
    """MemoryPort 适配器：包装 LongTermMemory + DialogueEngine（reset 走 engine.reset_all）。"""

    def __init__(self, engine: DialogueEngine) -> None:
        self._engine = engine

    def _memory(self) -> LongTermMemory:
        if self._engine.long_term is None:
            raise ApplicationDependencyUnavailable("长期记忆未启用")
        return self._engine.long_term

    async def list_grouped(self) -> MemoryListResult:
        memory = self._memory()
        rows = await memory.list_memory(self._engine.user_id)
        groups: Dict[str, List[MemoryItemView]] = {}
        for row in rows:
            groups.setdefault(row.category, []).append(_memory_item_view(row))
        return MemoryListResult(user_id=self._engine.user_id, groups=groups)

    async def delete(self, memory_id: int) -> bool:
        return await self._memory().delete_memory(memory_id)

    async def correct(self, memory_id: int, value: str) -> Optional[MemoryItemView]:
        record = await self._memory().correct_memory(memory_id, value)
        return _memory_item_view(record) if record is not None else None

    async def list_summaries(self) -> List[SummaryView]:
        records = await self._memory().list_summaries()
        return [_summary_view(record) for record in records]

    async def reset(self, level: str, session_id: str) -> None:
        await self._engine.reset_all(level, session_id)


class SchedulerInitiativeSourcePort:
    """InitiativeSourcePort 适配器：包装 InitiativeScheduler。"""

    def __init__(self, scheduler: InitiativeScheduler) -> None:
        self._scheduler = scheduler

    async def collect(self) -> List[str]:
        return await self._scheduler.collect()


# ---------------------------------------------------------------- R1：Application Service 单例


@lru_cache(maxsize=1)
def get_dialogue_query_service() -> DialogueQueryService:
    """气氛查询服务（GET /api/mood）。"""
    return DialogueQueryService(EngineDialogueStatePort(get_mood()))


@lru_cache(maxsize=1)
def get_character_query_service() -> CharacterQueryService:
    """角色查询服务（GET /api/character）。"""
    return CharacterQueryService(EngineCharacterPort(get_engine()))


@lru_cache(maxsize=1)
def get_memory_application_service() -> MemoryApplicationService:
    """记忆管理服务（/api/memory*、/api/summaries）。"""
    return MemoryApplicationService(EngineMemoryPort(get_engine()))


@lru_cache(maxsize=1)
def get_reset_application_service() -> ResetApplicationService:
    """三档重置服务（POST /api/reset）。"""
    return ResetApplicationService(EngineMemoryPort(get_engine()))


@lru_cache(maxsize=1)
def get_initiative_query_service() -> InitiativeQueryService:
    """主动发言查询服务（GET /api/initiative）。"""
    return InitiativeQueryService(SchedulerInitiativeSourcePort(get_scheduler()))


# ---------------------------------------------------------------- R2：会话编排装配（chat 入口）


@lru_cache(maxsize=1)
def get_orchestrator() -> ConversationOrchestrator:
    """装配应用层编排器：DialogueService/ToolService 适配现有实现，chat 路由走此入口。

    Composition Root 仍在 api/dependencies.py（R1 决定）；装配稳定后迁入 bootstrap/container.py。
    """
    engine = get_engine()
    runtime = get_tool_runtime()
    loop = ToolLoop(
        llm_client=engine.llm,
        registry=runtime.registry,
        max_rounds=settings.agenda_tool_rounds,
        call_timeout=float(settings.agenda_tool_timeout),
        overall_timeout=float(settings.agenda_tool_overall_timeout),
    )
    tool_service = ToolServiceAdapter(loop=loop, registry=runtime.registry, runtime=runtime)
    return ConversationOrchestrator(
        router=CapabilityRouter(),
        dialogue=DialogueServiceAdapter(engine),
        tool=tool_service,
    )


def get_capability_snapshot() -> CapabilitySnapshot:
    """路由能力快照（R2 v1 近似：非 now 的可用工具即视为 calendar 能力；R5 引入 capability 元数据）。"""
    registry = get_tool_runtime().registry
    names = [spec.name for spec in registry.list() if not spec.disabled and spec.name != "now"]
    return CapabilitySnapshot(
        available_capabilities=["calendar.read", "calendar.write"] if names else [],
        active_entertainment=False,
    )


async def ensure_tools_synced() -> None:
    """工具来源同步入口：`chat` 首轮生成能力快照前先触发懒连接（R2 修复）。

    背景：R2 把同步从 DialogueEngine 移入 ToolServiceAdapter.sync 后，chat 入口无人触发，
    agenda 工具永不注册 → 能力快照恒空 → 路由永远走普通对话（"调日程没反应"）。
    本函数在 `get_capability_snapshot()` 之前调用；`ToolRuntime.sync()` 幂等且有 synced
    缓存（每轮开销极小），失败仅告警降级，不抛。
    """
    await get_tool_runtime().sync()


def default_request_context(
    session_id: str, requested_mode: Literal["companion", "office", "auto"] = "auto"
) -> RequestContext:
    """为一次性请求生成上下文（request/trace id 唯一；character_id 取自当前角色卡）。"""
    engine = get_engine()
    character_id = engine.card.meta.id if engine.card is not None else "unknown"
    return RequestContext(
        request_id=uuid4().hex,
        trace_id=uuid4().hex,
        session_id=session_id,
        character_id=character_id,
        requested_mode=requested_mode,
    )
