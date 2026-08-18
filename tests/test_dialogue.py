"""对话引擎单元测试（使用 fake LLM，不调用真实 API）。"""

from typing import AsyncGenerator, Dict, List

import pytest
from src.core.agent.dialogue import DialogueEngine
from src.core.agent.event import EventSystem
from src.core.agent.mood import MoodSystem
from src.core.memory.short_term import ShortTermMemory

from tests.conftest import EVENTS_CONFIG


class FakeLLMClient:
    """假 LLM 客户端，返回固定内容。"""

    def __init__(self, reply: str = "[情绪:happy]你好呀~"):
        self.reply = reply
        self.calls: List[List[Dict[str, str]]] = []

    async def stream(self, messages: List[Dict[str, str]]) -> AsyncGenerator[str, None]:
        self.calls.append(messages)
        for ch in self.reply:
            yield ch

    async def simple_chat(self, user_input: str) -> str:
        return "好的"


def build_engine(corpus: str = "", reply: str = "[情绪:happy]你好呀~") -> DialogueEngine:
    return DialogueEngine(
        llm_client=FakeLLMClient(reply=reply),
        memory=ShortTermMemory(max_history=10),
        mood=MoodSystem(),
        events=EventSystem(EVENTS_CONFIG),
        system_prompt="你是一个陪伴角色",
        corpus=corpus,
    )


@pytest.mark.asyncio
async def test_chat_stream_outputs_emotion_mark():
    engine = build_engine()
    chunks = []
    async for chunk in engine.chat_stream("你好", "s1"):
        chunks.append(chunk)
    full = "".join(chunks)
    # 首段为情绪标记，正文为剥离标签后的内容
    assert full.startswith("[[EMOTION:happy]]")
    assert "你好呀~" in full
    assert "[情绪:" not in full


@pytest.mark.asyncio
async def test_chat_stream_strips_emotion_tag():
    engine = build_engine(reply="[情绪:sad]我很难过")
    full = ""
    async for chunk in engine.chat_stream("你好", "s1"):
        full += chunk
    assert "[[EMOTION:sad]]" in full
    assert "我很难过" in full


@pytest.mark.asyncio
async def test_chat_stream_saves_memory():
    engine = build_engine()
    async for _ in engine.chat_stream("你好", "s1"):
        pass
    history = engine.memory.get_history("s1")
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "你好"}
    # assistant 记录的是剥离标签后的正文
    assert history[1]["content"] == "你好呀~"


def test_extract_emotion():
    engine = build_engine()
    assert engine._extract_emotion("[情绪:happy]你好") == "happy"
    assert engine._extract_emotion("[情绪：surprised]哇") == "surprised"
    assert engine._extract_emotion("没有标签的回复") is None


def test_extract_emotion_multi_segment():
    engine = build_engine()
    # 多段发言：取最后一段的情绪
    text = "[情绪:happy]嗨，想我了吗～♪---[情绪:idle]今天天气真好呢。"
    assert engine._extract_emotion(text) == "idle"


def test_strip_emotion():
    engine = build_engine()
    assert engine._strip_emotion("[情绪:happy]你好呀") == "你好呀"
    assert engine._strip_emotion("直接正文") == "直接正文"


def test_strip_emotion_multi_segment():
    engine = build_engine()
    # 多段发言：所有段的情绪标签都要被剥离
    text = "[情绪:happy]嗨，想我了吗～♪---[情绪:idle]今天天气真好呢。"
    body = engine._strip_emotion(text)
    assert "[情绪:" not in body
    assert "嗨，想我了吗～♪" in body
    assert "今天天气真好呢。" in body


@pytest.mark.asyncio
async def test_chat_stream_no_emotion_fallback_idle():
    engine = build_engine(reply="普通回复没有标签")
    full = ""
    async for chunk in engine.chat_stream("你好", "s1"):
        full += chunk
    assert "[[EMOTION:idle]]" in full
    assert "普通回复没有标签" in full
