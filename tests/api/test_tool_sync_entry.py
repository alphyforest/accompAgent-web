"""R2 修复回归：chat 入口必须先同步工具来源，再生成能力快照。

背景：R2 把工具同步从 DialogueEngine 移入 ToolServiceAdapter.sync 后，chat 入口无人触发，
agenda 工具永不注册 → 能力快照恒空 → 路由永远走普通对话（"调日程没反应"）。
本文件守护两条接线：dependencies 入口存在、chat.generate 内顺序正确（sync 先于 capability）。
"""

import ast
from pathlib import Path

import pytest
from src.api.dependencies import ensure_tools_synced


class _FakeRuntime:
    """记录 sync 调用的假 ToolRuntime。"""

    def __init__(self) -> None:
        self.sync_calls = 0

    async def sync(self) -> None:
        self.sync_calls += 1


@pytest.mark.asyncio
async def test_ensure_tools_synced_calls_runtime_sync(monkeypatch):
    """dependencies 同步入口必须调用 ToolRuntime.sync（接线存在）。"""
    fake = _FakeRuntime()
    monkeypatch.setattr("src.api.dependencies.get_tool_runtime", lambda: fake)  # type: ignore[assignment]
    await ensure_tools_synced()
    assert fake.sync_calls == 1


def test_chat_generate_syncs_before_capability_snapshot():
    """AST 守卫：chat.generate 中 ensure_tools_synced 必须出现在 get_capability_snapshot 之前。

    若将来有人把 sync 移走/调换顺序，此测试即红，防止 R2 接缝再次断开。
    """
    source = (Path(__file__).resolve().parents[2] / "src" / "api" / "routes" / "chat.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    generate = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "generate"
    )
    calls: list[tuple[int, str]] = []
    for node in ast.walk(generate):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.append((node.lineno, func.id))

    names = {name for _, name in calls}
    assert "ensure_tools_synced" in names, "chat.generate 缺少 ensure_tools_synced 调用"
    assert "get_capability_snapshot" in names, "chat.generate 缺少 get_capability_snapshot 调用"

    sync_at = next(lineno for lineno, name in calls if name == "ensure_tools_synced")
    snapshot_at = next(lineno for lineno, name in calls if name == "get_capability_snapshot")
    assert sync_at < snapshot_at, "工具同步必须发生在能力快照之前（否则路由看不到 agenda 工具）"
