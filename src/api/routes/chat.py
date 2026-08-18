"""对话相关路由。"""

from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from src.api.dependencies import get_engine, get_mood
from src.api.schemas import ChatRequest, MoodResponse
from src.core.agent.dialogue import DialogueEngine
from src.core.agent.mood import MoodSystem

router = APIRouter()


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, engine: DialogueEngine = Depends(get_engine)) -> StreamingResponse:
    """SSE 流式对话接口。"""

    async def generate() -> AsyncGenerator[str, None]:
        async for chunk in engine.chat_stream(request.input, request.session_id):
            yield chunk

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
async def get_mood_value(mood: MoodSystem = Depends(get_mood)) -> MoodResponse:
    """查询当前气氛值。"""
    return MoodResponse(mood=mood.mood, label=mood.get_label())
