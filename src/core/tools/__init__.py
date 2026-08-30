"""工具引擎：契约（ToolSpec/ToolError）、注册表与 MCP 适配器。

依赖单向：ToolLoop（运行时） → ToolRegistry（工具层）；工具层禁止反向 import
任何对话引擎/记忆模块（规格 doc/04-专项设计/MCP_TOOL_ENGINE_SPEC.md §2）。
"""

from src.core.tools.registry import ToolRegistry
from src.core.tools.spec import ToolError, ToolSpec

__all__ = ["ToolError", "ToolRegistry", "ToolSpec"]
