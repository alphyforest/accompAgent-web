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
from src.utils.paths import resource_path


@lru_cache(maxsize=1)
def get_engine() -> DialogueEngine:
    """构建对话引擎单例（角色卡驱动，事件系统已解耦为独立单例）。"""
    llm = LLMClient(settings)
    memory = ShortTermMemory(settings.max_history)
    mood = MoodSystem()
    long_term = LongTermMemory(str(resource_path(settings.memory_db_path)))

    config_dir = str(resource_path(settings.config_dir))
    card = load_character_card(config_dir)
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
def get_scheduler() -> InitiativeScheduler:
    """获取主动说话调度器单例（复用引擎内置触发器匹配器）。"""
    engine = get_engine()
    matcher = engine.matcher or InitiativeTriggerMatcher([])
    return InitiativeScheduler(engine=engine, matcher=matcher, session_id="default")
