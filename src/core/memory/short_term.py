"""会话内短期记忆（滑动窗口）。"""

from collections import deque
from typing import Deque, Dict, List


class ShortTermMemory:
    """会话内滑动窗口记忆，按 session_id 隔离。"""

    def __init__(self, max_history: int = 10):
        self.max_history = max_history
        self._sessions: Dict[str, Deque[Dict[str, str]]] = {}

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        """返回指定会话的历史消息列表。"""
        queue = self._sessions.setdefault(session_id, deque())
        return list(queue)

    def add(self, session_id: str, role: str, content: str) -> None:
        """向会话历史追加一条消息。"""
        queue = self._sessions.setdefault(session_id, deque())
        queue.append({"role": role, "content": content})
        while len(queue) > self.max_history:
            queue.popleft()

    def clear(self, session_id: str) -> None:
        """清空指定会话的历史。"""
        self._sessions.pop(session_id, None)
