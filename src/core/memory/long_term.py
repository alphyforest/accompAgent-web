"""SQLite 长期记忆持久化：用户画像 / 会话摘要 / 关系状态 / 事件进度 + 事件链状态。

第二阶段按 ``doc/PHASE2_DEV_PLAN.md`` 扩展两张语义表：
- ``user_memory``：用户画像 / 喜好 / 事实 / 边界 / 需求 / 关系 / 里程碑 / 事件进度
- ``session_summaries``：会话摘要（话题 / 约定 / 情绪倾向）

只存语义层，禁止存对话原文（``conversations`` 表不接入业务）。
"""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional

import aiosqlite
from pydantic import BaseModel


class MemoryRecord(BaseModel):
    """用户记忆条目（领域模型，Pydantic，rules.md §15.1）。"""

    id: int
    user_id: str
    category: str
    key: str
    value: str
    importance: int
    confirmed: int
    source_session: Optional[str]
    created_at: str
    updated_at: str


class SummaryRecord(BaseModel):
    """会话摘要记录（领域模型，Pydantic，rules.md §15.1）。"""

    session_id: str
    topics: List[str]
    open_plans: List[str]
    emotional_state: Optional[str]
    created_at: str


class LongTermMemory:
    """基于 SQLite 的长期记忆，负责语义记忆与会话摘要的持久化。

    连接按操作短开短合（aiosqlite），表结构在首次调用时惰性初始化。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        """幂等初始化：首次使用时建表（测试无 lifespan 场景同样可用）。"""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self.init_db()
            self._initialized = True

    async def init_db(self) -> None:
        """初始化数据库表结构（集中维护 DDL，禁止业务代码拼接）。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(DDL)

    # ---------------------------------------------------------------- 用户记忆

    async def save_memory(
        self,
        user_id: str,
        category: str,
        key: str,
        value: str,
        importance: int = 5,
        confirmed: int = 0,
        source_session: Optional[str] = None,
    ) -> MemoryRecord:
        """保存或更新一条用户记忆（按 user_id+category+key 唯一，更新并刷新 updated_at）。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO user_memory "
                "(user_id, category, key, value, importance, confirmed, source_session) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, category, key) DO UPDATE SET "
                "value = excluded.value, importance = excluded.importance, "
                "confirmed = excluded.confirmed, source_session = excluded.source_session, "
                "updated_at = CURRENT_TIMESTAMP",
                (user_id, category, key, value, importance, confirmed, source_session),
            )
            await db.commit()
        record = await self.get_memory_by(user_id, category, key)
        assert record is not None
        return record

    async def get_memory_by(self, user_id: str, category: str, key: str) -> Optional[MemoryRecord]:
        """按主键组合查询单条记忆。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT id, user_id, category, key, value, importance, confirmed, "
                "source_session, created_at, updated_at FROM user_memory "
                "WHERE user_id = ? AND category = ? AND key = ?",
                (user_id, category, key),
            )
            row = await cursor.fetchone()
        return self._to_memory(row) if row is not None else None

    async def get_memory(self, memory_id: int) -> Optional[MemoryRecord]:
        """按 id 查询单条记忆。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT id, user_id, category, key, value, importance, confirmed, "
                "source_session, created_at, updated_at FROM user_memory WHERE id = ?",
                (memory_id,),
            )
            row = await cursor.fetchone()
        return self._to_memory(row) if row is not None else None

    async def list_memory(self, user_id: str) -> List[MemoryRecord]:
        """列出用户全部记忆（按 category 分组排序）。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT id, user_id, category, key, value, importance, confirmed, "
                "source_session, created_at, updated_at FROM user_memory "
                "WHERE user_id = ? ORDER BY category, importance DESC, updated_at DESC",
                (user_id,),
            )
            rows = await cursor.fetchall()
        return [self._to_memory(r) for r in rows]

    async def get_top_memory(self, user_id: str, k: int) -> List[MemoryRecord]:
        """按 importance 取 top-k 记忆（同分取最近更新），供 prompt 注入。"""
        await self._ensure_initialized()
        limit = max(1, k)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT id, user_id, category, key, value, importance, confirmed, "
                "source_session, created_at, updated_at FROM user_memory "
                "WHERE user_id = ? ORDER BY importance DESC, updated_at DESC LIMIT ?",
                (user_id, limit),
            )
            rows = await cursor.fetchall()
        return [self._to_memory(r) for r in rows]

    async def delete_memory(self, memory_id: int) -> bool:
        """删除单条记忆，返回是否存在。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM user_memory WHERE id = ?", (memory_id,))
            await db.commit()
        return cursor.rowcount > 0

    async def correct_memory(self, memory_id: int, value: str) -> Optional[MemoryRecord]:
        """纠正记忆：更新 value 并标记 confirmed=1。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE user_memory SET value = ?, confirmed = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (value, memory_id),
            )
            await db.commit()
        if cursor.rowcount == 0:
            return None
        return await self.get_memory(memory_id)

    async def touch_memory(self, memory_ids: List[int]) -> None:
        """刷新指定记忆的 updated_at（注入引用时调用）。"""
        if not memory_ids:
            return
        await self._ensure_initialized()
        placeholders = ", ".join("?" for _ in memory_ids)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE user_memory SET updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                tuple(memory_ids),
            )
            await db.commit()

    async def apply_forgetting(self, user_id: str, days: int, decay: int) -> Dict[str, int]:
        """执行遗忘策略：超 days 天未引用的记忆降权 decay 分，importance<=0 删除。

        注意：降权本身**不刷新** ``updated_at``——只有"被引用/注入"（``touch_memory``）
        才重置引用时钟，否则每次降权都会把该记忆的时间拨回当下，导致遗忘形同虚设。
        """
        await self._ensure_initialized()
        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE user_memory SET importance = importance - ? "
                "WHERE user_id = ? AND importance > 0 AND updated_at < ?",
                (decay, user_id, cutoff),
            )
            decayed = cursor.rowcount
            cursor = await db.execute("DELETE FROM user_memory WHERE user_id = ? AND importance <= 0", (user_id,))
            removed = cursor.rowcount
            await db.commit()
        return {"decayed": decayed, "removed": removed}

    async def clear_user_memory(self, user_id: str) -> None:
        """清空用户全部记忆（忘记我）。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM user_memory WHERE user_id = ?", (user_id,))
            await db.commit()

    @staticmethod
    def _to_memory(row: sqlite3.Row) -> MemoryRecord:
        """sqlite 行 -> MemoryRecord。"""
        return MemoryRecord(
            id=row[0],
            user_id=row[1],
            category=row[2],
            key=row[3],
            value=row[4],
            importance=row[5],
            confirmed=row[6],
            source_session=row[7],
            created_at=row[8],
            updated_at=row[9],
        )

    # ---------------------------------------------------------------- 会话摘要

    async def save_summary(
        self,
        session_id: str,
        topics: List[str],
        open_plans: List[str],
        emotional_state: Optional[str],
    ) -> None:
        """保存或更新会话摘要（数组字段以 JSON 文本存储）。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO session_summaries "
                "(session_id, topics, open_plans, user_emotional_state) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "topics = excluded.topics, open_plans = excluded.open_plans, "
                "user_emotional_state = excluded.user_emotional_state",
                (
                    session_id,
                    json.dumps(topics, ensure_ascii=False),
                    json.dumps(open_plans, ensure_ascii=False),
                    emotional_state,
                ),
            )
            await db.commit()

    async def get_summary(self, session_id: str) -> Optional[SummaryRecord]:
        """查询单会话摘要。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT session_id, topics, open_plans, user_emotional_state, created_at "
                "FROM session_summaries WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return SummaryRecord(
            session_id=row[0],
            topics=self._json_list(row[1]),
            open_plans=self._json_list(row[2]),
            emotional_state=row[3],
            created_at=row[4],
        )

    async def list_summaries(self) -> List[SummaryRecord]:
        """列出全部会话摘要（新会话在前）。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT session_id, topics, open_plans, user_emotional_state, created_at "
                "FROM session_summaries ORDER BY created_at DESC",
            )
            rows = await cursor.fetchall()
        return [
            SummaryRecord(
                session_id=row[0],
                topics=self._json_list(row[1]),
                open_plans=self._json_list(row[2]),
                emotional_state=row[3],
                created_at=row[4],
            )
            for row in rows
        ]

    async def delete_summary(self, session_id: str) -> None:
        """删除单会话摘要。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM session_summaries WHERE session_id = ?", (session_id,))
            await db.commit()

    async def clear_summaries(self) -> None:
        """清空全部会话摘要（清所有聊天记录档）。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM session_summaries")
            await db.commit()

    @staticmethod
    def _json_list(raw: Optional[str]) -> List[str]:
        """解析 JSON 数组文本，损坏时兜底为空列表。"""
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        if isinstance(data, list):
            return [str(item) for item in data]
        return []

    # ---------------------------------------------------------------- 对话原文（未接线，保留）

    async def save_conversation(self, session_id: str, role: str, content: str, mood: Optional[int] = None) -> None:
        """保存一条对话记录（预留，业务未接线）。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO conversations (session_id, role, content, mood) VALUES (?, ?, ?, ?)",
                (session_id, role, content, mood),
            )
            await db.commit()

    async def get_history(self, session_id: str, limit: int = 10) -> List[Dict[str, str]]:
        """查询指定会话最近的历史消息（预留，业务未接线）。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT role, content FROM conversations WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
            rows = list(await cursor.fetchall())
        return [{"role": row[0], "content": row[1]} for row in rows[::-1]]

    # ---------------------------------------------------------------- 事件链状态（保留）

    async def save_chain_state(self, session_id: str, chain_id: str, current_step: int, is_active: bool) -> None:
        """保存或更新事件链状态（预留，业务未接线）。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO event_chains (session_id, chain_id, current_step, is_active) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "chain_id = excluded.chain_id, "
                "current_step = excluded.current_step, "
                "is_active = excluded.is_active, "
                "updated_at = CURRENT_TIMESTAMP",
                (session_id, chain_id, current_step, int(is_active)),
            )
            await db.commit()

    async def get_chain_state(self, session_id: str) -> Optional[Dict[str, object]]:
        """查询事件链状态（预留，业务未接线）。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT chain_id, current_step, is_active FROM event_chains WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {"chain_id": row[0], "current_step": row[1], "is_active": bool(row[2])}

    async def clear_chain_state(self, session_id: str) -> None:
        """清除事件链状态（预留，业务未接线）。"""
        await self._ensure_initialized()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM event_chains WHERE session_id = ?", (session_id,))
            await db.commit()


DDL = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    mood INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conversations_session_id ON conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_conversations_created_at ON conversations(created_at);

CREATE TABLE IF NOT EXISTS event_chains (
    session_id TEXT PRIMARY KEY,
    chain_id TEXT NOT NULL,
    current_step INTEGER NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'default',
    category TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    importance INTEGER DEFAULT 5,
    confirmed INTEGER DEFAULT 0,
    source_session TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, category, key)
);

CREATE INDEX IF NOT EXISTS idx_user_memory_updated_at ON user_memory(updated_at);

CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT PRIMARY KEY,
    topics TEXT,
    open_plans TEXT,
    user_emotional_state TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
