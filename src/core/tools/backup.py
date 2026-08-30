"""agenda-data.json 每日备份（规格 §7）。

接入起保留每日副本，删除类操作可回滚；MVP 接受共享文件并发残余风险，
写前重读由 MCP Server 侧仓储负责（读 → 改 → 原子写）。
"""

import shutil
from datetime import date
from pathlib import Path
from typing import Optional


def ensure_daily_backup(data_path: str, backup_dir: str) -> Optional[Path]:
    """当日已有副本则返回现有路径；否则复制一份并返回。

    数据文件不存在时返回 None（首次接入尚未生成文件，不报错）。
    """
    source = Path(data_path)
    if not source.is_file():
        return None
    target = Path(backup_dir) / f"agenda-data-{date.today().isoformat()}.json"
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target
