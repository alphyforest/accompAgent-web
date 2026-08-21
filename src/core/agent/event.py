"""事件系统：气氛值 + 概率触发、链式状态机、分钟冷却。

事件配置（events.json）为数组，每个节点字段：
- ``id``: 唯一标识
- ``name``: 名称
- ``type``: "event"（可自主触发）| "chain"（仅由上级链驱动）
- ``minMood`` / ``maxMood``: 触发所需气氛值范围（仅 event 节点）
- ``probability``: 触发概率 0~1（仅 event 节点）
- ``cooldownMinutes``: 触发后的冷却分钟数（仅 event 节点）
- ``prompt``: 触发后交给 LLM 生成回复的提示词
- ``emotion``: 系统指定的情绪标签
- ``chain``: [同意后下一节点 id, 拒绝后下一节点 id]（可为 null）
"""

import json
import random
import time
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class EventNode(BaseModel):
    """事件节点（领域模型，Pydantic，rules.md §15.1）。"""

    id: str
    name: str
    type: str
    prompt: str
    emotion: str
    min_mood: int = -100
    max_mood: int = 100
    probability: float = 1.0
    cooldown_minutes: int = 0
    chain: List[Optional[str]] = Field(default_factory=list)


class EventSystem:
    """事件系统。"""

    def __init__(self, config_path: str):
        self.nodes: Dict[str, EventNode] = {}
        # 记录各 event 节点上次触发时间（Unix 秒），用于分钟级冷却
        self._last_trigger: Dict[str, float] = {}
        # 当前激活链状态：{"node_id": str, "prev_node_id": str | None}
        self.active_node_id: Optional[str] = None
        self.active_prev_id: Optional[str] = None
        self._load_config(config_path)

    def _load_config(self, config_path: str) -> None:
        """从 JSON 配置文件加载事件节点。"""
        with Path(config_path).open(encoding="utf-8") as f:
            data = json.load(f)

        for item in data:
            node = EventNode(
                id=item["id"],
                name=item["name"],
                type=item.get("type", "event"),
                prompt=item.get("prompt", ""),
                emotion=item.get("emotion", "idle"),
                min_mood=item.get("minMood", -100),
                max_mood=item.get("maxMood", 100),
                probability=item.get("probability", 1.0),
                cooldown_minutes=item.get("cooldownMinutes", 0),
                chain=item.get("chain", []),
            )
            self.nodes[node.id] = node

    def match_event(self, mood: int) -> Optional[str]:
        """按气氛值 + 概率匹配可自主触发的事件，返回事件节点 id。"""
        if self.active_node_id is not None:
            return None

        now = time.time()
        candidates: List[str] = []
        for node in self.nodes.values():
            if node.type != "event":
                continue
            if not (node.min_mood <= mood <= node.max_mood):
                continue
            # 冷却检查
            last = self._last_trigger.get(node.id, 0.0)
            if node.cooldown_minutes > 0 and now - last < node.cooldown_minutes * 60:
                continue
            # 概率判定
            if random.random() <= node.probability:
                candidates.append(node.id)

        if not candidates:
            return None
        # 随机选取一个候选
        return random.choice(candidates)

    def start_event(self, node_id: str) -> Optional[EventNode]:
        """启动事件，记录触发时间与链状态，返回事件节点。"""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        self._last_trigger[node_id] = time.time()
        self.active_node_id = node_id
        self.active_prev_id = None
        return node

    def judge_response(self, user_input: str) -> Optional[str]:
        """判断用户对事件链的回应：accept / reject / 不明确（None）。"""
        if self._is_accept(user_input):
            return "accept"
        if self._is_reject(user_input):
            return "reject"
        return None

    def process_response(self, user_input: str) -> Optional[EventNode]:
        """处理事件链中的用户响应，返回下一步应执行的节点。

        仅当当前节点带有 ``chain``（即 [同意, 拒绝]）时才会推进；
        否则视为链已结束。
        """
        if self.active_node_id is None:
            return None

        current = self.nodes.get(self.active_node_id)
        if current is None:
            self._end_chain()
            return None

        if not current.chain:
            self._end_chain()
            return None

        accept, reject = current.chain[0], (current.chain[1] if len(current.chain) > 1 else None)
        attitude = self.judge_response(user_input)
        if attitude == "accept":
            target = accept
        elif attitude == "reject":
            target = reject
        else:
            return None  # 不明确，等待用户再次表态

        if target is None:
            self._end_chain()
            return None

        self.active_prev_id = current.id
        self.active_node_id = target
        return self.nodes[target]

    def force_trigger(self, node_id: str) -> Optional[EventNode]:
        """按节点 id 强制触发事件（用于测试）。"""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        return self.start_event(node_id)

    @staticmethod
    def _is_accept(user_input: str) -> bool:
        """判断用户是否表达同意。"""
        return any(kw in user_input for kw in ["好", "可以", "嗯", "愿意", "同意", "行"])

    @staticmethod
    def _is_reject(user_input: str) -> bool:
        """判断用户是否表达拒绝。

        注意排除「不错」等含「不」的肯定表达。
        """
        reject_words = ["不想", "不要", "不了", "不行", "不去", "拒绝", "算了", "没空", "下次"]
        if any(kw in user_input for kw in reject_words):
            return True
        return False

    def _end_chain(self) -> None:
        self.active_node_id = None
        self.active_prev_id = None

    @property
    def active_node(self) -> Optional[str]:
        """返回当前激活链的节点 id。"""
        return self.active_node_id

    @property
    def cooldown(self) -> int:
        """兼容旧接口：返回 0（冷却已改用时间戳管理）。"""
        return 0

    def reset(self) -> None:
        """重置事件系统状态。"""
        self._end_chain()
        self._last_trigger.clear()
