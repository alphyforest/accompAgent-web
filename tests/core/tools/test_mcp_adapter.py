"""MCP stdio 适配器与工具运行时单元测试（Fake transport / Fake session，不 spawn 进程）。"""

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from src.core.tools.backup import ensure_daily_backup
from src.core.tools.registry import ToolRegistry
from src.core.tools.runtime import ToolRuntime
from src.core.tools.sources.mcp import McpToolSource, _normalize_datetime_arguments, sanitize_mcp_schema
from src.core.tools.spec import ToolError, ToolSpec
from src.core.tools.tool_loop import ToolLoop


class FakeSession:
    """假 MCP 会话：list_tools / call_tool 行为可配置。"""

    def __init__(
        self,
        tools: Optional[List[Tool]] = None,
        list_error: Optional[Exception] = None,
        call_error: Optional[Exception] = None,
    ):
        self.tools = tools or []
        self.list_error = list_error
        self.call_error = call_error
        self.calls: List[tuple] = []
        self.list_calls = 0

    async def initialize(self) -> None:
        pass

    async def list_tools(self) -> ListToolsResult:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return ListToolsResult(tools=self.tools)

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> CallToolResult:
        self.calls.append((name, arguments))
        if self.call_error is not None:
            raise self.call_error
        if name == "boom_is_error":
            return CallToolResult(content=[TextContent(type="text", text="该日程不存在")], isError=True)
        return CallToolResult(content=[TextContent(type="text", text=json.dumps({"ok": True}))], isError=False)


def _make_factory(session: FakeSession) -> Any:
    async def factory() -> FakeSession:
        return session

    return factory


def _tool(name: str, read_only: bool = False, schema: Optional[Dict[str, Any]] = None) -> Tool:
    return Tool(
        name=name,
        description=f"{name} 描述",
        inputSchema=schema or {"type": "object", "properties": {}},
        annotations={"readOnlyHint": read_only},
    )


def test_sanitize_mcp_schema_whitelist():
    schema = {
        "type": "object",
        "properties": {
            "start": {
                "type": "string",
                "format": "datetime",
                "title": "开始时间",
                "example": "2026-08-29T09:00:00+08:00",
            },
            "category": {"type": "string", "enum": ["work", "study"], "default": "work"},
        },
        "required": ["start"],
        "$schema": "http://json-schema.org/draft-07/schema#",
        "additionalProperties": False,
    }
    out = sanitize_mcp_schema(schema)
    assert "$schema" not in out  # 不支持键跳过
    assert "title" not in out["properties"]["start"]  # 不支持键跳过
    assert "example" not in out["properties"]["start"]
    assert out["properties"]["start"]["format"] == "datetime"  # 白名单 format 保留
    assert out["properties"]["category"]["enum"] == ["work", "study"]
    assert out["properties"]["category"]["default"] == "work"
    assert out["additionalProperties"] is False


def test_sanitize_mcp_schema_rejects_unknown_format():
    out = sanitize_mcp_schema({"type": "string", "format": "uri"})
    assert "format" not in out  # 不支持 format 跳过


def test_sanitize_mcp_schema_keeps_common_constraint_keys():
    """复查扩表：zod 常见组合/约束键与 uuid format 不再被丢弃，白名单机制仍生效。"""
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "format": "uuid"},
            "title": {"type": "string", "minLength": 1, "maxLength": 100, "pattern": "^[^\\n]+$"},
            "count": {"type": "integer", "minimum": 0, "maximum": 100},
            "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10},
            "recurrence": {
                "anyOf": [
                    {"type": "object", "properties": {"frequency": {"const": "daily"}}},
                    {"type": "null"},
                ]
            },
        },
        "x-draft": "should-be-dropped",
    }
    out = sanitize_mcp_schema(schema)
    props = out["properties"]
    assert props["id"]["format"] == "uuid"  # uuid 放行
    assert props["title"]["minLength"] == 1 and props["title"]["maxLength"] == 100
    assert props["title"]["pattern"] == "^[^\\n]+$"
    assert props["count"]["minimum"] == 0 and props["count"]["maximum"] == 100
    assert props["tags"]["minItems"] == 1 and props["tags"]["maxItems"] == 10
    assert props["recurrence"]["anyOf"][0]["properties"]["frequency"]["const"] == "daily"
    assert props["recurrence"]["anyOf"][1] == {"type": "null"}
    assert "x-draft" not in out  # 未知键仍跳过并告警


