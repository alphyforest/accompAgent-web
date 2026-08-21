"""主动说话后台调度器：周期性检查触发器，命中则让 Agent 主动开口。

- 仅当用户"静默"超过阈值才可能触发（不打扰正在交谈的用户）
- 冷却记录在匹配器内存中（重启清零）
- 生成的主动发言进入队列，由 ``GET /api/initiative`` 供前端轮询获取展示
"""

import asyncio
from typing import Any, List, Optional

from src.core.agent.triggers import InitiativeTriggerMatcher
from src.utils.logger import logger


class InitiativeScheduler:
    """后台主动说话调度器（与事件系统解耦，负责"何时开口"）。"""

    def __init__(
        self,
        engine: Any,
        matcher: InitiativeTriggerMatcher,
        interval_seconds: float = 60.0,
        min_silence_seconds: float = 30.0,
        session_id: str = "default",
    ):
        self.engine = engine
        self.matcher = matcher
        self.interval = interval_seconds
        self.min_silence = min_silence_seconds
        self.session_id = session_id
        self._task: Optional[asyncio.Task[None]] = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def start(self) -> None:
        """启动后台循环任务（幂等）。"""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """停止后台循环（幂等）。"""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        """周期性 tick，异常不致命。"""
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - 调度单次异常不影响整体
                logger.warning("主动说话调度异常: {}", exc)
            await asyncio.sleep(self.interval)

    async def _tick(self) -> None:
        """单次检查：静默判定 + 触发器匹配 + 生成主动发言。"""
        if self.matcher.empty or self.engine is None:
            return
        context = self.engine.trigger_context(self.session_id)
        silence = context["silence_seconds"]
        if silence < self.min_silence:
            return  # 用户近期活跃，不打扰
        trigger = self.matcher.check(context)
        if trigger is None:
            return
        text = await self.engine.generate_initiative(trigger, self.session_id)
        if text:
            self._queue.put_nowait(text)
            logger.info("主动说话触发 trigger={} session={}", trigger.id, self.session_id)

    async def collect(self) -> List[str]:
        """取出当前积压的主动发言（供轮询接口返回）。"""
        items: List[str] = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items
