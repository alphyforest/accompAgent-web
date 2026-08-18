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
    assert settings.global_cooldown == 3
    assert settings.port == 5000


def test_settings_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
