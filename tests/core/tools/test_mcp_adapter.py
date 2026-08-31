"""MCP stdio 适配器与工具运行时单元测试（Fake transport / Fake session，不 spawn 进程）。"""

import json
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