@pytest.mark.asyncio
async def test_source_list_and_build_spec_translation():
    session = FakeSession(tools=[_tool("list_events", read_only=True), _tool("create_event")])
    source = McpToolSource("node", ["x"], session_factory=_make_factory(session))
    infos = await source.list_tools()
    assert [t["name"] for t in infos] == ["list_events", "create_event"]

    read = source.build_spec(infos[0])
    write = source.build_spec(infos[1])
    assert isinstance(read, ToolSpec) and read.read_only is True
    assert isinstance(write, ToolSpec) and write.read_only is False
    # 描述补齐：ISO 时间写法；create 含查重语义（规格 §3）
    assert "ISO 8601" in read.description
    assert "ISO 8601" in write.description
    assert "查重" in write.description
    # schema 白名单已翻译
    assert read.input_schema == {"type": "object", "properties": {}}


@pytest.mark.asyncio
async def test_source_call_success_and_is_error():
    session = FakeSession(tools=[_tool("list_events")])
    source = McpToolSource("node", ["x"], session_factory=_make_factory(session))
    result = await source.call_tool("list_events", {"start": "2026-08-29T00:00:00+08:00"})
    assert result["content"] == json.dumps({"ok": True})
    with pytest.raises(ToolError) as excinfo:
        await source.call_tool("boom_is_error", {})
    assert excinfo.value.code == "mcp_tool_error"
    assert excinfo.value.user_message == "该日程不存在"


@pytest.mark.asyncio
async def test_source_connect_failure_marks_failed():
    async def bad_factory() -> FakeSession:
        raise OSError("spawn failed")

    source = McpToolSource("node", ["x"], session_factory=bad_factory)
    with pytest.raises(ToolError) as excinfo:
        await source.call_tool("x", {})
    assert excinfo.value.code == "tool_source_unavailable"
    assert source.failed is True
    # 失败后不再重连：直接快速失败
    with pytest.raises(ToolError):
        await source.call_tool("x", {})


@pytest.mark.asyncio
async def test_source_process_failure_disables_tools_via_callback():
    disabled: List[str] = []
    session = FakeSession(tools=[_tool("a"), _tool("b")], call_error=BrokenPipeError("connection lost"))
    source = McpToolSource(
        "node", ["x"],
        session_factory=_make_factory(session),
        on_unavailable=lambda names: disabled.extend(names),
    )
    await source.list_tools()
    with pytest.raises(ToolError) as excinfo:
        await source.call_tool("a", {})
    assert excinfo.value.code == "tool_source_unavailable"
    assert source.failed is True
    assert disabled == ["a", "b"]


@pytest.mark.asyncio
async def test_runtime_sync_registers_tools_and_backup(tmp_path):
    data_file = tmp_path / "agenda-data.json"
    data_file.write_text(json.dumps({"events": []}), encoding="utf-8")
    session = FakeSession(tools=[_tool("list_events", read_only=True), _tool("create_event")])
    source = McpToolSource("node", ["x"], session_factory=_make_factory(session))
    registry = ToolRegistry()
    runtime = ToolRuntime(
        registry,
        [source],
        backup_data_path=str(data_file),
        backup_dir=str(tmp_path / "backup"),
    )
    await runtime.sync()
    assert {s.name for s in registry.list()} == {"list_events", "create_event"}
    backup = tmp_path / "backup"
    assert backup.is_dir()
    assert len(list(backup.glob("agenda-data-*.json"))) == 1


@pytest.mark.asyncio
async def test_runtime_sync_cached_after_success(tmp_path):
    """复查修复②：来源成功同步后不再重复 list_tools（消除每轮刷屏）。"""
    data_file = tmp_path / "agenda-data.json"
    data_file.write_text(json.dumps({"events": []}), encoding="utf-8")
    session = FakeSession(tools=[_tool("list_events", read_only=True)])
    source = McpToolSource("node", ["x"], session_factory=_make_factory(session))
    registry = ToolRegistry()
    runtime = ToolRuntime(registry, [source])
    await runtime.sync()
    assert source.synced is True
    assert session.list_calls == 1
    await runtime.sync()  # 第二次同步不重新 list_tools
    assert session.list_calls == 1


