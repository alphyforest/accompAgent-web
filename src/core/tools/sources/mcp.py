"""MCP stdio 适配器（规格 §6）：协议翻译，不含业务规则。

- 子进程：node <tsx CLI 入口> mcp/server.ts（Windows 避免直接 spawn .cmd）
- 生命周期：懒连接（首次 list/call 时连接）；统一启停归 lifespan（close）
- stdout 只走协议；server 日志经 stderr 透传
- schema 白名单翻译：enum / default / format(datetime) 逐项校验，不支持项跳过并告警
- 进程级故障：failed 标记 + 回调禁用其工具，不崩服务
"""

import asyncio
import os
import re
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.core.tools.spec import ToolError, ToolSpec
from src.utils.logger import logger

# schema 白名单（规格 §6）：只允许这些键直通；其余跳过并告警（禁止静默吞掉）
# （2026-08-30 复查扩表：zod 常见组合/约束键 anyOf/oneOf/allOf/pattern/minLength/
#   minimum/maximum/minItems/maxItems/const 已在真实联调中出现，丢弃会让模型拿不到
#   字段结构；白名单机制不变——这些键的值仍会被递归过滤校验。）
_SCHEMA_ALLOWED_KEYS = frozenset({
    "type",
    "description",
    "properties",
    "required",
    "items",
    "enum",
    "default",
    "format",
    "additionalProperties",
    "anyOf",
    "oneOf",
    "allOf",
    "pattern",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "const",
})
# format 白名单：时间字段一律 ISO 8601 带偏移（date 型为 YYYY-MM-DD）；uuid 为 id 字段常见标注
_FORMAT_ALLOWED = frozenset({"datetime", "date", "date-time", "uuid"})

# 描述补齐（协议翻译层面的文档约束，规格 §3：日历类必含时间写法与查重语义）
_ISO_NOTE = (
    "时间参数一律使用 ISO 8601 带时区偏移格式（如 2026-08-29T09:00:00+08:00），"
    "日期参数使用 YYYY-MM-DD。"
)
_CREATE_DEDUP_NOTE = "创建前应先调用 list_events 按 title+start 查重，避免重复日程。"


def sanitize_mcp_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """按白名单递归过滤 MCP inputSchema（类型已由工具来源保证为 dict）。"""
    result = _sanitize_node(schema, "$")
    assert isinstance(result, dict)
    return result


def _sanitize_node(node: Any, path: str) -> Any:
    """递归过滤：不支持的 schema 键跳过并告警；format 仅放行白名单值。

    注意：properties 里的键是字段名而非 schema 关键字，必须原样保留，
    只对其值递归过滤。
    """
    if isinstance(node, dict):
        out: Dict[str, Any] = {}
        for key, value in node.items():
            if key == "properties":
                out[key] = {
                    name: _sanitize_node(child, f"{path}.{key}.{name}") for name, child in value.items()
                }
                continue
            if key not in _SCHEMA_ALLOWED_KEYS:
                logger.warning("MCP schema 跳过不支持键 path={} key={}", path, key)
                continue
            if key == "format" and value not in _FORMAT_ALLOWED:
                logger.warning("MCP schema 跳过不支持 format path={} value={}", path, value)
                continue
            out[key] = _sanitize_node(value, f"{path}.{key}")
        return out
    if isinstance(node, list):
        return [_sanitize_node(item, f"{path}[]") for item in node]
    return node


def _is_read_only(tool: Any) -> bool:
    """读取 MCP readOnlyHint（annotations 可能为 dict 或对象，防御性取值）。"""
    annotations = getattr(tool, "annotations", None)
    if isinstance(annotations, dict):
        return bool(annotations.get("readOnlyHint"))
    hint = getattr(annotations, "readOnlyHint", None) if annotations is not None else None
    return bool(hint)


def _content_text(content: Any) -> str:
    """把 MCP 返回的 content 块拼成协议文本（stdout 只走协议，此处仅取数据）。"""
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for block in content:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
    return "\n".join(part for part in parts if part)


# ISO 带时区偏移的日期时间（如 2026-08-29T09:00:00+08:00）或已带 Z 后缀
_ISO_OFFSET_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)$")


