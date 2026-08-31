"""对话相关路由。"""

from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from src.api.dependencies import (
    default_request_context,
    ensure_tools_synced,
    get_capability_snapshot,
    get_dialogue_query_service,
    get_orchestrator,
)
from src.api.schemas import ChatRequest, MoodResponse
from src.api.sse import encode_sse, legacy_chunks, wants_sse
from src.application.orchestrator import ConversationOrchestrator
from src.application.query_service import DialogueQueryService

router = APIRouter()


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    raw: Request,
    orchestrator: ConversationOrchestrator = Depends(get_orchestrator),
) -> StreamingResponse:
    """对话接口（R4：UIEvent v1 + SSE；兼容旧文本通道）。

    - Accept: text/event-stream → SSE（SPEC-050 §2，前端主用）
    - 其他 Accept → LegacyDialogueAdapter 文本通道（SPEC-050 §11，迁移兼容）
    """

    async def generate() -> AsyncGenerator[str, None]:
        # 工具来源懒连接必须先于能力快照（R2 接缝守护，test_tool_sync_entry.py）
        await ensure_tools_synced()
        context = default_request_context(request.session_id)
        capabilities = get_capability_snapshot()
        events = orchestrator.handle(context, request.input, capabilities)
        if wants_sse(raw.headers.get("accept", "")):
            async for event in events:
                yield encode_sse(event)
        else:
            async for chunk in legacy_chunks(events):
                yield chunk

    use_sse = wants_sse(raw.headers.get("accept", ""))
    return StreamingResponse(
        generate(),
        media_type="text/event-stream; charset=utf-8" if use_sse else "text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/mood", response_model=MoodResponse)
async def get_mood_value(service: DialogueQueryService = Depends(get_dialogue_query_service)) -> MoodResponse:
    """查询当前气氛值（应用层查询服务）。"""
    snapshot = service.get_mood()
    return MoodResponse(mood=snapshot.mood, label=snapshot.label)
