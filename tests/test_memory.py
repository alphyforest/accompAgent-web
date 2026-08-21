"""记忆系统单元测试。"""

import aiosqlite
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


# ================================================================ 第二阶段：语义记忆


@pytest.mark.asyncio
async def test_user_memory_upsert(tmp_path):
    """user_memory 按 (user_id, category, key) 唯一，重复写入更新而非新增。"""
    memory = LongTermMemory(str(tmp_path / "m.db"))
    await memory.save_memory("default", "fact", "city", "上海", importance=6)
    await memory.save_memory("default", "fact", "city", "北京", importance=8)

    rows = await memory.list_memory("default")
    assert len(rows) == 1
    assert rows[0].value == "北京"
    assert rows[0].importance == 8


@pytest.mark.asyncio
async def test_top_memory_ordering(tmp_path):
    """get_top_memory 按 importance 降序取 top-k。"""
    memory = LongTermMemory(str(tmp_path / "m.db"))
    for i in range(1, 6):
        await memory.save_memory("default", "fact", f"k{i}", str(i), importance=i)
    top = await memory.get_top_memory("default", k=3)
    assert [r.value for r in top] == ["5", "4", "3"]


@pytest.mark.asyncio
async def test_memory_correct_and_delete(tmp_path):
    """纠正置 confirmed=1；删除按 id。"""
    memory = LongTermMemory(str(tmp_path / "m.db"))
    record = await memory.save_memory("default", "profile", "user_name", "小林")
    assert record.confirmed == 0

    corrected = await memory.correct_memory(record.id, "小玲")
    assert corrected is not None and corrected.value == "小玲" and corrected.confirmed == 1

    assert await memory.delete_memory(record.id) is True
    assert await memory.delete_memory(record.id) is False  # 已删除
    assert await memory.get_memory(record.id) is None


@pytest.mark.asyncio
async def test_forgetting_decay_and_remove(tmp_path):
    """遗忘策略：超期降权，importance<=0 删除。"""
    memory = LongTermMemory(str(tmp_path / "m.db"))
    old = await memory.save_memory("default", "fact", "old_fact", "旧事实", importance=1)
    fresh = await memory.save_memory("default", "fact", "fresh_fact", "新事实", importance=9)
    # 将 old 记录的 updated_at 拨回 31 天前（UTC 同 CURRENT_TIMESTAMP 格式）
    async with aiosqlite.connect(str(tmp_path / "m.db")) as db:
        await db.execute("UPDATE user_memory SET updated_at = datetime('now','-31 days') WHERE id = ?", (old.id,))
        await db.commit()

    result = await memory.apply_forgetting("default", days=30, decay=2)
    # old: importance 1 -> -1 -> 删除；fresh 未超期保留
    assert result["removed"] == 1
    assert await memory.get_memory(old.id) is None
    assert await memory.get_memory(fresh.id) is not None


@pytest.mark.asyncio
async def test_forgetting_decay_does_not_refresh_updated_at(tmp_path):
    """降权不刷新 updated_at（Bug 3）：未引用记忆能持续衰减，而非降一次后时钟被拨回。"""
    memory = LongTermMemory(str(tmp_path / "m.db"))
    rec = await memory.save_memory("default", "fact", "k", "v", importance=5)
    async with aiosqlite.connect(str(tmp_path / "m.db")) as db:
        await db.execute("UPDATE user_memory SET updated_at = datetime('now','-31 days') WHERE id = ?", (rec.id,))
        await db.commit()

    # 第一次降权：5 -> 3，updated_at 仍为 31 天前（未被刷新）
    r1 = await memory.apply_forgetting("default", days=30, decay=2)
    assert r1["decayed"] == 1 and r1["removed"] == 0
    row = await memory.get_memory(rec.id)
    assert row is not None and row.importance == 3

    # 再次执行（模拟 31 天后仍未引用）：3 -> 1，仍可继续衰减、不删除
    r2 = await memory.apply_forgetting("default", days=30, decay=2)
    assert r2["decayed"] == 1 and r2["removed"] == 0
    row = await memory.get_memory(rec.id)
    assert row is not None and row.importance == 1


@pytest.mark.asyncio
async def test_session_summary_roundtrip(tmp_path):
    """会话摘要读写（JSON 数组字段往返）。"""
    memory = LongTermMemory(str(tmp_path / "m.db"))
    await memory.save_summary("s1", ["数学"], ["周末去公园"], "有点累")

    summary = await memory.get_summary("s1")
    assert summary is not None
    assert summary.topics == ["数学"]
    assert summary.open_plans == ["周末去公园"]
    assert summary.emotional_state == "有点累"

    summaries = await memory.list_summaries()
    assert len(summaries) == 1

    await memory.delete_summary("s1")
    assert await memory.get_summary("s1") is None
    await memory.clear_summaries()
    assert await memory.list_summaries() == []
