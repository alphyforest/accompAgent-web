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
import subprocess
import time
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple

from anyio.streams.text import TextReceiveStream
from mcp import ClientSession, StdioServerParameters
from mcp.types import JSONRPCMessage

from src.core.tools.spec import GenericCapability, ToolError, ToolSpec
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


@asynccontextmanager
async def _stdio_client_capture_stderr(
    params: StdioServerParameters,
    source_name: str,
) -> AsyncIterator[Tuple[Any, Any]]:
    """stdio_client 替身：stderr 走管道并按行接入结构化日志（R5b，SPEC-030 §9）。

    mcp 1.0.0 的 stdio_client 固定 stderr=sys.stderr（无法检索），这里镜像其
    transport 语义（内存对象流 + stdin/stdout reader/writer），仅将 stderr 改为
    逐行 loguru 输出并带 source_id；stdout 仍只走 JSON-RPC 协议。
    """
    import anyio

    read_stream_writer: Any
    read_stream: Any
    write_stream: Any
    write_stream_reader: Any
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    process = await anyio.open_process(
        [params.command, *params.args],
        env=params.env,
        stderr=subprocess.PIPE,
    )

    async def stdout_reader() -> None:
        assert process.stdout is not None
        try:
            async with read_stream_writer:
                buffer = ""
                async for chunk in TextReceiveStream(process.stdout):
                    lines = (buffer + chunk).split("\n")
                    buffer = lines.pop()
                    for line in lines:
                        try:
                            message = JSONRPCMessage.model_validate_json(line)
                        except Exception as exc:  # noqa: BLE001 - 非法协议行转异常帧
                            await read_stream_writer.send(exc)
                            continue
                        await read_stream_writer.send(message)
        except Exception as exc:  # noqa: BLE001 - 流关闭属正常
            if not isinstance(exc, (anyio.ClosedResourceError, BrokenPipeError)):
                logger.debug("mcp_source {} stdout reader 结束 err={}", source_name, exc)

    async def stdin_writer() -> None:
        assert process.stdin is not None
        try:
            async with write_stream_reader:
                async for message in write_stream_reader:
                    payload = message.model_dump_json(by_alias=True, exclude_none=True)
                    await process.stdin.send((payload + "\n").encode())
        except Exception as exc:  # noqa: BLE001
            if not isinstance(exc, (anyio.ClosedResourceError, BrokenPipeError)):
                logger.debug("mcp_source {} stdin writer 结束 err={}", source_name, exc)

    async def stderr_reader() -> None:
        if process.stderr is None:
            return
        buffer = ""
        try:
            async for chunk in TextReceiveStream(process.stderr):
                buffer += chunk
                lines = buffer.split("\n")
                buffer = lines.pop()
                for line in lines:
                    stripped = line.strip()
                    if stripped:
                        # 结构化日志：带 source_id/stderr 标记，可在日志检索（PLAN-010 R5 验收）
                        logger.info("mcp_source stderr source={} line={}", source_name, stripped)
        except Exception as exc:  # noqa: BLE001 - 子进程退出后流关闭属正常
            logger.debug("mcp_source {} stderr reader 结束 err={}", source_name, exc)

    async with (
        anyio.create_task_group() as tg,
        process,
    ):
        tg.start_soon(stdout_reader)
        tg.start_soon(stdin_writer)
        tg.start_soon(stderr_reader)
        yield read_stream, write_stream


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
        initialize_timeout: float = 10.0,
        list_timeout: float = 30.0,
        call_timeout: float = 30.0,
        close_timeout: float = 3.0,
        backoff_seconds: float = 30.0,
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
        self.initialize_timeout = initialize_timeout
        self.list_timeout = list_timeout
        self.call_timeout = call_timeout
        self.backoff_seconds = backoff_seconds
        self._close_timeout = close_timeout
        self._connect_lock = asyncio.Lock()
        self._on_unavailable = on_unavailable
        self._stack: Optional[AsyncExitStack] = None
        self._session: Optional[Any] = None
        self.failed = False
        # R5b 来源状态机：uninitialized -> connecting -> ready / failed -> backoff -> connecting；ready/failed -> closed
        self.state = "uninitialized"
        self._retry_at = 0.0
        self.tool_names: List[str] = []

    async def start(self) -> None:
        """预留显式启动点；真实连接为懒加载（首次 list/call 触发）。"""
        self.failed = False

    def can_retry(self) -> bool:
        """退避判定（R5b）：closed 终态不可重连；failed 需过退避窗口。"""
        if self.state == "closed":
            return False
        if not self.failed:
            return True
        return time.time() >= self._retry_at

    async def _teardown(self) -> None:
        """释放连接与子进程引用（不修改状态/失败标记）。

        Ctrl+C 卡终端修复：子进程关闭加 wait_for 超时（close_timeout，默认 3s）。
        """
        stack = self._stack
        self._stack = None
        self._session = None
        if stack is not None:
            try:
                await asyncio.wait_for(stack.aclose(), timeout=self._close_timeout)
            except Exception as exc:  # noqa: BLE001 - 关闭异常/超时仅记录，不阻塞应用退出
                logger.warning("MCP 来源 {} 关闭异常/超时 err={}", self.name, exc)

    async def close(self) -> None:
        """关闭连接并释放子进程（幂等）；进入 closed 终态，不再重连。"""
        await self._teardown()
        self.failed = True
        self.state = "closed"

    def _mark_failed(self) -> None:
        """进入 failed 态：禁用快查 + 退避时间窗（R5b）。"""
        self.failed = True
        self.state = "failed"
        self._retry_at = time.time() + self.backoff_seconds

    async def _ensure_connected(self) -> None:
        """懒连接；失败标记 failed 并退避（不崩服务）。

        并发安全：连接建立全程持有 _connect_lock（双检），杜绝并发请求双启子进程
        （R4 实测：SSE 前端快速连发时双 spawn）。
        独立超时：connect 总预算 connect_timeout；initialize 独立 initialize_timeout。
        退避：failed 未到 _retry_at 时快速失败；到点后允许重连（ToolRuntime 同步驱动）。
        取消安全：连接/初始化被取消时不标记故障，清理半成品后向上传播。
        """
        if self.state == "closed":
            raise ToolError(code="tool_source_unavailable", user_message="工具服务已关闭，请重启后重试")
        if self.failed and not self.can_retry():
            raise ToolError(code="tool_source_unavailable", user_message="工具服务暂不可用，请稍后再试")
        if self._session is not None:
            return
        self.state = "connecting"
        async with self._connect_lock:
            if self._session is not None:
                self.state = "ready"
                return  # 等待者直接复用已建连接
            try:
                async with asyncio.timeout(self.connect_timeout):
                    if self._session_factory is not None:
                        session = await self._session_factory()
                    else:
                        params = StdioServerParameters(command=self.command, args=self.args, env=self._env)
                        self._stack = AsyncExitStack()
                        transport = await self._stack.enter_async_context(
                            _stdio_client_capture_stderr(params, self.name)
                        )
                        read, write = transport
                        session = await self._stack.enter_async_context(ClientSession(read, write))
                    # initialize 独立超时（初始化卡死不占用整段连接预算）
                    await asyncio.wait_for(session.initialize(), timeout=self.initialize_timeout)
                self._session = session
                self.failed = False
                self.state = "ready"
            except asyncio.CancelledError:
                # 取消不标记来源故障：清理半成品连接后向上传播（供 ASGI 正常处理断开）
                self.state = "uninitialized"
                await self._teardown()
                raise
            except TimeoutError:
                self._mark_failed()
                await self._teardown()
                logger.warning(
                    "MCP 来源 {} 连接/初始化超时（connect={}s init={}s）",
                    self.name,
                    self.connect_timeout,
                    self.initialize_timeout,
                )
                raise ToolError(code="tool_source_unavailable", user_message="工具服务连接超时，请稍后再试") from None
            except Exception as exc:  # noqa: BLE001 - 连接失败仅禁用来源，不崩服务
                self._mark_failed()
                await self._teardown()
                logger.warning("MCP 来源 {} 连接失败 err={}", self.name, exc)
                raise ToolError(code="tool_source_unavailable", user_message="工具服务暂不可用，请稍后再试") from exc

    async def list_tools(self) -> List[Dict[str, Any]]:
        """list_tools → 规整化工具信息（协议层）；list 独立超时 list_timeout。"""
        await self._ensure_connected()
        assert self._session is not None
        try:
            result = await asyncio.wait_for(self._session.list_tools(), timeout=self.list_timeout)
        except TimeoutError:
            raise ToolError(code="tool_timeout", user_message="工具列表获取超时，请稍后再试") from None
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

        read_only = bool(info.get("read_only", False))
        return ToolSpec(
            name=name,
            description=_enrich_description(name, str(info.get("description") or "")),
            input_schema=info["input_schema"],
            executable=execute,
            read_only=read_only,
            source_id=self.name,
            # agenda 来源按读写分类为 calendar.*；其他来源安全默认 generic（本地策略覆盖）
            capability=(
                "calendar.read" if read_only else "calendar.write" if self.name == "agenda" else GenericCapability
            ),
            risk_level="low",
            idempotency="natural" if not read_only else "none",
            # ADR-004：agenda 自动执行；其余来源写工具默认需确认（不设全局自动执行）
            confirmation_policy="never" if self.name == "agenda" else "conditional",
            timeout_seconds=30.0,
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
            # 进程级故障：标记 failed + 退避；禁用来源工具，后续调用快速失败
            self._mark_failed()
            await self._teardown()
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

