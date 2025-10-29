import importlib

import pytest

import config as config_module


@pytest.fixture(autouse=True)
def reset_config(monkeypatch):
    yield
    monkeypatch.delenv("AI_HISTORY_MESSAGE_LIMIT", raising=False)
    importlib.reload(config_module)


def _reload_config(monkeypatch, **env):
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return importlib.reload(config_module)


def test_history_limit_uses_default_when_empty(monkeypatch):
    config = _reload_config(monkeypatch, AI_HISTORY_MESSAGE_LIMIT="")
    assert config.Config.AI_HISTORY_MESSAGE_LIMIT == 6


def test_history_limit_clamped_to_zero(monkeypatch):
    config = _reload_config(monkeypatch, AI_HISTORY_MESSAGE_LIMIT="-5")
    assert config.Config.AI_HISTORY_MESSAGE_LIMIT == 0


def test_history_limit_accepts_positive_values(monkeypatch):
    config = _reload_config(monkeypatch, AI_HISTORY_MESSAGE_LIMIT="8")
    assert config.Config.AI_HISTORY_MESSAGE_LIMIT == 8
