"""应用配置，通过环境变量加载，支持 .env 文件。"""

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
    host: str = "0.0.0.0"
    port: int = 5000

    # 路径
    phrases_dir: str = "./phrases"
    config_dir: str = "./src/config/roles"
    data_dir: str = "./data"

    # 记忆
    max_history: int = 10

    # 事件
    global_cooldown: int = 3


# 全局配置单例；api_key 等必填项在运行时从 .env / 环境变量注入，
# mypy 静态分析无法感知，故此处忽略必填参数检查。
settings = Settings()  # type: ignore[call-arg]
