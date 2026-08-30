"""工具层契约：ToolSpec 与 ToolError（规格 §3 / §9）。

- ToolSpec：工具的唯一契约（name/description/input_schema/executable + 可用性与确认标记）
- ToolError：工具调用错误统一结构，对话层只向用户呈现 user_message

编码约束（规格 §11）：
- 本模块只依赖标准库，不 import 任何引擎/记忆模块
- description 必须含用途、参数语义与示例（日历类工具在适配器层补足时间写法与查重语义）
"""

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict

# 工具名约束：snake_case（对齐 agenda 工具命名）
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ToolError(Exception):
    """工具调用错误（统一契约）。

    - code：结构化错误码，进日志
    - user_message：可直接呈现给用户的中文说明
    """

    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


@dataclass
class ToolSpec:
    """工具契约（对齐 OpenAI function calling 的 parameters 直通）。

    - name：唯一、snake_case
    - description：含用途、参数语义与示例；日历类必须说明时间写法与"先查重"语义
    - input_schema：JSON Schema 字典（与 tools 参数直通）
    - executable：异步可调用，入参 (args: dict) -> dict（结构化结果）
    - read_only：来自 MCP readOnlyHint，用于日志与（未来）确认策略
    - require_confirmation：预留给将来确认策略，当前恒 False（ADR-004 自动执行）
    - disabled：可用性标记，注册表可整体/单条禁用
    """

    name: str
    description: str
    input_schema: Dict[str, Any]
    executable: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
    read_only: bool = False
    require_confirmation: bool = False
    disabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_PATTERN.match(self.name):
            raise ValueError(f"工具名必须为 snake_case 非空字符串：{self.name!r}")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(f"工具 {self.name} 的 description 不能为空（必须含用途/参数语义/示例）")
        if not isinstance(self.input_schema, dict):
            raise ValueError(f"工具 {self.name} 的 input_schema 必须是 JSON Schema 字典")
        if not callable(self.executable):
            raise ValueError(f"工具 {self.name} 的 executable 必须是可调用对象")

    def to_openai_schema(self) -> Dict[str, Any]:
        """转换为 OpenAI function calling 的 tools 条目（与注册表直通）。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }
