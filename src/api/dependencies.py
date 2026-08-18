"""依赖注入：构建并共享对话引擎、气氛值、事件系统等单例。"""

from functools import lru_cache
from pathlib import Path

from src.config.settings import settings
from src.core.agent.dialogue import DialogueEngine
from src.core.agent.event import EventSystem
from src.core.agent.mood import MoodSystem
from src.core.character.corpus import load_corpus
from src.core.character.loader import load_system_prompt
from src.core.llm.client import LLMClient
from src.core.memory.short_term import ShortTermMemory


@lru_cache(maxsize=1)
def get_engine() -> DialogueEngine:
    """构建对话引擎单例。"""
    llm = LLMClient(settings)
    memory = ShortTermMemory(settings.max_history)
    mood = MoodSystem()
    events = EventSystem(str(Path(settings.config_dir) / "events.json"))

    system_prompt = load_system_prompt(settings.config_dir)
    # 语料位于角色配置目录下的 phrases 子目录
    corpus = load_corpus(str(Path(settings.config_dir) / "phrases"))

    return DialogueEngine(
        llm_client=llm,
        memory=memory,
        mood=mood,
        events=events,
        system_prompt=system_prompt,
        corpus=corpus,
    )


@lru_cache(maxsize=1)
def get_mood() -> MoodSystem:
    """获取气氛值系统单例。"""
    return get_engine().mood


@lru_cache(maxsize=1)
def get_events() -> EventSystem:
    """获取事件系统单例。"""
    return get_engine().events
