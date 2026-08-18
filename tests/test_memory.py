"""记忆系统单元测试。"""

import pytest
from src.core.memory.long_term import LongTermMemory
from src.core.memory.short_term import ShortTermMemory


def test_short_term_add_and_get():
    memory = ShortTermMemory(max_history=4)
    memory.add("s1", "user", "你好")
    memory.add("s1", "assistant", "你好呀~")
    history = memory.get_history("s1")
    assert len(history) == 2
    assert history[0]["role"] == "user"


def test_short_term_sliding_window():
    memory = ShortTermMemory(max_history=3)
    for i in range(5):
        memory.add("s1", "user", f"消息{i}")
    history = memory.get_history("s1")
    assert len(history) == 3
    assert history[0]["content"] == "消息2"


def test_short_term_clear():
    memory = ShortTermMemory()
    memory.add("s1", "user", "你好")
    memory.clear("s1")
    assert memory.get_history("s1") == []


@pytest.mark.asyncio
async def test_long_term_roundtrip(tmp_path):
    db_path = str(tmp_path / "test.db")
    memory = LongTermMemory(db_path)
    await memory.init_db()

    await memory.save_conversation("s1", "user", "你好", mood=5)
    await memory.save_conversation("s1", "assistant", "你好呀~", mood=6)

    history = await memory.get_history("s1", limit=10)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["content"] == "你好呀~"


@pytest.mark.asyncio
async def test_long_term_chain_state(tmp_path):
    db_path = str(tmp_path / "test.db")
    memory = LongTermMemory(db_path)
    await memory.init_db()

    await memory.save_chain_state("s1", "invite_chain", 2, True)
    state = await memory.get_chain_state("s1")
    assert state is not None
    assert state["chain_id"] == "invite_chain"
    assert state["current_step"] == 2

    await memory.clear_chain_state("s1")
    assert await memory.get_chain_state("s1") is None
