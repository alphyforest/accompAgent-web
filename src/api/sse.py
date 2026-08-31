"""SSE 编码与 /api/chat/stream 协议协商（R4，SPEC-050 §2/§11）。"""

from typing import AsyncIterator

from src.application.contracts import UIEvent


def wants_sse(accept: str) -> bool:
    """客户端是否请求 SSE（Accept: text/event-stream）。"""
    return "text/event-stream" in (accept or "")


def encode_sse(event: UIEvent) -> str:
    """单个 UIEvent -> SSE frame（id / event / data）。"""
    return (
        f"id: {event.event_id}\n"
        f"event: {event.type}\n"
        f"data: {event.model_dump_json()}\n\n"
    )


async def legacy_chunks(events: AsyncIterator[UIEvent]) -> AsyncIterator[str]:
    """LegacyDialogueAdapter（文本通道）：事件流重组为旧 [[EMOTION:]]text 协议。

    只有后端边界存在（SPEC-050 §11）；新桌面客户端不允许解析 legacy 格式。
    """
    async for event in events:
        if event.type == "message.delta":
            text = event.payload.get("text", "")
            if text:
                yield str(text)
