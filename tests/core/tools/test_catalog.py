"""ToolCatalog 测试（SPEC-030 §4）：capability 筛选快照。"""

from typing import Any, Dict

from src.core.tools.catalog import ToolCatalog
from src.core.tools.registry import ToolRegistry
from src.core.tools.spec import ToolSpec


async def _ok(args: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True}


def _spec(name: str, capability: str = "generic", read_only: bool = False) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} 工具。",
        input_schema={"type": "object"},
        executable=_ok,
        capability=capability,
        read_only=read_only,
        source_id="test",
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_spec("list_events", capability="calendar.read", read_only=True))
    registry.register(_spec("create_event", capability="calendar.write"))
    registry.register(_spec("now_time", capability="time.read", read_only=True))
    return registry


def test_snapshot_filters_disabled_and_capability():
    registry = _registry()
    registry.set_disabled("now_time", True)
    catalog = ToolCatalog(registry)

    all_names = [s.name for s in catalog.snapshot().all()]
    assert all_names == ["list_events", "create_event"]

    calendar = catalog.snapshot().find_by_capability("calendar.read")
    assert [s.name for s in calendar] == ["list_events"]

    generic_fallback = catalog.snapshot().find_by_capability("unknown.x")
    assert generic_fallback == []


def test_snapshot_prefilter_and_get():
    registry = _registry()
    catalog = ToolCatalog(registry)
    snapshot = catalog.snapshot(capability="calendar.write")
    assert [s.name for s in snapshot.all()] == ["create_event"]
    assert snapshot.get("create_event") is not None
    assert snapshot.get("missing") is None


def test_set_source_state_failed_disables_source_tools():
    registry = _registry()
    ToolCatalog(registry).set_source_state("test", "failed")
    assert registry.get("list_events").disabled is True
    assert registry.get("create_event").disabled is True
