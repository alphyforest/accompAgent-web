"""R4：SSE 编码与 legacy 文本通道测试（SPEC-050 §2/§11）。"""

from datetime import UTC, datetime

from src.api.sse import encode_sse, legacy_chunks, wants_sse
from src.application.contracts import UIEvent


def _event(event_type: str, payload: dict) -> UIEvent:
    return UIEvent(
        event_id="evt_1",
        request_id="req-1",
        trace_id="trace-1",
        type=event_type,
        timestamp=datetime.now(UTC),
        sequence=1,
        payload=payload,
    )


async def test_wants_sse():
    assert wants_sse("text/event-stream") is True
    assert wants_sse("application/json") is False
    assert wants_sse("") is False


def test_encode_sse_frame():
    frame = encode_sse(_event("message.delta", {"message_id": "m1", "text": "你好"}))
    lines = frame.split("\n")
    assert lines[0] == "id: evt_1"
    assert lines[1] == "event: message.delta"
    assert lines[2].startswith("data: {")
    assert frame.endswith("\n\n")
    import json

    payload = json.loads(lines[2][6:])
    assert payload["type"] == "message.delta"
    assert payload["payload"]["text"] == "你好"


async def test_legacy_chunks_rebuilds_text_protocol():
    events = [
        _event("message.delta", {"message_id": "m1", "text": "[[EMOTION:happy]]", "emotion_mark": True}),
        _event("message.delta", {"message_id": "m1", "text": "你好呀~"}),
    ]
    async def _async_events():
        for event in events:
            yield event

    chunks = [chunk async for chunk in legacy_chunks(_async_events())]
    assert chunks == ["[[EMOTION:happy]]", "你好呀~"]
