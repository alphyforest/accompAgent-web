"""ToolLoop 运行时单元测试（规格 §5 验收，Fake LLM 不连真实 API）。"""

import asyncio
from typing import Any, Dict, List, Optional

import pytest
from src.core.tools.registry import ToolRegistry
from src.core.tools.spec import ToolError, ToolSpec
from src.core.tools.tool_loop import ToolLoop


class FakeToolLLM:
    """假 LLM：按脚本逐次返回 chat 结果（dict 或 callable(messages)）。"""

    def __init__(self, script: List[Any], chat_error: Optional[Exception] = None):
        self.script = list(script)
        self.chat_error = chat_error
        self.calls: List[Dict[str, Any]] = []
        self.tool_lists: List[Optional[List[Dict[str, Any]]]] = []

    async def chat(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        self.calls.append({"messages": list(messages), "tools": tools})
        self.tool_lists.append(tools)
        if self.chat_error is not None:
            raise self.chat_error
        entry = self.script.pop(0) if self.script else {"content": "", "tool_calls": []}
        if callable(entry):
            return entry(messages)
        return entry


def _echo_spec(executed: List[Dict[str, Any]]) -> ToolSpec:
    async def execute(args: Dict[str, Any]) -> Dict[str, Any]:
        executed.append(args)
        return {"echo": args}

    return ToolSpec(
        name="echo",
        description="回显工具。参数 value 为任意字符串。示例：{'value': 'x'}。",
        input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
        executable=execute,
    )


def _registry(executed: List[Dict[str, Any]]) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_echo_spec(executed))
    return registry


def _tool_call(call_id: str, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": call_id, "name": name, "arguments": arguments}


def _final(content: str) -> Dict[str, Any]:
    return {"content": content, "tool_calls": []}


@pytest.mark.asyncio
async def test_run_returns_none_when_no_tools():
    registry = ToolRegistry()
    loop = ToolLoop(llm_client=FakeToolLLM([]), registry=registry)
    assert await loop.run([{"role": "user", "content": "hi"}]) is None


@pytest.mark.asyncio
async def test_run_executes_tool_and_returns_final_text():
    executed: List[Dict[str, Any]] = []
    llm = FakeToolLLM([
        {"content": "先查一下", "tool_calls": [_tool_call("c1", "echo", {"value": "x"})]},
        _final("[情绪:happy]结果是 x~"),
    ])
    loop = ToolLoop(llm_client=llm, registry=_registry(executed))
    result = await loop.run([{"role": "user", "content": "回显 x"}])
    assert result == "[情绪:happy]结果是 x~"
    assert executed == [{"value": "x"}]
    second_messages = llm.calls[1]["messages"]
    assert any(m.get("role") == "tool" and m.get("name") == "echo" for m in second_messages)


@pytest.mark.asyncio
async def test_run_feeds_tool_error_text_back():
    executed: List[Dict[str, Any]] = []

    async def boom(args: Dict[str, Any]) -> Dict[str, Any]:
        executed.append(args)
        raise ToolError(code="mcp_tool_error", user_message="该日程不存在")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="boom",
            description="必失败工具。示例：{}。",
            input_schema={"type": "object", "properties": {}},
            executable=boom,
        )
    )
    llm = FakeToolLLM([
        {"content": "", "tool_calls": [_tool_call("c1", "boom", {})]},
        _final("抱歉，没找到这条日程。"),
    ])
    loop = ToolLoop(llm_client=llm, registry=registry)
    result = await loop.run([{"role": "user", "content": "删掉它"}])
    assert result == "抱歉，没找到这条日程。"
    tool_msg = next(m for m in llm.calls[1]["messages"] if m.get("role") == "tool")
    assert "该日程不存在" in tool_msg["content"]
    assert "isError" in tool_msg["content"]


