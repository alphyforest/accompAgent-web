"""SQLite 长期记忆持久化。"""

from typing import Dict, List, Optional

import aiosqlite


class LongTermMemory:
    """基于 SQLite 的长期记忆，负责对话与事件链状态的持久化。"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init_db(self) -> None:
        """初始化数据库表结构。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(DDL)

    async def save_conversation(self, session_id: str, role: str, content: str, mood: Optional[int] = None) -> None:
        """保存一条对话记录。"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO conversations (session_id, role, content, mood) VALUES (?, ?, ?, ?)",
                (session_id, role, content, mood),
            )
            await db.commit()

    async def get_history(self, session_id: str, limit: int = 10) -> List[Dict[str, str]]:
        """查询指定会话最近的历史消息。"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT role, content FROM conversations WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
            rows = list(await cursor.fetchall())
        # 按时间正序返回
        return [{"role": row[0], "content": row[1]} for row in rows[::-1]]

    async def save_chain_state(self, session_id: str, chain_id: str, current_step: int, is_active: bool) -> None:
        """保存或更新事件链状态。"""
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
        """查询事件链状态。"""
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
        """清除事件链状态。"""
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
"""