@pytest.mark.asyncio
async def test_runtime_sync_failure_disables_source(tmp_path):
    session = FakeSession(tools=[], list_error=RuntimeError("init failed"))
    source = McpToolSource("node", ["x"], session_factory=_make_factory(session))
    registry = ToolRegistry()
    runtime = ToolRuntime(registry, [source])
    await runtime.sync()  # 不应抛异常
    assert source.failed is True
    assert registry.list() == []


@pytest.mark.asyncio
async def test_runtime_close_is_idempotent():
    session = FakeSession(tools=[_tool("a")])
    source = McpToolSource("node", ["x"], session_factory=_make_factory(session))
    await source.list_tools()
    await source.close()
    await source.close()  # 幂等
    assert source.failed is True


@pytest.mark.asyncio
async def test_source_close_timeout_returns_quickly():
    """Ctrl+C 卡终端修复：子进程关闭挂起（SDK aclose 不返回）时，close 必须在超时内返回。"""
    class _HangingStack:
        """模拟卡死的 AsyncExitStack：aclose 永不返回。"""

        async def aclose(self) -> None:
            await asyncio.sleep(10)

    source = McpToolSource(
        "node", ["x"],
        session_factory=_make_factory(FakeSession([_tool("a")])),
        close_timeout=0.05,
    )
    source._stack = _HangingStack()  # 私有字段直写：单测模拟关闭挂起
    source._session = object()
    start = time.monotonic()
    await source.close()
    assert time.monotonic() - start < 1.0  # 未被 10s 挂起拖住
    assert source.failed is True
    assert source._stack is None and source._session is None


@pytest.mark.asyncio
async def test_concurrent_sync_lists_tools_once(tmp_path):
    """R4 修复：并发 sync 只 list_tools 一次（sync 锁 + synced 缓存双保险，无重复注册/告警）。"""
    data_file = tmp_path / "agenda-data.json"
    data_file.write_text(json.dumps({"events": []}), encoding="utf-8")
    session = FakeSession(tools=[_tool("a", read_only=True)])
    source = McpToolSource("node", ["x"], session_factory=_make_factory(session))
    registry = ToolRegistry()
    runtime = ToolRuntime(
        registry, [source],
        backup_data_path=str(data_file),
        backup_dir=str(tmp_path / "bk"),
    )
    await asyncio.gather(runtime.sync(), runtime.sync())
    assert session.list_calls == 1


@pytest.mark.asyncio
async def test_concurrent_ensure_connected_single_spawn():
    """R4 修复：并发懒连接只启动一次子进程（连接锁），list 幂等可各自执行。"""
    session = FakeSession(tools=[_tool("a")])
    spawns: List[int] = []

    async def counting_factory() -> FakeSession:
        spawns.append(1)
        return session

    source = McpToolSource("node", ["x"], session_factory=counting_factory)
    await asyncio.gather(source.list_tools(), source.list_tools())
    assert len(spawns) == 1  # 子进程/连接只建一次
    assert session.list_calls == 2  # list 幂等，两个调用各自执行


@pytest.mark.asyncio
async def test_ensure_connected_cancel_cleans_up_without_failed():
    """R4 修复：连接初始化中取消（SSE 断连）→ 清理半成品、不标记来源故障、取消向上传播。"""
    class BlockingSession:
        async def initialize(self) -> None:
            await asyncio.sleep(30)

        async def list_tools(self) -> ListToolsResult:
            return ListToolsResult(tools=[])

    async def blocking_factory() -> BlockingSession:
        return BlockingSession()

    source = McpToolSource("node", ["x"], session_factory=blocking_factory, close_timeout=0.05)
    task = asyncio.create_task(source.list_tools())
    await asyncio.sleep(0.05)  # 让 initialize 进入挂起
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert source.failed is False  # 取消不是来源故障
    assert source._session is None and source._stack is None  # 半成品已清理


