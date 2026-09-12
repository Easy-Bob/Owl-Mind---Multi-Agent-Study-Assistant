"""Configuration behaviour -- ISSUE-001 FR3.

Covers the acceptance criterion that a missing ANTHROPIC_API_KEY stops startup
with a message naming the setting, rather than surfacing on the first model
call hours later.
"""

from __future__ import annotations

import pytest

from owl_mind.core import config


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch, tmp_path):
    """Run each test against an empty directory so no local .env leaks in."""
    monkeypatch.chdir(tmp_path)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_missing_api_key_names_the_setting(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(config.MissingConfiguration) as excinfo:
        config.get_settings()

    message = str(excinfo.value)
    assert "ANTHROPIC_API_KEY" in message
    assert ".env.example" in message


def test_missing_api_key_exits_non_zero(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        config.load_settings_or_exit()

    assert excinfo.value.code == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_settings_are_read_once(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "first")
    first = config.get_settings()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "second")
    assert config.get_settings() is first, "settings must not be re-read per access"


def test_chroma_url_is_derived(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("CHROMA_HOST", "chroma")
    monkeypatch.setenv("CHROMA_PORT", "8000")

    assert config.get_settings().chroma_url == "http://chroma:8000"