@pytest.mark.asyncio
async def test_run_rejects_no_progress_retry():
    executed: List[Dict[str, Any]] = []
    llm = FakeToolLLM([
        {
            "content": "",
            "tool_calls": [
                _tool_call("c1", "echo", {"value": "x"}),
                _tool_call("c2", "echo", {"value": "x"}),
            ],
        },
        {"content": "", "tool_calls": [_tool_call("c3", "echo", {"value": "x"})]},
        _final("完成"),
    ])
    loop = ToolLoop(llm_client=llm, registry=_registry(executed))
    result = await loop.run([{"role": "user", "content": "hi"}])
    assert result == "完成"
    assert len(executed) == 1
    tool_messages = [m for m in llm.calls[1]["messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 2
    rejected = [m for m in tool_messages if "no_progress_retry" in m["content"]]
    assert len(rejected) == 1


@pytest.mark.asyncio
async def test_run_allows_repeat_for_read_only_tool():
    """复查修复③：只读工具同参重调放行（验证性重读），不会被无进展护栏拦截。"""
    executed: List[Dict[str, Any]] = []

    async def read_execute(args: Dict[str, Any]) -> Dict[str, Any]:
        executed.append(args)
        return {"events": []}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="list_events",
            description="只读查询工具。示例：{}。",
            input_schema={"type": "object", "properties": {"start": {"type": "string"}}},
            executable=read_execute,
            read_only=True,
        )
    )
    llm = FakeToolLLM([
        {
            "content": "",
            "tool_calls": [
                _tool_call("c1", "list_events", {"start": "2026-08-30T00:00:00+08:00"}),
                _tool_call("c2", "list_events", {"start": "2026-08-30T00:00:00+08:00"}),
            ],
        },
        _final("验证完成"),
    ])
    loop = ToolLoop(llm_client=llm, registry=registry)
    result = await loop.run([{"role": "user", "content": "复查一下"}])
    assert result == "验证完成"
    assert len(executed) == 2  # 同参但只读，两次都执行
    tool_messages = [m for m in llm.calls[1]["messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert not [m for m in tool_messages if "no_progress_retry" in m["content"]]


@pytest.mark.asyncio
async def test_run_max_rounds_ends_with_no_tools_request():
    executed: List[Dict[str, Any]] = []
    script = [_final("done")]
    for i in range(3):
        script.insert(0, {"content": "", "tool_calls": [_tool_call(f"c{i}", "echo", {"value": f"v{i}"})]})
    llm = FakeToolLLM(script)
    loop = ToolLoop(llm_client=llm, registry=_registry(executed), max_rounds=3)
    result = await loop.run([{"role": "user", "content": "hi"}])
    assert result == "done"
    assert len(executed) == 3
    assert llm.tool_lists[-1] is None
    assert len(llm.calls) == 4


@pytest.mark.asyncio
async def test_run_llm_error_degrades_to_none():
    llm = FakeToolLLM([], chat_error=RuntimeError("api down"))
    loop = ToolLoop(llm_client=llm, registry=_registry([]))
    assert await loop.run([{"role": "user", "content": "hi"}]) is None


@pytest.mark.asyncio
async def test_run_call_timeout_feeds_tool_timeout():
    executed: List[Dict[str, Any]] = []

    async def slow(args: Dict[str, Any]) -> Dict[str, Any]:
        executed.append(args)
        await asyncio.sleep(10)
        return {"echo": args}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="slow",
            description="慢工具。示例：{}。",
            input_schema={"type": "object", "properties": {}},
            executable=slow,
        )
    )
    llm = FakeToolLLM([
        {"content": "", "tool_calls": [_tool_call("c1", "slow", {})]},
        _final("工具超时了，我告诉用户。"),
    ])
    loop = ToolLoop(llm_client=llm, registry=registry, call_timeout=0.01)
    result = await loop.run([{"role": "user", "content": "hi"}])
    assert result == "工具超时了，我告诉用户。"
    tool_msg = next(m for m in llm.calls[1]["messages"] if m.get("role") == "tool")
    assert "tool_timeout" in tool_msg["content"]


@pytest.mark.asyncio
async def test_run_overall_timeout_degrades_to_none():
    executed: List[Dict[str, Any]] = []

    async def chat_slow(
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        await asyncio.sleep(5)
        return _final("拖了")

    llm = FakeToolLLM([])
    llm.chat = chat_slow  # type: ignore[method-assign]
    loop = ToolLoop(llm_client=llm, registry=_registry(executed), overall_timeout=0.01)
    assert await loop.run([{"role": "user", "content": "hi"}]) is None




async def test_run_uses_provided_tool_subset():
    """R5：ToolLoop 只把传入的工具子集暴露给模型（ToolCatalog 筛选后）。"""
    executed: List[Dict[str, Any]] = []
    registry = _registry(executed)
    extra = ToolSpec(
        name="other_tool",
        description="另一个工具。",
        input_schema={"type": "object"},
        executable=_echo_spec(executed).executable,
    )
    echo_spec = _echo_spec(executed)
    llm = FakeToolLLM(
        [
            {"content": "", "tool_calls": [_tool_call("c1", echo_spec.name, {"value": "x"})]},
            {"content": "完成了", "tool_calls": []},
        ]
    )
    loop = ToolLoop(llm, registry)
    final = await loop.run([{"role": "user", "content": "你好"}], tools=[echo_spec])
    assert final == "完成了"
    # 模型第一轮只看到 echo（另一个工具未暴露）
    assert llm.tool_lists[0] is not None
    names = [t["function"]["name"] for t in llm.tool_lists[0]]
    assert names == ["echo"]
    assert extra.name not in names
    assert executed == [{"value": "x"}]



async def test_run_propagates_cancellation():
    """R5b：工具执行中取消任务 → CancelledError 向上传播（不被吞掉）。"""
    started = asyncio.Event()

    async def slow(args: Dict[str, Any]) -> Dict[str, Any]:
        started.set()
        await asyncio.sleep(60)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="slow_tool",
            description="慢工具。",
            input_schema={"type": "object"},
            executable=slow,
        )
    )
    llm = FakeToolLLM(
        [{"content": "", "tool_calls": [_tool_call("c1", "slow_tool", {})]}]
    )
    loop = ToolLoop(llm, registry)
    task = asyncio.create_task(loop.run([{"role": "user", "content": "你好"}]))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
