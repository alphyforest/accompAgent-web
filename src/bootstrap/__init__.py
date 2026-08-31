"""Composition Root 迁移目标（PLAN-010 R1）。

对象装配当前仍由 src/api/dependencies.py 承担（临时 Composition Root）；
待 DialogueService / ToolService 装配稳定后，容器逻辑迁入本包（container.py）。
"""

__all__: list[str] = []
