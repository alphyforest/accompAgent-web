"""应用配置，通过环境变量加载，支持 .env 文件。"""

from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。

    环境变量通过字段名自动映射（大小写不敏感）：
    ``deepseek_api_key`` -> ``DEEPSEEK_API_KEY``，其余字段同理。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DeepSeek
    deepseek_api_key: str = Field(...)
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    reasoning_effort: str = "low"

    # 服务
    host: str = "127.0.0.1"
    port: int = 5000

    # 路径
    phrases_dir: str = "./phrases"
    config_dir: str = "./src/config/roles"
    data_dir: str = "./data"

    # 记忆
    max_history: int = 10

    # 事件
    global_cooldown: int = 3

    # 第二阶段：语义化长期记忆
    memory_db_path: str = "./data/memory.db"  # 长期记忆 SQLite 文件路径
    memory_idle_timeout_minutes: int = 15  # 惰性总结：空闲超时（分钟）后视为上一会话结束
    memory_segment_max_messages: int = 30  # 惰性总结：条数阈值兜底分段
    memory_inject_top_k: int = 8  # 记忆注入 top-k 上限
    memory_forget_days: int = 30  # 遗忘：超 N 天未引用降权
    memory_forget_decay: int = 2  # 遗忘：每次降权分数
    # 即时抽取（方案 B：function calling 实时写入）：
    # 用户消息命中关键字时，后台异步调 LLM 函数调用抽取画像/事实即时入库，不阻塞回复。
    memory_instant_enabled: bool = True  # 总开关
    memory_instant_keywords: List[str] = [  # 命中任一即触发抽取的用户信息信号
        "我叫",
        "我是",
        "我的",
        "我喜欢",
        "我不喜欢",
        "我讨厌",
        "我住在",
        "我在",
        "我正在",
        "我最近",
        "我养",
        "我有",
        "我准备",
        "我在准备",
        "我打算",
        "我计划",
        "我有点",
        "我想",
        "我想要",
        "我工作",
        "我学",
        "我家",
        "可以叫我",
        "请别",
        "不要这样",
    ]

    # 第三阶段：MCP 工具引擎（Agenda MCP Server 接入，规格 §10）
    # 总开关；关闭则 agenda 工具不入注册表（内置 now 演示工具仍保留）
    agenda_mcp_enabled: bool = True
    # 启动器：node + tsx CLI 入口（Windows 避免直接 spawn .cmd；可含 F:\lab\agenda1 绝对路径）
    agenda_mcp_command: str = "node"
    agenda_mcp_args: List[str] = [
        r"F:\lab\agenda1\node_modules\tsx\dist\cli.mjs",
        r"F:\lab\agenda1\mcp\server.ts",
    ]
    # 共享数据文件（与 agenda 桌面端同一文件）
    agenda_data_path: str = r"F:\lab\agenda1\agenda-data.json"
    # ToolLoop 参数
    agenda_tool_rounds: int = 4
    agenda_tool_timeout: int = 30
    agenda_tool_overall_timeout: int = 120
    # 每日备份目录（规格 §7）
    agenda_data_backup_dir: str = "./data/agenda_backup"


# 全局配置单例；api_key 等必填项在运行时从 .env / 环境变量注入，
# mypy 静态分析无法感知，故此处忽略必填参数检查。
settings = Settings()  # type: ignore[call-arg]
