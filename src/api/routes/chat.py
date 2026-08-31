"""对话相关路由。"""

from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from src.api.dependencies import (
    default_request_context,
    ensure_tools_synced,
    get_capability_snapshot,
    get_dialogue_query_service,
    get_orchestrator,
)
from src.api.schemas import ChatRequest, MoodResponse
from src.application.orchestrator import ConversationOrchestrator
from src.application.query_service import DialogueQueryService

router = APIRouter()


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    orchestrator: ConversationOrchestrator = Depends(get_orchestrator),
) -> StreamingResponse:
    """对话接口（R2：经 ConversationOrchestrator；v1 保持 text/plain 兼容输出，R3/R4 迁移真流式/UIEvent）。"""

    async def generate() -> AsyncGenerator[str, None]:
        context = default_request_context(request.session_id)
        # R2 修复：能力快照依赖工具注册，必须先触发工具来源同步（懒连接；幂等 + 失败降级）
        await ensure_tools_synced()
        capabilities = get_capability_snapshot()
        async for event in orchestrator.handle(context, request.input, capabilities):
            if event.type == "message.delta" and event.payload.get("content"):
                yield str(event.payload["content"])

    return StreamingResponse(
        generate(),
        media_type="text/plain; charset=utf-8",
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
