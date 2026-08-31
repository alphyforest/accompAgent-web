"""工具层契约：ToolSpec v2 + ToolError（规格 §3 / §9，SPEC-030 §3）。

- ToolSpec：工具的唯一契约（name/description/input_schema/executable + 来源/能力/风险/幂等等元数据）
- ToolError：工具调用错误统一结构，对话层只向用户呈现 user_message
- R5 起为 Pydantic v2（SPEC-030 §3 一次性迁移），构造即校验；
  无并行 ToolSpec；旧来源（MCP）转换时提供安全默认值（见 sources/mcp.py build_spec）

编码约束（规格 §11 / STD-010 §4）：
- 本模块只依赖标准库与 pydantic，不 import 任何引擎/记忆模块
- description 必须含用途、参数语义与示例（日历类工具在适配器层补足时间写法与查重语义）
"""

import re
from typing import Any, Awaitable, Callable, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 工具名约束：snake_case（对齐 agenda 工具命名）
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# 稳定能力分类（SPEC-030 §3）：本地策略覆盖外部来源的宽松声明
GenericCapability = "generic"

RiskLevel = Literal["low", "medium", "high"]
Idempotency = Literal["none", "natural", "keyed"]
ConfirmationPolicy = Literal["never", "conditional", "always"]


class ToolError(Exception):
    """工具调用错误（统一契约）。

    - code：结构化错误码，进日志
    - user_message：可直接呈现给用户的中文说明
    """

    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


class ToolSpec(BaseModel):
    """工具契约 v2（SPEC-030 §3）。

    新增（R5）：source_id / capability / tags / risk_level / idempotency /
    confirmation_policy / timeout_seconds；旧 MCP 来源在 build_spec 提供安全默认值。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    input_schema: Dict[str, Any]
    executable: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]

    # R5 元数据
    source_id: str = "unknown"
    capability: str = GenericCapability
    tags: List[str] = Field(default_factory=list)
    read_only: bool = False
    risk_level: RiskLevel = "low"
    idempotency: Idempotency = "none"
    confirmation_policy: ConfirmationPolicy = "never"
    timeout_seconds: float = Field(default=30.0, gt=0)
    disabled: bool = False

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not isinstance(value, str) or not _NAME_PATTERN.match(value):
            raise ValueError(f"工具名必须为 snake_case 非空字符串：{value!r}")
        return value

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("description 不能为空（必须含用途/参数语义/示例）")
        return value

    @field_validator("input_schema")
    @classmethod
    def _validate_schema(cls, value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("input_schema 必须是 JSON Schema 字典")
        return value

    @field_validator("capability")
    @classmethod
    def _validate_capability(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("capability 不能为空（如 calendar.read / calendar.write）")
        return value

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
