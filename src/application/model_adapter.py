"""OpenAICompatibleModelAdapter（SPEC-010 §4）：包装当前 LLMClient，实现 ModelPort。

本模块是应用层中唯一允许直接依赖具体 LLMClient 的边界适配器；
其余应用用例只依赖 DialogueModelPort / ToolModelPort。
"""

from typing import AsyncIterator, Dict, List

from src.application.contracts import (
    DialogueModelPort,
    DialogueModelRequest,
    ExtractRequest,
    ExtractResult,
    ToolModelPort,
    ToolModelRequest,
    ToolModelResult,
)
from src.core.llm.client import LLMClient


class OpenAICompatibleModelAdapter(DialogueModelPort, ToolModelPort):
    """把现有 LLMClient（openai 兼容接口）适配为两个 ModelPort。"""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    async def stream_reply(self, request: DialogueModelRequest) -> AsyncIterator[str]:
        async for chunk in self._client.stream(request.messages):
            yield chunk

    async def structured_extract(self, request: ExtractRequest) -> ExtractResult:
        data = await self._client.extract_json(request.messages)
        return ExtractResult(data=data)

    async def complete_with_tools(self, request: ToolModelRequest) -> ToolModelResult:
        tools: List[Dict[str, object]] = request.tools
        result = await self._client.chat(request.messages, tools=tools or None)
        return ToolModelResult(
            content=result.get("content") or "",
            tool_calls=result.get("tool_calls") or [],
            finish_reason=result.get("finish_reason"),
        )
