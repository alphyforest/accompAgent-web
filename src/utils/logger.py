"""Loguru 结构化日志配置。"""

import sys

from loguru import logger

# 移除默认 handler，统一控制输出格式
logger.remove()

logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
)

__all__ = ["logger"]
