"""工具层契约/注册表单元测试（阶段 1 验收，不依赖任何引擎模块）。"""

from typing import Any, Dict

import pytest
from src.core.tools.builtin import build_now_tool
from src.core.tools.registry import ToolRegistry
from src.core.tools.spec import ToolError, ToolSpec


async def _ok_execute(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"echo": args}


async def _boom_execute(args: Dict[str, Any]) -> Dict[str, Any]:
    raise RuntimeError("db broken")


async def _tool_error_execute(args: Dict[str, Any]) -> Dict[str, Any]:
    raise ToolError(code="mcp_tool_error", user_message="该日程不存在")


def _spec(name: str = "demo_tool", executable: Any = _ok_execute) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="演示工具。参数 value 为任意字符串。示例：{'value': 'x'}。",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string", "description": "任意值"}},
            "required": ["value"],
        },
        executable=executable,
    )


def test_register_list_get_snapshot():
    registry = ToolRegistry()
    registry.register(_spec("a"))
    registry.register(_spec("b"))
    names = sorted(s.name for s in registry.list())
    assert names == ["a", "b"]
    assert registry.get("a") is not None
    assert registry.get("missing") is None
    snap = registry.snapshot()
    assert set(snap) == {"a", "b"}
    # snapshot 是拷贝，外部修改不影响内部表
    snap["c"] = _spec("c")
    assert registry.get("c") is None


def test_register_idempotent_same_spec():
    registry = ToolRegistry()
    registry.register(_spec("a"))
    registry.register(_spec("a"))  # 同契约重复注册为 no-op
    assert len(registry.list()) == 1


def test_register_duplicate_requires_replace():
    registry = ToolRegistry()
    registry.register(_spec("a"))
    with pytest.raises(ValueError):
        registry.register(_spec("a", executable=_boom_execute))
    registry.register(_spec("a", executable=_boom_execute), replace=True)
    assert registry.get("a").executable is _boom_execute


def test_set_disabled_and_has_enabled_tools():
    registry = ToolRegistry()
    registry.register(_spec("a"))
    registry.register(_spec("b"))
    assert registry.has_enabled_tools() is True
    registry.set_disabled("a", True)
    assert registry.get("a").disabled is True
    assert registry.has_enabled_tools() is True  # b 仍可用
    registry.disable_names(["b", "not_exist"])
    assert registry.has_enabled_tools() is False
    with pytest.raises(KeyError):
        registry.set_disabled("missing", True)


@pytest.mark.asyncio
async def test_execute_success_normalize_dict():
    registry = ToolRegistry()
    registry.register(_spec("a"))
    result = await registry.execute("a", {"value": "x"})
    assert result == {"echo": {"value": "x"}}


@pytest.mark.asyncio
async def test_execute_normalizes_unknown_exception_to_tool_error():
    registry = ToolRegistry()
    registry.register(_spec("a", executable=_boom_execute))
    with pytest.raises(ToolError) as excinfo:
        await registry.execute("a")
    assert excinfo.value.code == "tool_execution_error"
    assert excinfo.value.user_message  # 用户可读


@pytest.mark.asyncio
async def test_execute_propagates_tool_error_as_is():
    registry = ToolRegistry()
    registry.register(_spec("a", executable=_tool_error_execute))
    with pytest.raises(ToolError) as excinfo:
        await registry.execute("a")
    assert excinfo.value.code == "mcp_tool_error"
    assert excinfo.value.user_message == "该日程不存在"


@pytest.mark.asyncio
async def test_execute_not_found_and_disabled():
    registry = ToolRegistry()
    registry.register(_spec("a"))
    with pytest.raises(ToolError) as exc:
        await registry.execute("missing")
    assert exc.value.code == "tool_not_found"
    registry.set_disabled("a", True)
    with pytest.raises(ToolError) as exc:
        await registry.execute("a")
    assert exc.value.code == "tool_disabled"


@pytest.mark.asyncio
async def test_execute_rejects_non_dict_result():
    async def bad(args: Dict[str, Any]) -> Any:
        return "not a dict"

    registry = ToolRegistry()
    registry.register(_spec("a", executable=bad))
    with pytest.raises(ToolError) as exc:
        await registry.execute("a")
    assert exc.value.code == "tool_result_invalid"


def test_spec_validation():
    with pytest.raises(ValueError):
        _spec(name="Bad Name")
    with pytest.raises(ValueError):
        _spec(name="1abc")
    with pytest.raises(ValueError):
        ToolSpec(name="x", description="  ", input_schema={}, executable=_ok_execute)
    with pytest.raises(ValueError):
        ToolSpec(name="x", description="描述", input_schema=[], executable=_ok_execute)
    with pytest.raises(ValueError):
        ToolSpec(name="x", description="描述", input_schema={}, executable=None)  # type: ignore[arg-type]


def test_to_openai_schema():
    spec = _spec("a")
    schema = spec.to_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "a"
    assert schema["function"]["parameters"] == spec.input_schema


@pytest.mark.asyncio
async def test_now_tool_returns_iso8601_with_offset():
    registry = ToolRegistry()
    registry.register(build_now_tool())
    result = await registry.execute("now")
    assert result.get("now")
    assert "+" in result["now"] or result["now"].endswith("Z")  # 带时区偏移
    assert result.get("weekday") in {"星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"}
    assert result.get("utc_offset")  # 如 +0800
