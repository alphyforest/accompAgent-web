"""配置单元测试。"""

import pytest
from pydantic import ValidationError
from src.config.settings import Settings


def test_settings_defaults(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    settings = Settings(_env_file=None)
    assert settings.deepseek_api_key == "sk-test"
    assert settings.deepseek_model == "deepseek-v4-flash"
    assert settings.max_history == 10
    assert settings.port == 5000
    # 第三阶段：MCP 工具引擎配置默认值（R6 起未配置默认不可用）
    assert settings.agenda_mcp_enabled is True
    assert settings.agenda_mcp_command == ""
    assert settings.agenda_mcp_args == []
    assert settings.agenda_data_path == ""
    assert settings.agenda_data_backup_dir == ""
    assert settings.agenda_tool_rounds == 4
    assert settings.agenda_tool_timeout == 30
    assert settings.agenda_tool_overall_timeout == 120


def test_settings_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
