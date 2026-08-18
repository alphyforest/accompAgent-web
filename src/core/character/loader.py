"""角色配置加载。"""

import json
from pathlib import Path
from typing import Any, Dict, List


def load_system_prompt(config_dir: str) -> str:
    """从配置目录加载角色人设文本。"""
    path = Path(config_dir) / "system_prompt.txt"
    return path.read_text(encoding="utf-8").strip()


def load_events(config_dir: str) -> List[Dict[str, Any]]:
    """从配置目录加载事件配置（顶层为数组）。"""
    path = Path(config_dir) / "events.json"
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return list(data.get("chains", []))
