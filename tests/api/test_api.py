"""API 路由单元测试（不连接真实 API）。"""

import pytest
from fastapi.testclient import TestClient
from src.api.app import app
from src.api.dependencies import get_engine

client = TestClient(app)


def test_get_status():
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "mood" in data
    assert "mood_label" in data


def test_get_mood():
    response = client.get("/api/mood")
    assert response.status_code == 200
    data = response.json()
    assert "mood" in data
    assert "label" in data


def test_reset():
    response = client.post("/api/reset")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_event_trigger_invalid_keyword():
    response = client.post("/api/event/trigger", json={"keyword": "不存在"})
    assert response.status_code == 200
    assert response.json() == {"detail": "未找到匹配的事件"}


def test_event_trigger_valid():
    response = client.post("/api/event/trigger", json={"keyword": "excited"})
    assert response.status_code == 200
    data = response.json()
    assert data["node_id"] == "excited"
    assert "emotion" in data
    assert "prompt" in data


def test_chat_stream_validation():
    # 空输入应返回 422
    response = client.post("/api/chat/stream", json={"input": ""})
    assert response.status_code == 422


def test_get_character():
    """角色卡下发（改动三）：立绘映射/默认情绪来自后端角色卡。"""
    data = client.get("/api/character").json()
    assert data["meta"]["id"] == "elysia"
    assert data["meta"]["name"] == "爱莉希雅"
    assert "happy" in data["portrait_map"]
    assert data["default_emotion"] == "idle"


def test_get_character_init_state():
    """角色卡下发含初始状态（init_state 已接线，非死配置）：接口透出 init_state。"""
    data = client.get("/api/character").json()
    assert data["init_state"]["mood"] == 0
    assert data["init_state"]["emotion"] == "idle"


def test_engine_mood_seeded_from_card_init_state():
    """回归（高危复查项）：引擎气氛值以角色卡 init_state.mood 初始化（不再恒为 0）。"""
    engine = get_engine()
    assert engine.card is not None
    # 引擎初始气氛值必须与角色卡声明的初始状态一致（防回退为无参 MoodSystem()）
    assert engine.mood.mood == engine.card.init_state.mood
    assert engine.init_emotion == engine.card.init_state.emotion


def test_get_initiative_empty():
    """主动发言接口：未触发时为合法空列表。"""
    response = client.get("/api/initiative")
    assert response.status_code == 200
    assert response.json() == []


# ================================================================ 第二阶段：记忆管理 API


@pytest.mark.asyncio
async def test_memory_api_list_correct_delete():
    """记忆列表 / 纠正（confirmed=1）/ 单条删除 / 404。"""
    client.post("/api/reset", json={"level": "all"})
    engine = get_engine()
    assert engine.long_term is not None
    record = await engine.long_term.save_memory("default", "fact", "city", "上海", importance=6, confirmed=0)

    data = client.get("/api/memory").json()
    assert data["user_id"] == "default"
    assert "fact" in data["groups"]
    item = data["groups"]["fact"][0]
    assert item["confirmed"] == 0

    resp = client.post(f"/api/memory/{item['id']}/correct", json={"value": "北京"})
    assert resp.status_code == 200
    assert resp.json()["value"] == "北京"
    assert resp.json()["confirmed"] == 1

    resp = client.delete(f"/api/memory/{item['id']}")
    assert resp.status_code == 200
    assert "fact" not in client.get("/api/memory").json()["groups"]

    assert client.delete(f"/api/memory/{item['id']}").status_code == 404
    assert client.post(f"/api/memory/{record.id}/correct", json={"value": "x"}).status_code == 404


@pytest.mark.asyncio
async def test_reset_levels_via_api():
    """三档清除 API：history 保留身份记忆；all 全部清空；非法 level 422。"""
    client.post("/api/reset", json={"level": "all"})
    engine = get_engine()
    assert engine.long_term is not None
    await engine.long_term.save_memory("default", "profile", "user_name", "小林", importance=8, confirmed=1)
    await engine.long_term.save_summary("s1", ["话题"], [], "有点累")

    # 档2 history：清摘要，保留身份
    resp = client.post("/api/reset", json={"level": "history"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert client.get("/api/summaries").json() == []
    assert "profile" in client.get("/api/memory").json()["groups"]

    # 档3 all：全部清空
    resp = client.post("/api/reset", json={"level": "all"})
    assert resp.status_code == 200
    assert client.get("/api/memory").json()["groups"] == {}

    # 非法 level：422
    assert client.post("/api/reset", json={"level": "bogus"}).status_code == 422

    # 无 body 兼容旧前端：默认档1 session
    assert client.post("/api/reset").status_code == 200


@pytest.mark.asyncio
async def test_summaries_api():
    """历史会话摘要列表。"""
    client.post("/api/reset", json={"level": "all"})
    engine = get_engine()
    assert engine.long_term is not None
    await engine.long_term.save_summary("s1", ["数学"], ["周末去公园"], "有点累")
    summaries = client.get("/api/summaries").json()
    assert len(summaries) == 1
    assert summaries[0]["session_id"] == "s1"
    assert summaries[0]["topics"] == ["数学"]