def test_ensure_daily_backup_once(tmp_path):
    data_file = tmp_path / "agenda-data.json"
    data_file.write_text("{}", encoding="utf-8")
    backup_dir = tmp_path / "backup"
    first = ensure_daily_backup(str(data_file), str(backup_dir))
    second = ensure_daily_backup(str(data_file), str(backup_dir))
    assert first == second  # 同日不重复备份
    assert len(list(backup_dir.glob("agenda-data-*.json"))) == 1
    assert ensure_daily_backup(str(tmp_path / "missing.json"), str(backup_dir)) is None


@pytest.mark.asyncio
async def test_tool_loop_disables_source_unavailable_tool():
    registry = ToolRegistry()

    async def flaky(args: Dict[str, Any]) -> Dict[str, Any]:
        raise ToolError(code="tool_source_unavailable", user_message="断开")

    registry.register(
        ToolSpec(
            name="flaky",
            description="必断工具。示例：{}。",
            input_schema={"type": "object", "properties": {}},
            executable=flaky,
        )
    )

    class FakeLLM:
        def __init__(self):
            self.calls = 0

        async def chat(
            self, messages: List[Dict[str, str]],
            tools: Optional[List[Dict[str, Any]]] = None,
            tool_choice: str = "auto",
            max_tokens: Optional[int] = None,
        ) -> Dict[str, Any]:
            self.calls += 1
            if self.calls == 1:
                return {"content": "", "tool_calls": [{"id": "c1", "name": "flaky", "arguments": {}}]}
            return {"content": "知道了。", "tool_calls": []}

    llm = FakeLLM()
    loop = ToolLoop(llm_client=llm, registry=registry)  # type: ignore[arg-type]
    result = await loop.run([{"role": "user", "content": "hi"}])
    assert result == "知道了。"
    assert registry.get("flaky").disabled is True  # 来源故障 → 工具标记不可用


def test_normalize_datetime_arguments_wire_format():
    """带偏移 ISO 时间转 UTC Z；date/普通字符串/Z 后缀原样保留（协议级翻译）。"""
    args = {
        "start": "2026-08-29T09:00:00+08:00",
        "end": "2026-08-29T09:00:00Z",
        "date": "2026-08-29",
        "title": "评审",
        "tags": ["a", "2026-08-29T10:00:00+08:00"],
        "nested": {"when": "2026-08-29T01:00:00+08:00"},
    }
    out = _normalize_datetime_arguments(args)
    assert out["start"] == "2026-08-29T01:00:00Z"  # +08:00 → UTC Z
    assert out["end"] == "2026-08-29T09:00:00Z"  # 已是 Z，不动
    assert out["date"] == "2026-08-29"
    assert out["title"] == "评审"
    assert out["tags"][1] == "2026-08-29T02:00:00Z"
    assert out["nested"]["when"] == "2026-08-28T17:00:00Z"  # 01:00+08:00 = 前一天 17:00Z




# ================================================================ R5b：SourceManager 加固（锁/超时/退避/取消/重试）


