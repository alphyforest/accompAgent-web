"""DeepSeek API 封装，支持流式、非流式与 JSON 结构化抽取调用。"""

import json
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

from openai import AsyncOpenAI

from src.config.settings import Settings

# 抽取响应外围可能出现的代码围栏，剥离后按 JSON 解析
_CODE_FENCE_PATTERN = re.compile(r"^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$", re.MULTILINE)

# 即时抽取注册的函数工具名（方案 B：function calling 实时写入）
MEMORY_TOOL_NAME = "save_user_memory"


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

    async def chat(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """非流式对话，支持函数调用（ToolLoop 运行时用，规格 §5）。

        返回规整化消息：{"content", "tool_calls", "finish_reason"}；
        tool_calls 为 [{"id", "name", "arguments"}]（arguments 已解析为 dict）。
        """
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "extra_body": {"reasoning_effort": self.reasoning_effort},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = await self.client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        raw_calls = getattr(message, "tool_calls", None) or []
        tool_calls: List[Dict[str, Any]] = []
        for call in raw_calls:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except ValueError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(
                {"id": call.id or "", "name": call.function.name or "", "arguments": arguments}
            )
        return {
            "content": message.content or "",
            "tool_calls": tool_calls,
            "finish_reason": response.choices[0].finish_reason,
        }

    async def simple_chat(self, user_input: str) -> str:
        """非流式调用，返回完整回复。"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": user_input}],
            max_tokens=50,
        )
        return response.choices[0].message.content or ""

    async def extract_json(self, messages: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
        """结构化抽取：要求模型输出 JSON 对象，解析失败返回 None（调用方负责降级）。

        使用 response_format={"type": "json_object"} 约束输出，
        兼容 openai 1.50 与 2.x 及 DeepSeek 兼容接口。
        """
        try:
            # messages 松散 dict 形式与 openai 强类型消息参数不匹配（call-overload），属库边界
            response = await self.client.chat.completions.create(  # type: ignore[call-overload]
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                max_tokens=1024,
            )
        except Exception:
            return None
        content = response.choices[0].message.content or ""
        cleaned = _CODE_FENCE_PATTERN.sub("", content).strip()
        try:
            data = json.loads(cleaned)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        return data

    async def extract_user_facts(self, messages: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """函数调用抽取用户画像/事实（方案 B 实时写入）。

        注册 \x60\x60save_user_memory\x60\x60 工具，LLM 自行决定要保存哪些用户明确表达的信息，
        返回规范化的事实列表 [{"category","key","value","importance"}]；
        失败或无工具调用返回 []（调用方自行降级，不阻塞主对话）。
        """
        tools = [
            {
                "type": "function",
                "function": {
                    "name": MEMORY_TOOL_NAME,
                    "description": "保存用户主动表达的个人信息（身份/喜好/生活事实/边界/情感需求），供长期记忆使用。"
                    "category 取值 profile|interest|fact|boundary|need，importance 为 1~10，值越大越重要。"
                    "只保存用户明确表达的内容，禁止编造。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "facts": {
                                "type": "array",
                                "description": "要保存的用户信息列表，无则给空数组。",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "category": {
                                            "type": "string",
                                            "enum": ["profile", "interest", "fact", "boundary", "need"],
                                        },
                                        "key": {"type": "string", "description": "字段名，如 user_name / city"},
                                        "value": {"type": "string", "description": "信息内容"},
                                        "importance": {"type": "integer", "minimum": 1, "maximum": 10},
                                    },
                                    "required": ["category", "key", "value", "importance"],
                                },
                            }
                        },
                        "required": ["facts"],
                    },
                },
            }
        ]
        try:
            response = await self.client.chat.completions.create(  # type: ignore[call-overload]
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=1024,
            )
        except Exception:
            return []
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        facts: List[Dict[str, Any]] = []
        for call in tool_calls:
            if getattr(call.function, "name", None) != MEMORY_TOOL_NAME:
                continue
            try:
                args = json.loads(call.function.arguments or "{}")
            except ValueError:
                continue
            raw_facts = args.get("facts", []) if isinstance(args, dict) else []
            for item in raw_facts:
                if isinstance(item, dict):
                    facts.append(item)
        return facts
