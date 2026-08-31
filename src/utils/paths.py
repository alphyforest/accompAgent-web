"""路径解析（R6）：资源 / 可写数据 / 配置 / 日志 / 备份 五类分离。

- resource_path：只读资源（static、角色配置），打包时指向 sys._MEIPASS
- user_data_path / user_config_path / user_log_path / user_backup_path：
  可写数据一律进用户目录（Windows %LOCALAPPDATA%\ai-agent，其余 ~/.ai-agent；
  可用 AI_AGENT_HOME 覆盖），禁止落入 _MEIPASS（P0，桌面化门禁，PLAN-010 §9.1）
"""

import os
import sys
from pathlib import Path
from typing import Optional


def resource_path(relative: str) -> Path:
    """将相对资源路径解析为可访问的路径。

    源码运行时返回当前工作目录下的相对路径；
    打包运行时静态资源解压到 sys._MEIPASS 临时目录，自动指向该目录。
    资源路径统一走此函数解析，禁止在业务代码中写死相对路径。
    """
    bundle_dir: Optional[str] = getattr(sys, "_MEIPASS", None)
    normalized = os.path.normpath(relative)
    if bundle_dir is not None:
        return Path(bundle_dir) / normalized
    return Path(normalized)


def app_home() -> Path:
    """可写数据根目录（AI_AGENT_HOME 可覆盖）。"""
    override = os.environ.get("AI_AGENT_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ai-agent"
    return Path.home() / ".ai-agent"


def _user_path(kind: str, *parts: str) -> Path:
    path = app_home() / kind
    if parts:
        path = path.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def user_data_path(*parts: str) -> Path:
    """可写数据目录（memory.db 等）。"""
    return _user_path("data", *parts)


def user_config_path(*parts: str) -> Path:
    """可写配置目录。"""
    return _user_path("config", *parts)


def user_log_path(*parts: str) -> Path:
    """日志目录。"""
    return _user_path("log", *parts)


def user_backup_path(*parts: str) -> Path:
    """备份目录。"""
    return _user_path("backup", *parts)


def resolve_user_path(configured: str, kind: str) -> Path:
    """按配置值解析可写路径：绝对路径直接用；相对路径落入指定用户目录。"""
    raw = Path(configured)
    if raw.is_absolute():
        raw.parent.mkdir(parents=True, exist_ok=True)
        return raw
    if kind == "data":
        return user_data_path(*raw.parts)
    if kind == "config":
        return user_config_path(*raw.parts)
    if kind == "log":
        return user_log_path(*raw.parts)
    return user_backup_path(*raw.parts)