class SlowSession(FakeSession):
    """FakeSession 的慢速变体：initialize/list/call 可注入延迟。"""

    def __init__(
        self,
        tools: Optional[List[Tool]] = None,
        initialize_delay: float = 0.0,
        list_delay: float = 0.0,
        call_delay: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(tools=tools or [], **kwargs)
        self.initialize_delay = initialize_delay
        self.list_delay = list_delay
        self.call_delay = call_delay

    async def initialize(self) -> None:
        if self.initialize_delay:
            await asyncio.sleep(self.initialize_delay)

    async def list_tools(self) -> ListToolsResult:
        if self.list_delay:
            await asyncio.sleep(self.list_delay)
        return await super().list_tools()

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> CallToolResult:
        if self.call_delay:
            await asyncio.sleep(self.call_delay)
        return await super().call_tool(name, arguments)


def _counting_factory(session: FakeSession, counter: List[int]) -> Any:
    async def factory() -> FakeSession:
        counter[0] += 1
        return session

    return factory


@pytest.mark.asyncio
async def test_concurrent_list_tools_connects_once():
    """验收：并发首次调用只启动一个 MCP 进程（锁 + 双检）。"""
    counter: List[int] = [0]
    session = SlowSession(tools=[_tool("a")], initialize_delay=0.05)
    source = McpToolSource("node", ["x"], session_factory=_counting_factory(session, counter))
    results = await asyncio.gather(source.list_tools(), source.list_tools())
    assert counter[0] == 1
    assert len(results) == 2
    assert source.state == "ready"


@pytest.mark.asyncio
async def test_initialize_timeout_independent_of_connect():
    """验收：initialize 卡死可独立超时（不占用整段连接预算）。"""
    session = SlowSession(tools=[_tool("a")], initialize_delay=0.3)
    source = McpToolSource(
        "node", ["x"], session_factory=_make_factory(session),
        connect_timeout=10.0, initialize_timeout=0.05,
    )
    with pytest.raises(ToolError) as excinfo:
        await source.list_tools()
    assert excinfo.value.code == "tool_source_unavailable"
    assert source.failed is True
    assert source.state == "failed"


@pytest.mark.asyncio
async def test_connect_timeout_when_factory_hangs():
    async def factory() -> FakeSession:
        await asyncio.sleep(5)
        return FakeSession(tools=[])

    source = McpToolSource("node", ["x"], session_factory=factory, connect_timeout=0.05)
    with pytest.raises(ToolError) as excinfo:
        await source.list_tools()
    assert excinfo.value.code == "tool_source_unavailable"
    assert source.failed is True
    assert source.can_retry() is False


@pytest.mark.asyncio
async def test_backoff_blocks_immediate_retry_then_allows():
    attempts: List[int] = [0]

    async def factory() -> FakeSession:
        attempts[0] += 1
        if attempts[0] == 1:
            raise OSError("spawn failed")
        return FakeSession(tools=[_tool("ok")])

    source = McpToolSource("node", ["x"], session_factory=factory)
    with pytest.raises(ToolError):
        await source.list_tools()
    assert source.state == "failed"
    assert source.can_retry() is False  # 退避窗口内禁止立即重试（不刷屏）
    source._retry_at = 0  # 越过退避窗口
    infos = await source.list_tools()
    assert [info["name"] for info in infos] == ["ok"]
    assert source.failed is False
    assert source.state == "ready"


@pytest.mark.asyncio
async def test_list_tools_timeout_raises_tool_timeout():
    session = SlowSession(tools=[_tool("a")], list_delay=0.2)
    source = McpToolSource("node", ["x"], session_factory=_make_factory(session), list_timeout=0.05)
    with pytest.raises(ToolError) as excinfo:
        await source.list_tools()
    assert excinfo.value.code == "tool_timeout"


@pytest.mark.asyncio
async def test_call_cancelled_does_not_mark_failed():
    """取消传播：执行中取消不把来源标记为故障（SPEC-050 §10 断线语义）。"""
    session = SlowSession(tools=[_tool("a")], call_delay=60.0)
    source = McpToolSource("node", ["x"], session_factory=_make_factory(session))
    task = asyncio.create_task(source.call_tool("a", {}))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert source.failed is False
    assert source.state == "ready"


@pytest.mark.asyncio
async def test_runtime_retries_failed_source_after_backoff():
    """验收：来源失败不会每轮重试刷屏；退避窗口过后可自动恢复。"""
    attempts: List[int] = [0]

    async def factory() -> FakeSession:
        attempts[0] += 1
        if attempts[0] == 1:
            raise OSError("spawn failed")
        return FakeSession(tools=[_tool("a")])

    source = McpToolSource("node", ["x"], session_factory=factory)
    registry = ToolRegistry()
    runtime = ToolRuntime(registry, [source])
    await runtime.sync()
    assert source.failed is True
    assert registry.list() == []
    # 窗口内再同步：不触发重连
    await runtime.sync()
    assert attempts[0] == 1
    source._retry_at = 0  # 越过退避窗口
    await runtime.sync()
    assert [spec.name for spec in registry.list()] == ["a"]
    assert source.failed is False
    assert getattr(source, "synced", False) is True
