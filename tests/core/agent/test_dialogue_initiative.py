"""流水线内主动触发测试：interrupt_reply 接管门（问题 2）与两路径上下文一致（问题 3）。"""

import pytest
from src.core.agent.dialogue import DialogueEngine
from src.core.agent.mood import MoodSystem
from src.core.agent.triggers import InitiativeTriggerMatcher
from src.core.character.card import InitiativeTrigger, TriggerCondition, parse_character_card
from src.core.memory.short_term import ShortTermMemory


class _PipelineLLM:
    """最小 LLM 桩：按 system 是否含"[系统主动指令]"区分 普通回复 / 触发器接管。"""

    async def stream(self, messages):
        system = messages[0]["content"]
        yield "TRIGGER_TAKEOVER" if "[系统主动指令]" in system else "NORMAL_BODY"

    async def simple_chat(self, user_input):
        return ""


def _matcher(interrupt: bool) -> InitiativeTriggerMatcher:
    return InitiativeTriggerMatcher(
        [
            InitiativeTrigger(
                id="t",
                condition=TriggerCondition(mood_min=0, mood_max=100, expression=None),
                probability=1.0,
                cooldown_minutes=0.0,
                prompt="引导",
                emotion="happy",
                interrupt_reply=interrupt,
            )
        ]
    )


def _engine(interrupt: bool) -> DialogueEngine:
    llm = _PipelineLLM()
    return DialogueEngine(
        llm_client=llm,
        memory=ShortTermMemory(max_history=10),
        mood=MoodSystem(),
        matcher=_matcher(interrupt),
        system_prompt="",
        corpus="",
        long_term=None,
        user_id="default",
    )


async def _collect(engine: DialogueEngine, session_id: str, text: str) -> str:
    chunks = []
    async for chunk in engine.chat_stream(text, session_id):
        chunks.append(chunk)
    return "".join(chunks)


@pytest.mark.asyncio
async def test_non_interrupt_trigger_keeps_normal_reply():
    """interrupt_reply=False（社交类）命中时不劫持，走普通回复（问题 2）。"""
    engine = _engine(interrupt=False)
    result = await _collect(engine, "default", "你好")
    assert "NORMAL_BODY" in result
    assert "TRIGGER_TAKEOVER" not in result


@pytest.mark.asyncio
async def test_interrupt_trigger_takes_over_reply():
    """interrupt_reply=True（紧急安抚类）命中时接管回复（问题 2）。"""
    engine = _engine(interrupt=True)
    result = await _collect(engine, "default", "你好")
    assert "TRIGGER_TAKEOVER" in result


@pytest.mark.asyncio
async def test_trigger_context_full_vars():
    """流水线触发器判定上下文含 mood / silence_seconds / message_count（与调度器一致，问题 3）。"""
    engine = _engine(interrupt=False)
    ctx = engine.trigger_context("default")
    assert set(ctx) == {"mood", "silence_seconds", "message_count"}
    assert ctx["message_count"] == 0
    assert ctx["silence_seconds"] >= 0


def test_parse_trigger_interrupt_reply_default_and_flag():
    """interrupt_reply 缺省 False，配置 true 解析为 True。"""
    default = parse_character_card({"initiative_triggers": [{"id": "a", "prompt": "p"}]})
    assert default.initiative_triggers[0].interrupt_reply is False

    flagged = parse_character_card(
        {"initiative_triggers": [{"id": "a", "prompt": "p", "interrupt_reply": True}]}
    )
    assert flagged.initiative_triggers[0].interrupt_reply is True