def _normalize_datetime_arguments(value: Any) -> Any:
    """协议级时区归一化：带偏移 ISO 时间转 UTC Z 后缀。

    Agenda MCP Server 的 zod datetime() 仅接受 UTC（Z 后缀），而向模型暴露的时间
    写法是带时区偏移（规格 §8）。此处在适配器边界做 wire 格式翻译——只转换时间
    字符串，不掺任何业务规则（如课程/工作日判断）。
    """
    if isinstance(value, dict):
        return {key: _normalize_datetime_arguments(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_datetime_arguments(item) for item in value]
    if isinstance(value, str) and _ISO_OFFSET_PATTERN.match(value) and not value.endswith("Z"):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return value
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


def _enrich_description(name: str, description: str) -> str:
    """在协议翻译层补齐工具的文档要求（ISO 时间写法；create 查重语义）。"""
    parts = [description.strip()]
    parts.append(_ISO_NOTE)
    if name == "create_event":
        parts.append(_CREATE_DEDUP_NOTE)
    return " ".join(part for part in parts if part)


class McpToolSource:
    """MCP stdio 工具来源（懒连接、可 close、仅做协议翻译）。"""

    def __init__(
        self,
        command: str,
        args: List[str],
        *,
        env: Optional[Dict[str, str]] = None,
        name: str = "mcp",
        session_factory: Optional[Callable[[], Awaitable[Any]]] = None,
        connect_timeout: float = 15.0,
        call_timeout: float = 30.0,
        on_unavailable: Optional[Callable[[List[str]], None]] = None,
    ) -> None:
        self.command = command
        self.args = list(args)
        self._env = dict(os.environ)
        if env:
            self._env.update(env)
        self.name = name
        self._session_factory = session_factory
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self._on_unavailable = on_unavailable
        self._stack: Optional[AsyncExitStack] = None
        self._session: Optional[Any] = None
        self.failed = False
        self.tool_names: List[str] = []

    async def start(self) -> None:
        """预留显式启动点；真实连接为懒加载（首次 list/call 触发）。"""
        self.failed = False

    async def close(self) -> None:
        """关闭连接并释放子进程（幂等；异常仅记录）。"""
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP 来源 {} 关闭异常 err={}", self.name, exc)
        self._session = None
        self._stack = None
        self.failed = True

    async def _ensure_connected(self) -> None:
        """懒连接；失败标记 failed 并回落（不崩服务）。"""
        if self.failed:
            raise ToolError(code="tool_source_unavailable", user_message="工具服务暂不可用，请稍后再试")
        if self._session is not None:
            return
        try:
            if self._session_factory is not None:
                session = await self._session_factory()
            else:
                params = StdioServerParameters(command=self.command, args=self.args, env=self._env)
                self._stack = AsyncExitStack()
                transport = await self._stack.enter_async_context(stdio_client(params))
                read, write = transport
                session = await self._stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
            self._session = session
        except Exception as exc:  # noqa: BLE001 - 连接失败仅禁用来源，不崩服务
            self.failed = True
            await self.close()
            logger.warning("MCP 来源 {} 连接失败 err={}", self.name, exc)
            raise ToolError(code="tool_source_unavailable", user_message="工具服务暂不可用，请稍后再试") from exc

    async def list_tools(self) -> List[Dict[str, Any]]:
        """list_tools → 规整化工具信息（协议层）。"""
        await self._ensure_connected()
        assert self._session is not None
        result = await asyncio.wait_for(self._session.list_tools(), timeout=self.connect_timeout)
        tools: List[Dict[str, Any]] = []
        for tool in result.tools:
            tools.append({
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": sanitize_mcp_schema(tool.inputSchema),
                "read_only": _is_read_only(tool),
            })
        self.tool_names = [tool["name"] for tool in tools]
        return tools

    def build_spec(self, info: Dict[str, Any]) -> ToolSpec:
        """把规整化工具信息翻译为 ToolSpec（executable 闭包路由到本来源 call_tool）。"""
        name = str(info["name"])
        source = self

        async def execute(args: Dict[str, Any]) -> Dict[str, Any]:
            return await source.call_tool(name, args)

        return ToolSpec(
            name=name,
            description=_enrich_description(name, str(info.get("description") or "")),
            input_schema=info["input_schema"],
            executable=execute,
            read_only=bool(info.get("read_only", False)),
        )

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """call_tool → 结构化结果；协议错误/进程故障归一化为 ToolError。"""
        await self._ensure_connected()
        assert self._session is not None
        wire_arguments = _normalize_datetime_arguments(arguments)
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(name, wire_arguments), timeout=self.call_timeout
            )
        except (EOFError, BrokenPipeError, ConnectionError, OSError) as exc:
            # 进程级故障：标记不可用并禁用来源工具，后续调用快速失败
            self.failed = True
            await self.close()
            if self._on_unavailable is not None:
                self._on_unavailable(self.tool_names)
            logger.warning("MCP 来源 {} 进程故障 name={} err={}", self.name, name, exc)
            raise ToolError(code="tool_source_unavailable", user_message="工具服务连接已断开，请稍后再试") from exc
        except TimeoutError:
            raise ToolError(code="tool_timeout", user_message="工具执行超时，请稍后重试") from None
        text = _content_text(result.content)
        if getattr(result, "isError", False):
            raise ToolError(code="mcp_tool_error", user_message=text or "工具执行出错")
        return {"content": text}

