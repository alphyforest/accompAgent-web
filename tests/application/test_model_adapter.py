"""OpenAICompatibleModelAdapter 测试：包装 LLMClient 实现 ModelPort（R1 边界适配器）。"""

from typing import Any, AsyncIterator, Dict, List, Optional

from src.application.contracts import DialogueModelRequest, ExtractRequest, ToolModelRequest
from src.application.model_adapter import OpenAICompatibleModelAdapter


class FakeLLM:
    def __init__(self) -> None:
        self.streamed: List[List[Dict[str, str]]] = []
        self.chatted: List[Any] = []
        self.extracted: List[List[Dict[str, str]]] = []

    async def stream(self, messages: List[Dict[str, str]]) -> AsyncIterator[str]:
        self.streamed.append(messages)
        yield "你"
        yield "好"

    async def chat(
        self,
        messages: List[Dict[str, str]],
        tools=None,
        tool_choice: str = "auto",
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        self.chatted.append((messages, tools))
        return {
            "content": "ok",
            "tool_calls": [{"id": "c1", "name": "now", "arguments": {}}],
            "finish_reason": "tool_calls",
        }

    async def extract_json(self, messages: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
        self.extracted.append(messages)
        return {"topics": ["t"]}


async def test_stream_reply_delegates_to_llm_stream():
    fake = FakeLLM()
    adapter = OpenAICompatibleModelAdapter(fake)  # type: ignore[arg-type]
    chunks = [c async for c in adapter.stream_reply(DialogueModelRequest(messages=[{"role": "user", "content": "hi"}]))]
    assert chunks == ["你", "好"]
    assert fake.streamed == [[{"role": "user", "content": "hi"}]]


async def test_structured_extract_delegates_to_llm_extract_json():
    fake = FakeLLM()
    adapter = OpenAICompatibleModelAdapter(fake)  # type: ignore[arg-type]
    result = await adapter.structured_extract(ExtractRequest(messages=[{"role": "user", "content": "我叫A"}]))
    assert result.data == {"topics": ["t"]}


async def test_complete_with_tools_delegates_to_llm_chat():
    fake = FakeLLM()
    adapter = OpenAICompatibleModelAdapter(fake)  # type: ignore[arg-type]
    result = await adapter.complete_with_tools(
        ToolModelRequest(messages=[{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    )
    assert result.content == "ok"
    assert result.tool_calls[0]["name"] == "now"
    assert result.finish_reason == "tool_calls"
