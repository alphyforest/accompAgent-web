"""Agenda MCP Server 真实联调脚本（规格 §6 验收 / 阶段 4 写闭环）。

用法：
    python scripts/agenda_mcp_smoke.py

行为：
- 使用临时目录作为 AGENDA_DATA_PATH（不触碰真实共享数据文件）
- 连接真实 Agenda MCP Server（node + tsx CLI，settings 配置）
- 校验 7 工具 schema 翻译可用
- 调通 3 个读工具（list_events / get_timetable_for_date / get_semester_config）
- 跑写闭环：create → list 可见 → complete → delete → list 不可见
- 校验 agenda-data.json 为合法 JSON（共享单写者写入产物）

退出码：0=PASS，1=FAIL。
"""

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

# 使脚本可从任意 cwd 直接运行（python scripts/agenda_mcp_smoke.py）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import settings
from src.core.tools.sources.mcp import McpToolSource
from src.utils.logger import logger

EXPECTED_TOOLS = {
    "list_events",
    "create_event",
    "update_event",
    "complete_event",
    "delete_event",
    "get_timetable_for_date",
    "get_semester_config",
}


def _iso(day_offset: int, hour: int, minute: int = 0) -> str:
    """N 天后的本地日期时间，ISO 8601 带时区偏移。"""
    now = datetime.now().astimezone()
    target = (now + timedelta(days=day_offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target.isoformat(timespec="seconds")


def _today() -> str:
    now = datetime.now().astimezone()
    return now.date().isoformat()


def _result_text(payload: Dict[str, Any]) -> str:
    return str(payload.get("content") or "")


async def main() -> int:
    checks: List[str] = []
    failures: List[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(name)
        if ok:
            print(f"  [PASS] {name}")
        else:
            failures.append(name)
            print(f"  [FAIL] {name} {detail}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="agenda_mcp_smoke_"))
    data_path = tmp_dir / "agenda-data.json"
    print(f"== Agenda MCP 联调（临时数据目录: {tmp_dir}）==")

    source = McpToolSource(
        command=settings.agenda_mcp_command,
        args=settings.agenda_mcp_args,
        env={"AGENDA_DATA_PATH": str(data_path)},
        name="agenda-smoke",
        connect_timeout=30.0,
        call_timeout=30.0,
    )

    async def call(name: str, arguments: Dict[str, Any]) -> Any:
        return await source.call_tool(name, arguments)

    try:
        print("-- 1. list_tools（7/7 schema 翻译）--")
        infos = await source.list_tools()
        names = {info["name"] for info in infos}
        check("list_tools 返回 7 工具", names == EXPECTED_TOOLS, f"got={sorted(names)}")
        missing = EXPECTED_TOOLS - names
        check("schema 翻译 7/7 可用", not missing, f"missing={sorted(missing)}")
        for info in infos:
            print("    - " + str(info["name"]) + ": read_only=" + str(info["read_only"]))

        print("-- 2. 读工具 --")
        day = _today()
        result = await call("list_events", {"start": _iso(-1, 0), "end": _iso(7, 23, 59)})
        check("list_events 真实调用", isinstance(result, dict), str(result)[:200])
        result = await call("get_timetable_for_date", {"date": day})
        check("get_timetable_for_date 真实调用", isinstance(result, dict), str(result)[:200])
        result = await call("get_semester_config", {})
        check("get_semester_config 真实调用", isinstance(result, dict), str(result)[:200])

        print("-- 3. 写工具闭环（create → list → complete → delete）--")
        created = await call(
            "create_event",
            {
                "title": "MCP 联调日程（可删除）",
                "description": "smoke test",
                "start": _iso(1, 9, 0),
                "end": _iso(1, 10, 0),
                "category": "personal",
                "isImportant": False,
                "tags": ["mcp-smoke"],
            },
        )
        created_text = _result_text(created)
        check("create_event 成功", bool(created_text), created_text[:200])
        try:
            created_data = json.loads(created_text)
            event_id = created_data.get("id", "")
        except ValueError:
            event_id = ""
        check("create_event 返回 id", bool(event_id), created_text[:200])

        listed = await call("list_events", {"start": _iso(0, 0), "end": _iso(2, 23, 59), "tags": ["mcp-smoke"]})
        listed_text = _result_text(listed)
        check("list_events 可见新建日程", event_id in listed_text, listed_text[:300])

        data_pre = data_path.read_bytes() if data_path.exists() else b""
        check("agenda-data.json 已生成（共享单写者产物）", bool(data_pre))
        try:
            json.loads(data_pre)
            check("agenda-data.json 为合法 JSON", True)
        except ValueError as exc:
            check("agenda-data.json 为合法 JSON", False, str(exc))

        completed = await call("complete_event", {"id": event_id, "completed": True})
        completed_text = _result_text(completed)
        check(
            "complete_event 成功",
            "completed" in completed_text or "updatedAt" in completed_text,
            completed_text[:200],
        )

        deleted = await call("delete_event", {"id": event_id})
        check("delete_event 成功", bool(_result_text(deleted)), _result_text(deleted)[:200])
        after = await call("list_events", {"start": _iso(0, 0), "end": _iso(2, 23, 59), "tags": ["mcp-smoke"]})
        check("delete 后不可见", event_id not in _result_text(after), _result_text(after)[:300])

    except Exception as exc:  # noqa: BLE001 - 联调脚本失败聚合输出
        failures.append("联调整体异常")
        print(f"  [FAIL] 联调异常: {exc!r}")
        logger.exception("smoke failed")
    finally:
        await source.close()

    print(f"== 结果: {len(checks) - len(failures)}/{len(checks)} PASS ==")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

