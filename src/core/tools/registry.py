"""工具注册表（规格 §4）。

- register 幂等：同名同契约重复注册为 no-op；同名不同契约必须显式 replace=True
- execute 统一归一化：任何异常转为 ToolError（code + user_message），不裸抛
- 可用性标记：单条 set_disabled / 整体批量禁用
- 本模块不依赖任何引擎模块（无反向 import），仅依赖 spec / logger
"""

from typing import Any, Dict, Iterable, List, Optional

from src.core.tools.spec import ToolError, ToolSpec
from src.utils.logger import logger


class ToolRegistry:
    """工具注册表：register / list / get / set_disabled / snapshot / execute。"""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec, replace: bool = False) -> None:
        """注册工具。

        同名同契约重复注册幂等（no-op）；同名不同契约需显式 replace=True，
        否则抛 ValueError。
        """
        existing = self._tools.get(spec.name)
        if existing is not None:
            if existing == spec:
                return  # 幂等：相同契约重复注册
            if not replace:
                raise ValueError(f"工具 {spec.name} 已注册，覆盖需显式 replace=True")
        self._tools[spec.name] = spec
        logger.debug("工具注册 name={}", spec.name)

    def list(self) -> List[ToolSpec]:
        """列出全部工具（返回副本，避免外部误改内部表）。"""
        return list(self._tools.values())

    def get(self, name: str) -> Optional[ToolSpec]:
        """按名取工具，不存在返回 None。"""
        return self._tools.get(name)

    def set_disabled(self, name: str, disabled: bool) -> None:
        """单条可用性标记；不存在抛 KeyError（调用方应先用 get 判断）。"""
        spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"工具 {name} 未注册")
        spec.disabled = disabled

    def disable_names(self, names: Iterable[str]) -> None:
        """批量禁用（供来源故障降级使用，规格 §7 生命周期）。"""
        for name in names:
            spec = self._tools.get(name)
            if spec is not None:
                spec.disabled = True

    def has_enabled_tools(self) -> bool:
        """是否存在至少一个可用工具（ToolLoop 的降级判据）。"""
        return any(not spec.disabled for spec in self._tools.values())

    def snapshot(self) -> Dict[str, ToolSpec]:
        """返回当前注册表浅拷贝（读快照，供调试/观测）。"""
        return dict(self._tools)

    async def execute(self, name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """执行工具，成功返回结构化结果。

        任何异常归一化为 ToolError（tool_not_found / tool_disabled /
        tool_execution_error / tool_result_invalid），不向对话层裸抛。
        """
        spec = self._tools.get(name)
        if spec is None:
            raise ToolError(code="tool_not_found", user_message=f"工具 {name} 不存在，请稍后重试")
        if spec.disabled:
            raise ToolError(code="tool_disabled", user_message=f"工具 {name} 当前不可用")
        try:
            result = await spec.executable(args or {})
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - 注册表负责归一化，禁止裸抛
            logger.exception("工具执行失败 name={} err={}", name, exc)
            raise ToolError(code="tool_execution_error", user_message=f"工具 {name} 执行失败，请稍后重试") from exc
        if not isinstance(result, dict):
            logger.error("工具返回非结构化结果 name={} type={}", name, type(result).__name__)
            raise ToolError(code="tool_result_invalid", user_message=f"工具 {name} 返回了无法解析的结果")
        return result
