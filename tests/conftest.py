"""测试基础设施：共享 fixture。"""

from pathlib import Path

import pytest
from src.core.agent.event import EventSystem
from src.core.agent.mood import MoodSystem

ROOT = Path(__file__).resolve().parent.parent
EVENTS_CONFIG = str(ROOT / "src" / "config" / "roles" / "events.json")


@pytest.fixture
def mood() -> MoodSystem:
    """返回初始化的气氛值系统。"""
    return MoodSystem()


@pytest.fixture
def events() -> EventSystem:
    """返回从配置文件加载的事件系统。"""
    return EventSystem(EVENTS_CONFIG)
