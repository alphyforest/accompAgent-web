"""角色配置加载（R6：移除未接线的 load_events，事件由 EventSystem 独立加载）。"""

from pathlib import Path


def load_system_prompt(config_dir: str, filename: str = "system_prompt.txt") -> str:
    """从配置目录加载角色人设文本（文件名默认 system_prompt.txt，可由角色卡指定）。"""
    path = Path(config_dir) / filename
    return path.read_text(encoding="utf-8").strip()
