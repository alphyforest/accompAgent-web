"""依赖注入：构建并共享对话引擎、气氛值、事件系统、主动调度器等单例。"""

from functools import lru_cache

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
from src.core.memory.long_term import LongTermMemory
from src.core.memory.short_term import ShortTermMemory
from src.core.tools.builtin import build_now_tool
from src.core.tools.registry import ToolRegistry
from src.core.tools.runtime import ToolRuntime
from src.core.tools.sources.mcp import McpToolSource
from src.utils.paths import resource_path


@lru_cache(maxsize=1)
def get_engine() -> DialogueEngine:
    """构建对话引擎单例（角色卡驱动，事件系统已解耦为独立单例）。"""
    llm = LLMClient(settings)
    memory = ShortTermMemory(settings.max_history)

    config_dir = str(resource_path(settings.config_dir))
    card = load_character_card(config_dir)
    # 用角色卡 init_state 初始化气氛值（蓝图 §3.1：切换角色时按初始状态复位）
    mood = MoodSystem(initial_mood=card.init_state.mood)
    long_term = LongTermMemory(str(resource_path(settings.memory_db_path)))

    system_prompt = load_system_prompt(config_dir, card.system_prompt_file)
    # 语料位于角色配置目录下的 phrases 子目录
    corpus = load_corpus(str(resource_path(settings.config_dir) / "phrases"))

    runtime = get_tool_runtime()
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
        tool_registry=runtime.registry,
        tool_runtime=runtime,
        tool_rounds=settings.agenda_tool_rounds,
        tool_call_timeout=float(settings.agenda_tool_timeout),
        tool_overall_timeout=float(settings.agenda_tool_overall_timeout),
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
    if settings.agenda_mcp_enabled:
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
        backup_dir=settings.agenda_data_backup_dir,
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
