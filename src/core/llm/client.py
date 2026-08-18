"""DeepSeek API 封装，支持流式和非流式调用。"""

from typing import AsyncGenerator, Dict, List

from openai import AsyncOpenAI

from src.config.settings import Settings


class LLMClient:
    """DeepSeek LLM 客户端。"""

    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        self.model = settings.deepseek_model
        self.reasoning_effort = settings.reasoning_effort

    async def stream(self, messages: List[Dict[str, str]]) -> AsyncGenerator[str, None]:
        """流式对话，逐段返回生成内容。"""
        # openai 类型存根未按 stream=True 收窄返回类型，
        # 且 messages 的松散 dict 形式与强类型参数不匹配，属库边界。
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore[arg-type]
            stream=True,
            extra_body={"reasoning_effort": self.reasoning_effort},
        )
        async for chunk in stream:  # type: ignore[union-attr]
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def simple_chat(self, user_input: str) -> str:
        """非流式调用，返回完整回复。"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": user_input}],
            max_tokens=50,
        )
        return response.choices[0].message.content or ""
