"""工具运行时装配（规格 §2 运行时层）：注册表 + 来源（MCP 等）的同步与生命周期。

- 对话不感知具体工具：只持有 ToolRegistry（契约接口）与 ToolRuntime（同步来源）
- 来源故障仅禁用其工具（不崩服务），失败来源不再重试（本进程内）
- agenda-data 每日备份在首次同步时执行一次
"""

import asyncio
from typing import Any, List

from src.core.tools.backup import ensure_daily_backup
from src.core.tools.registry import ToolRegistry
from src.utils.logger import logger


class ToolRuntime:
    """把注册表与外部工具来源装配起来，负责同步/禁用/关闭。"""

    def __init__(
        self,
        registry: ToolRegistry,
        sources: List[Any],
        backup_data_path: str = "",
        backup_dir: str = "",
    ) -> None:
        self.registry = registry
        self.sources = sources
        self.backup_data_path = backup_data_path
        self.backup_dir = backup_dir
        self._backup_checked = False

    async def sync(self) -> None:
        """同步来源工具到注册表（懒连接触发点；失败仅禁用来源，不抛异常）。"""
        if not self._backup_checked and self.backup_data_path and self.backup_dir:
            self._backup_checked = True
            try:
                ensure_daily_backup(self.backup_data_path, self.backup_dir)
            except Exception as exc:  # noqa: BLE001 - 备份失败不阻塞对话
                logger.warning("agenda-data 每日备份失败 err={}", exc)

        if not self.sources:
            return
        await asyncio.gather(*(self._sync_source(source) for source in self.sources))

    async def _sync_source(self, source: Any) -> None:
        """同步单个来源；连接/翻译失败 → 禁用该来源已有工具。

        已成功同步过的来源（source.synced）不再重复 list_tools（2026-08-30 复查：
        每轮对话重复同步导致 schema 告警刷屏与无谓重复注册）；失败标记 failed 后
        本进程内不再重试。
        """
        if source.failed or getattr(source, "synced", False):
            return
        try:
            tools = await source.list_tools()
            for info in tools:
                self.registry.register(source.build_spec(info), replace=True)
            source.synced = True
        except Exception as exc:  # noqa: BLE001 - 来源故障降级处理
            source.failed = True
            self.registry.disable_names(source.tool_names)
            logger.warning("工具来源 {} 同步失败，已禁用其工具 err={}", getattr(source, "name", "?"), exc)

    async def close(self) -> None:
        """统一关闭来源（归 lifespan；异常只记录，不阻断应用退出）。"""
        for source in self.sources:
            try:
                await source.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("工具来源 {} 关闭异常 err={}", getattr(source, "name", "?"), exc)
