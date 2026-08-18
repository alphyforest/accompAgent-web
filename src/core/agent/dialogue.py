"""对话引擎：流式生成、情绪标签解析、事件注入、语料预热。"""

import re
from typing import AsyncGenerator, Dict, List, Optional

from src.core.agent.event import EventNode, EventSystem
from src.core.agent.mood import MoodSystem
from src.core.llm.client import LLMClient
from src.core.llm.prompt_builder import build_system_prompt
from src.core.memory.short_term import ShortTermMemory

# 情绪标签正则：匹配 [情绪:xxx] 或 [情绪：xxx]，用于任意位置查找
EMOTION_PATTERN = re.compile(r"\[情绪[:：]\s*([A-Za-z]+)\]")

# 多段发言分隔符（prompt 中约定的占位符）
SEGMENT_SEPARATOR = "---"

# 合法的英文情绪标签
VALID_EMOTIONS = {"happy", "sad", "idle", "surprised", "embarrassed", "greet", "sharing"}

# 流式响应中情绪标记的前缀（前端据此切换立绘）
EMOTION_MARK_PREFIX = "[[EMOTION:"


class DialogueEngine:
    """对话引擎，协调 LLM、记忆、气氛值与事件系统。"""

    def __init__(
        self,
        llm_client: LLMClient,
        memory: ShortTermMemory,
        mood: MoodSystem,
        events: EventSystem,
        system_prompt: str,
        corpus: str,
    ):
        self.llm = llm_client
        self.memory = memory
        self.mood = mood
        self.events = events
        self.system_prompt = system_prompt
        self.corpus = corpus
        self._preheated = False

    async def chat_stream(self, user_input: str, session_id: str = "default") -> AsyncGenerator[str, None]:
        """处理一轮对话，流式产出回复文本。

        流式输出结构：
        1. 首段为情绪标记 ``[[EMOTION:xxx]]``（前端据此切换立绘）
        2. 后续为剥离情绪标签后的正文（可能由 ``---`` 分隔多段）
        """
        # 1. 处理进行中的事件链分支响应（优先于普通对话）
        chain_node = self.events.process_response(user_input)
        if chain_node is not None:
            await self.memory_add(session_id, "user", user_input)
            async for chunk in self._generate_with_emotion(chain_node, session_id):
                yield chunk
            return

        # 2. 更新气氛值
        self.mood.update(user_input)

        # 3. 匹配新事件
        event_node = self._match_and_start_event()

        # 4. 预热
        if not self._preheated:
            await self._preheat()

        # 5. 流式生成（普通对话或事件回复）
        await self.memory_add(session_id, "user", user_input)
        async for chunk in self._generate_with_emotion(event_node, session_id, user_input):
            yield chunk

    async def _generate_with_emotion(
        self, event_node: Optional[EventNode], session_id: str, user_input: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """生成回复，解析情绪标签并流式输出。"""
        if event_node is not None:
            # 事件回复：用事件 prompt 生成，情绪由事件指定
            messages = await self._build_event_messages(event_node, session_id)
            emotion = event_node.emotion
        else:
            messages = await self._build_messages(user_input or "", session_id)
            emotion = None

        full_response = ""
        async for chunk in self.llm.stream(messages):
            full_response += chunk

        # 解析情绪与正文（事件时用指定情绪覆盖）
        if emotion is None:
            emotion, body = self._parse_response(full_response)
        else:
            body = self._strip_all_emotions(full_response)

        # 输出情绪标记 + 正文
        yield f"{EMOTION_MARK_PREFIX}{emotion}]]"
        yield body

        # 保存记忆
        await self.memory_add(session_id, "assistant", body)

    def _parse_response(self, text: str) -> tuple[str, str]:
        """解析完整回复，返回 (情绪标签, 剥离标签后的正文)。

        支持多段发言（``---`` 分隔）：逐段剥离各自的情绪标签，
        最终情绪取最后一段的标签（最后一段是用户最后看到的内容）。
        """
        segments = text.split(SEGMENT_SEPARATOR)
        last_emotion: Optional[str] = None
        cleaned: List[str] = []

        for segment in segments:
            emotion, body = self._parse_segment(segment)
            if emotion is not None:
                last_emotion = emotion
            cleaned.append(body)

        # 多段用换行重新拼接，保证前端可读
        body = "\n".join(seg.strip() for seg in cleaned if seg.strip())
        return (last_emotion or "idle"), body

    def _parse_segment(self, segment: str) -> tuple[Optional[str], str]:
        """解析单段发言，返回 (情绪标签或 None, 剥离标签后的正文)。"""
        match = EMOTION_PATTERN.search(segment)
        if match:
            emotion = match.group(1).lower()
            if emotion in VALID_EMOTIONS:
                body = EMOTION_PATTERN.sub("", segment).strip()
                return emotion, body
        return None, segment.strip()

    def _extract_emotion(self, text: str) -> Optional[str]:
        """从回复中提取情绪标签（取最后一段的标签，无标签返回 None）。"""
        segments = text.split(SEGMENT_SEPARATOR)
        for segment in reversed(segments):
            emotion, _ = self._parse_segment(segment)
            if emotion is not None:
                return emotion
        return None

    def _strip_emotion(self, text: str) -> str:
        """移除回复中的所有情绪标签，返回干净正文。"""
        _, body = self._parse_response(text)
        return body

    def _strip_all_emotions(self, text: str) -> str:
        """移除所有情绪标签（用于事件回复，保留正文原样）。"""
        return EMOTION_PATTERN.sub("", text).strip()

    def _match_and_start_event(self) -> Optional[EventNode]:
        """匹配并启动事件，返回事件节点（无事件则返回 None）。"""
        event_id = self.events.match_event(self.mood.mood)
        if event_id is None:
            return None
        return self.events.start_event(event_id)

    async def _build_messages(self, user_input: str, session_id: str) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = [{"role": "system", "content": self._build_system_prompt()}]
        history = self.memory.get_history(session_id)
        messages.extend(history[-self.memory.max_history :])
        messages.append({"role": "user", "content": user_input})
        return messages

    async def _build_event_messages(self, node: EventNode, session_id: str) -> List[Dict[str, str]]:
        """构建事件回复的消息，将事件 prompt 注入 system。"""
        system = self._build_system_prompt()
        system += f"\n\n[系统事件指令] {node.prompt}"
        messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
        history = self.memory.get_history(session_id)
        messages.extend(history[-self.memory.max_history :])
        return messages

    def _build_system_prompt(self) -> str:
        return build_system_prompt(self.system_prompt, self.mood.mood, self.mood.get_label())

    async def _preheat(self) -> None:
        if self.corpus:
            await self.llm.simple_chat(f"请学习以下语料风格：\n{self.corpus}\n确认后回复'好的'")
        self._preheated = True

    async def memory_add(self, session_id: str, role: str, content: str) -> None:
        """保存记忆（短期滑动窗口）。"""
        self.memory.add(session_id, role, content)
