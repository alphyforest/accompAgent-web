"""API 路由单元测试（不连接真实 API）。"""

from fastapi.testclient import TestClient
from src.api.app import app

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
