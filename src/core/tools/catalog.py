"""ToolCatalog（SPEC-030 §4）：按 capability/tags 筛选的只读快照。

- snapshot() 返回请求开始时的只读视图，避免工具列表在 ToolLoop 中途变化
- 替代"每轮把全部 Registry 暴露给模型"的做法：工具只暴露与当前 capability 相关的子集
- 本模块只依赖 registry/spec，不 import 引擎/记忆模块
"""

from typing import List, Optional

from src.core.tools.registry import ToolRegistry
from src.core.tools.spec import GenericCapability, ToolSpec


class ToolCatalogSnapshot:
    """请求级只读工具视图。"""

    def __init__(self, tools: List[ToolSpec]) -> None:
        self._tools = list(tools)

    def all(self) -> List[ToolSpec]:
        """全部（含未筛选）。"""
        return list(self._tools)

    def find_by_capability(self, capability: str, tags: Optional[List[str]] = None) -> List[ToolSpec]:
        """按能力筛选：精确匹配或通用能力（generic）兜底；tags 可选交集。"""
        result = [tool for tool in self._tools if tool.capability == capability or tool.capability == GenericCapability]
        if tags:
            tag_set = set(tags)
            result = [tool for tool in result if tag_set.intersection(tool.tags)]
        return result

    def get(self, name: str) -> Optional[ToolSpec]:
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None


class ToolCatalog:
    """基于 ToolRegistry 的目录接口（R5 v1；来源状态管理随 R5b ToolSourceManager）。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def snapshot(self, capability: Optional[str] = None, tags: Optional[List[str]] = None) -> ToolCatalogSnapshot:
        """构建请求级快照（可选预筛选）。"""
        tools = [tool for tool in self._registry.list() if not tool.disabled]
        snapshot = ToolCatalogSnapshot(tools)
        if capability is not None:
            return ToolCatalogSnapshot(snapshot.find_by_capability(capability, tags))
        return snapshot

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._registry.get(name)

    def set_source_state(self, source_id: str, state: str) -> None:
        """来源状态占位（R5b ToolSourceManager 接管；设置失败来源时禁用其工具）。"""
        if state == "failed":
            names = [
                tool.name
                for tool in self._registry.list()
                if tool.source_id == source_id
            ]
            self._registry.disable_names(names)
