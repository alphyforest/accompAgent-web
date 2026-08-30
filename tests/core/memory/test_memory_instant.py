"""即时抽取（方案 B：关键字触发的 function-calling 实时写入）单元测试。

只 mock LLM 抽取（Fake LLM 模式，rules.md §14.7），禁止连真实 API。
"""

import asyncio
from typing import Any, Dict, List

import pytest
from src.core.agent.dialogue import DialogueEngine, keyword_matches
from src.core.agent.mood import MoodSystem
from src.core.memory.long_term import LongTermMemory
from src.core.memory.short_term import ShortTermMemory


class FakeExtractLLM:
    """仅用于即时抽取路径的最小 LLM 桩：只实现 extract_user_facts。"""

    def __init__(self, facts: List[Dict[str, Any]]):
        self.facts = facts
        self.called = False
        self.last_transcript = ""

    async def extract_user_facts(self, messages: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        self.called = True
        self.last_transcript = messages[-1]["content"]
        return self.facts


def _make_engine(db_path: str, llm, instant_keywords=None) -> DialogueEngine:
    return DialogueEngine(
        llm_client=llm,
        memory=ShortTermMemory(max_history=10),
        mood=MoodSystem(),
        long_term=LongTermMemory(db_path),
        user_id="default",
        instant_enabled=True,
        instant_keywords=instant_keywords,
    )


async def _wait_tasks(engine: DialogueEngine) -> None:
    """让后台即时抽取任务跑完。"""
    for _ in range(100):
        if not engine._instant_tasks or all(t.done() for t in engine._instant_tasks):
            await asyncio.sleep(0)
            return
        await asyncio.sleep(0.01)
    await asyncio.sleep(0)


def test_keyword_matches():
    """关键字预筛：命中任一返回 True，空文本/无关文本返回 False。"""
    assert keyword_matches("我叫小林", ["我叫", "我的"]) is True
    assert keyword_matches("吃了吗", ["我叫", "我的"]) is False
    assert keyword_matches("", ["我叫"]) is False
    assert keyword_matches("我在准备考研", ["我在准备"]) is True


@pytest.mark.asyncio
async def test_instant_extract_persists_on_keyword(tmp_path):
    """命中关键字时后台抽取并即时落库（用户画像写入 user_memory）。"""
    llm = FakeExtractLLM(
        facts=[{"category": "profile", "key": "user_name", "value": "小林", "importance": 8}]
    )
    db_path = str(tmp_path / "m.db")
    engine = _make_engine(db_path, llm)
    await engine.memory_add("s1", "user", "我叫小林")
    await engine.long_term.init_db()

    engine._maybe_instant_extract("s1", "我叫小林")
    await _wait_tasks(engine)

    rows = await engine.long_term.list_memory("default")
    assert llm.called is True
    assert len(rows) == 1
    assert rows[0].category == "profile"
    assert rows[0].key == "user_name"
    assert rows[0].value == "小林"


@pytest.mark.asyncio
async def test_instant_extract_skips_without_keyword(tmp_path):
    """未命中关键字时不触发抽取、不落库。"""
    llm = FakeExtractLLM(facts=[{"category": "fact", "key": "x", "value": "y", "importance": 5}])
    db_path = str(tmp_path / "m.db")
    engine = _make_engine(db_path, llm)
    await engine.memory_add("s1", "user", "早上好")
    await engine.long_term.init_db()

    engine._maybe_instant_extract("s1", "早上好")
    await _wait_tasks(engine)

    assert llm.called is False
    assert await engine.long_term.list_memory("default") == []


@pytest.mark.asyncio
async def test_instant_extract_disabled_skips(tmp_path):
    """关闭即时抽取时不触发。"""
    llm = FakeExtractLLM(facts=[{"category": "fact", "key": "x", "value": "y", "importance": 5}])
    db_path = str(tmp_path / "m.db")
    engine = _make_engine(db_path, llm)
    engine.instant_enabled = False
    await engine.memory_add("s1", "user", "我叫小林")
    await engine.long_term.init_db()

    engine._maybe_instant_extract("s1", "我叫小林")
    await _wait_tasks(engine)

    assert llm.called is False
    assert await engine.long_term.list_memory("default") == []
