"""对话引擎单元测试（使用 fake LLM，不调用真实 API）。"""

import time
from typing import Any, AsyncGenerator, Dict, List, Optional

import pytest
from src.core.agent.dialogue import DialogueEngine
from src.core.agent.mood import MoodSystem
from src.core.agent.triggers import InitiativeTriggerMatcher
from src.core.character.card import CharacterCard, InitiativeTrigger, TriggerCondition, load_character_card
from src.core.memory.long_term import LongTermMemory
from src.core.memory.short_term import ShortTermMemory
from src.core.tools.builtin import build_now_tool
from src.core.tools.registry import ToolRegistry

from tests.conftest import CHARACTER_CONFIG_DIR


class FakeLLMClient:
    """假 LLM 客户端，返回固定内容，可配置结构化抽取结果。"""

    def __init__(
        self,
        reply: str = "[情绪:happy]你好呀~",
        extract: Optional[Dict[str, Any]] = None,
        extract_error: bool = False,
    ):
        self.reply = reply
        self.extract = extract
        self.extract_error = extract_error
        self.calls: List[List[Dict[str, str]]] = []
        self.extract_calls: List[List[Dict[str, str]]] = []
        self.chat_calls: List[Dict[str, Any]] = []

    async def stream(self, messages: List[Dict[str, str]]) -> AsyncGenerator[str, None]:
        self.calls.append(messages)
        for ch in self.reply:
            yield ch

    async def simple_chat(self, user_input: str) -> str:
        return "好的"

    async def chat(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Fake：记录调用并返回固定内容（无工具调用）。"""
        self.chat_calls.append({"messages": messages, "tools": tools})
        return {"content": self.reply, "tool_calls": []}

    async def extract_json(self, messages: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
        self.extract_calls.append(messages)
        if self.extract_error:
            raise RuntimeError("fake 抽取失败")
        return self.extract


def build_engine(
    corpus: str = "",
    reply: str = "[情绪:happy]你好呀~",
    long_term: Optional[LongTermMemory] = None,
    segment_max: int = 30,
    idle_timeout: float = 3600.0,
    inject_top_k: int = 8,
    extract: Optional[Dict[str, Any]] = None,
    extract_error: bool = False,
    card: Optional[CharacterCard] = None,
    matcher: Optional[InitiativeTriggerMatcher] = None,
    tool_registry: Optional[ToolRegistry] = None,
    tool_rounds: int = 4,
    tool_call_timeout: float = 30.0,
    tool_overall_timeout: float = 120.0,
) -> DialogueEngine:
    if card is None:
        card = load_character_card(CHARACTER_CONFIG_DIR)  # 默认用真实角色卡
    if matcher is None:
        matcher = InitiativeTriggerMatcher([])  # 默认空匹配器：普通回复用例确定性
    return DialogueEngine(
        llm_client=FakeLLMClient(reply=reply, extract=extract, extract_error=extract_error),
        memory=ShortTermMemory(max_history=10),
        mood=MoodSystem(),
        card=card,
        matcher=matcher,
        system_prompt="你是一个陪伴角色",
        corpus=corpus,
        long_term=long_term,
        segment_max=segment_max,
        idle_timeout=idle_timeout,
        inject_top_k=inject_top_k,
        tool_registry=tool_registry,
        tool_rounds=tool_rounds,
        tool_call_timeout=tool_call_timeout,
        tool_overall_timeout=tool_overall_timeout,
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


def test_emotion_tag_without_colon():
    """容错：模型漏写冒号的情绪标签 [情绪happy] 也能被识别并剥离。"""
    engine = build_engine()
    assert engine._parse_segment("[情绪happy]你好") == ("happy", "你好")
    assert engine._parse_segment("正文[情绪happy]残留") == ("happy", "正文残留")


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


# ================================================================ 第二阶段：惰性总结

FAKE_EXTRACT = {
    "topics": ["数学题", "考研"],
    "open_plans": ["周末去公园"],
    "user_emotional_state": "有点累",
    "key_facts": [{"category": "fact", "key": "city", "value": "上海", "importance": 7}],
}


@pytest.mark.asyncio
async def test_lazy_summarize_by_count_threshold(tmp_path):
    """条数阈值触发：第 4 轮消息到达时总结前 3 轮，落库并清空开新段。"""
    db = LongTermMemory(str(tmp_path / "m.db"))
    engine = build_engine(long_term=db, segment_max=5, extract=FAKE_EXTRACT)
    for text in ["你好", "我住在上海", "最近在准备考研", "今天好累"]:
        async for _ in engine.chat_stream(text, "s1"):
            pass
    # 确定性等待后台总结任务完成（避免依赖 sleep 时序）
    if engine._summary_tasks:
        await engine._summary_tasks[-1]
    summary = await db.get_summary("s1")
    assert summary is not None
    assert "数学题" in summary.topics
    assert "周末去公园" in summary.open_plans
    assert summary.emotional_state == "有点累"
    fact = await db.get_memory_by("default", "fact", "city")
    assert fact is not None and fact.value == "上海" and fact.confirmed == 0
    assert len(engine.llm.extract_calls) == 1  # 仅触发一次
    # 开新段：buffer 只剩最后一轮消息
    assert len(engine._session_buffer["s1"]) <= 2


@pytest.mark.asyncio
async def test_relationship_milestone_not_persisted(tmp_path):
    """关系/里程碑线已掐掉：即使抽取返回 relationship/milestones，也不写入 user_memory（Bug 1）。"""
    db = LongTermMemory(str(tmp_path / "m.db"))
    engine = build_engine(
        long_term=db,
        extract={
            "topics": [],
            "open_plans": [],
            "user_emotional_state": None,
            "relationship": "恋人",
            "milestones": ["完成首次邀约"],
            "key_facts": [],
        },
    )
    await engine._run_summarize("s1", [{"role": "user", "content": "我们在一起了"}])
    assert await db.list_memory("default") == []


@pytest.mark.asyncio
async def test_should_summarize_by_idle_timeout(tmp_path):
    """空闲超时判定：超过 idle_timeout 即视为上一段会话结束。"""
    db = LongTermMemory(str(tmp_path / "m.db"))
    engine = build_engine(long_term=db, idle_timeout=60.0, extract=FAKE_EXTRACT)
    engine._session_buffer["s1"] = [{"role": "user", "content": "旧消息"}]
    engine._session_activity["s1"] = time.time() - 120
    assert engine._should_summarize("s1") is True
    await engine._run_summarize("s1", list(engine._session_buffer["s1"]))
    assert await db.get_summary("s1") is not None


@pytest.mark.asyncio
async def test_check_summarize_clears_buffer_and_runs_task(tmp_path):
    """_check_summarize 触发后台任务：buffer 清空、任务完成后摘要落库。"""
    db = LongTermMemory(str(tmp_path / "m.db"))
    engine = build_engine(long_term=db, segment_max=2, extract=FAKE_EXTRACT)
    engine._session_buffer["s1"] = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    engine._session_activity["s1"] = time.time()
    await engine._check_summarize("s1")
    assert "s1" not in engine._session_buffer  # 已清空开新段
    # 确定性等待后台总结任务完成（避免依赖 sleep 时序）
    assert engine._summary_tasks
    await engine._summary_tasks[-1]
    assert await db.get_summary("s1") is not None


@pytest.mark.asyncio
async def test_summarize_failure_keeps_buffer(tmp_path):
    """抽取失败降级：不抛异常、不影响对话，buffer 保留下次重试。"""
    db = LongTermMemory(str(tmp_path / "m.db"))
    engine = build_engine(long_term=db, extract_error=True)
    snapshot = [{"role": "user", "content": "旧内容"}]
    await engine._run_summarize("s1", snapshot)  # 不应抛异常
    assert engine._session_buffer["s1"] == snapshot  # buffer 保留
    assert len(engine.llm.extract_calls) == 1
    assert await db.get_summary("s1") is None


@pytest.mark.asyncio
async def test_memory_injection_top_k(tmp_path):
    """记忆注入：按 importance 取 top-k 拼入 system prompt，推测信息标注。"""
    db = LongTermMemory(str(tmp_path / "m.db"))
    for i in range(1, 11):
        await db.save_memory("default", "fact", f"key{i}", f"value{i}", importance=i, confirmed=0)
    engine = build_engine(long_term=db, inject_top_k=3)
    messages = await engine._build_messages("你好", "s1")
    system = messages[0]["content"]
    assert "关于用户你已知的信息" in system
    assert "value10" in system and "value9" in system and "value8" in system
    assert "value7" not in system  # 超出 top-k
    assert "推测" in system  # confirmed=0 标记推测


@pytest.mark.asyncio
async def test_memory_injection_confirmed_mark(tmp_path):
    """注入时 confirmed=1 的记忆标注为已确认（条目标注而非未确认）。"""
    db = LongTermMemory(str(tmp_path / "m.db"))
    await db.save_memory("default", "profile", "user_name", "小林", importance=8, confirmed=1)
    engine = build_engine(long_term=db)
    messages = await engine._build_messages("你好", "s1")
    system = messages[0]["content"]
    assert "小林（profile/user_name，已确认" in system  # 条目本身标注已确认
    assert "小林（profile/user_name，推测" not in system


@pytest.mark.asyncio
async def test_reset_memory_levels(tmp_path):
    """三档清除：session 保留他人摘要；history 清摘要留身份；all 全部清空。"""
    db = LongTermMemory(str(tmp_path / "m.db"))
    engine = build_engine(long_term=db)
    await db.save_memory("default", "profile", "user_name", "小林", importance=8, confirmed=1)
    await db.save_summary("s1", ["话题A"], [], None)
    await db.save_summary("other", ["别的话题"], [], None)
    engine.memory.add("s1", "user", "你好")
    engine._session_buffer["s1"] = [{"role": "user", "content": "你好"}]

    await engine.reset_memory("session", "s1")
    assert engine.memory.get_history("s1") == []
    assert "s1" not in engine._session_buffer
    assert await db.get_summary("s1") is None
    assert await db.get_summary("other") is not None  # 其他会话摘要保留
    assert await db.get_memory_by("default", "profile", "user_name") is not None  # 身份保留

    await engine.reset_memory("history", "s1")
    assert await db.get_summary("other") is None
    assert await db.get_memory_by("default", "profile", "user_name") is not None  # 身份保留

    await engine.reset_memory("all", "s1")
    assert await db.get_memory_by("default", "profile", "user_name") is None  # 忘记我


@pytest.mark.asyncio
async def test_emotion_parsing_config_driven():
    """情绪解析由角色卡驱动（改动二）：删/增标签、改默认情绪，解析行为跟随，无需改代码。"""
    card = load_character_card(CHARACTER_CONFIG_DIR)
    card.output_protocol.emotions = ["custom", "idle"]
    card.output_protocol.default_emotion = "custom"
    engine = build_engine(card=card)
    # happy 已从配置删除 → 不再识别，标签残留正文
    assert engine._parse_segment("[情绪:happy]你好") == (None, "[情绪:happy]你好")
    # 新增 custom 标签可被识别
    assert engine._parse_segment("[情绪:custom]你好") == ("custom", "你好")
    # 默认情绪跟随配置
    emotion, body = engine._parse_response("没有标签的正文")
    assert emotion == "custom"
    assert body == "没有标签的正文"


@pytest.mark.asyncio
async def test_pipeline_initiative_trigger_hit():
    """流水线内主动触发器"接管"命中（改动四·第一步）：interrupt_reply 触发器取代普通回复。"""
    trigger = InitiativeTrigger(
        id="always",
        emotion="surprised",
        probability=1.0,
        cooldown_minutes=0.0,
        prompt="主动开口提示",
        condition=TriggerCondition(mood_min=-100, mood_max=100),
        interrupt_reply=True,  # 仅"可接管"触发器在流水线内取代回复
    )
    engine = build_engine(matcher=InitiativeTriggerMatcher([trigger]))
    full = ""
    async for chunk in engine.chat_stream("你好", "s1"):
        full += chunk
    # 本次走了主动触发路径：情绪取触发器指定值 surprised（普通回复会解析为 happy）
    assert full.startswith("[[EMOTION:surprised]]")


@pytest.mark.asyncio
async def test_generate_initiative_emotion_override():
    """主动开口生成：触发器指定情绪优先于回复内标签。"""
    engine = build_engine(reply="[情绪:happy]嗨")
    trigger = InitiativeTrigger(id="t", emotion="sad", prompt="p", condition=TriggerCondition())
    text = await engine.generate_initiative(trigger, "s1")
    assert text.startswith("[[EMOTION:sad]]")
    assert "嗨" in text


@pytest.mark.asyncio
async def test_reset_all_resets_mood_and_state(tmp_path):
    """整体复位（改动五收拢）：清除记忆 + 复位气氛值。"""
    db = LongTermMemory(str(tmp_path / "m.db"))
    engine = build_engine(long_term=db)
    engine.mood.update("开心")
    engine._session_buffer["s1"] = [{"role": "user", "content": "x"}]
    await engine.reset_all("session", "s1")
    assert engine.mood.mood == 0
    assert "s1" not in engine._session_buffer


@pytest.mark.asyncio
async def test_proactive_message_source_tagged():
    """主动发言来源标记（rules.md §15.5）：主动消息计入 buffer 且 source=initiative，
    普通回复 source=reply，二者可区分。"""
    engine = build_engine()
    trigger = InitiativeTrigger(id="t", emotion="happy", prompt="p", condition=TriggerCondition())
    await engine.generate_initiative(trigger, "s1")
    async for _ in engine.chat_stream("你好", "s1"):
        pass
    buffer = engine._session_buffer["s1"]
    proactive = [m for m in buffer if m.get("source") == "initiative"]
    replies = [m for m in buffer if m.get("source") == "reply"]
    assert proactive and all(m["role"] == "assistant" for m in proactive)
    assert replies  # 普通回复带 reply 来源
    # 主动发言不应被当作 key_facts 来源：转写会标注 [主动]（此处仅验证 buffer 来源可区分）
    assert any(message.get("source") == "initiative" for message in buffer)


def test_engine_init_emotion_from_card():
    """回归（高危复查项）：引擎初始情绪取自角色卡 init_state.emotion，随配置生效。"""
    card = load_character_card(CHARACTER_CONFIG_DIR)
    card.init_state.emotion = "greet"
    engine = build_engine(card=card)
    assert engine.init_emotion == "greet"

# ================================================================ 第三阶段：工具循环（ToolLoop）委托


@pytest.mark.asyncio
async def test_chat_stream_uses_tool_loop_when_registry_present():
    """注册表存在时：对话走 ToolLoop（chat 调用），最终文本经情绪解析按既有协议输出。"""
    registry = ToolRegistry()
    registry.register(build_now_tool())
    engine = build_engine(reply="[情绪:happy]我查一下时间~", tool_registry=registry)
    full = ""
    async for chunk in engine.chat_stream("现在几点了", "s1"):
        full += chunk
    assert full.startswith("[[EMOTION:happy]]")
    assert "我查一下时间~" in full
    assert engine.llm.chat_calls  # 走了 chat（ToolLoop）
    history = engine.memory.get_history("s1")
    assert history[-1]["content"] == "我查一下时间~"


@pytest.mark.asyncio
async def test_chat_stream_falls_back_without_tools():
    """无注册表时：行为与现有一致（Stream），不调用 chat。"""
    engine = build_engine(reply="[情绪:happy]你好呀~")
    full = ""
    async for chunk in engine.chat_stream("你好", "s1"):
        full += chunk
    assert full.startswith("[[EMOTION:happy]]")
    assert not engine.llm.chat_calls


@pytest.mark.asyncio
async def test_build_system_prompt_injects_time_context_when_tools_enabled():
    """规格 §8：工具可用时 system prompt 必带当前时间上下文。"""
    registry = ToolRegistry()
    registry.register(build_now_tool())
    engine = build_engine(tool_registry=registry)
    prompt = await engine._build_system_prompt("s1")
    assert "[当前时间]" in prompt
    assert "当前时间:" in prompt and "+" in prompt  # ISO 8601 带偏移


@pytest.mark.asyncio
async def test_build_system_prompt_no_time_without_tools():
    engine = build_engine()
    prompt = await engine._build_system_prompt("s1")
    assert "当前时间:" not in prompt

