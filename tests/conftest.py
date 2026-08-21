"""测试基础设施：共享 fixture。"""

import os
import tempfile
from pathlib import Path

import pytest
from src.core.agent.event import EventSystem
from src.core.agent.mood import MoodSystem

ROOT = Path(__file__).resolve().parent.parent
EVENTS_CONFIG = str(ROOT / "src" / "config" / "roles" / "events.json")
CHARACTER_CONFIG_DIR = str(ROOT / "src" / "config" / "roles")

# 测试进程级数据隔离：长期记忆落到系统临时目录，禁止写入仓库（rules.md §11）
_TMP_DATA_DIR = tempfile.mkdtemp(prefix="ai_agent_test_data_")
os.environ["MEMORY_DB_PATH"] = str(Path(_TMP_DATA_DIR) / "memory.db")


@pytest.fixture
def mood() -> MoodSystem:
    """返回初始化的气氛值系统。"""
    return MoodSystem()


@pytest.fixture
def events() -> EventSystem:
    """返回从配置文件加载的事件系统。"""
    return EventSystem(EVENTS_CONFIG)
