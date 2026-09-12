"""Application settings -- the single place this project reads the environment.

Implements ISSUE-001 FR3.

No other module may touch ``os.environ``. ``tests/test_guardrails.py`` enforces
that, so a setting added here is a setting the whole codebase can rely on.

``get_settings()`` is cached, which gives the read-once semantics FR3 asks for
while still letting tests clear the cache and re-read a modified environment.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import Literal

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed configuration. See ``.env.example`` for every key."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # -- Model access ----------------------------------------------------
    # No default: a missing key must fail at startup, not on the first call.
    anthropic_api_key: str = Field(min_length=1)
    model: str = "claude-sonnet-5"

    # -- Dependencies ----------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    chroma_host: str = "localhost"
    chroma_port: int = 8000

    # -- Runtime ---------------------------------------------------------
    app_env: Literal["local", "dev", "prod"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Ceiling on concurrent Anthropic calls. Consumed by LLMGateway's
    # semaphore -- see core/llm_gateway.py and plan section 3.5.
    llm_max_concurrency: int = Field(default=8, ge=1, le=64)

    @property
    def chroma_url(self) -> str:
        return f"http://{self.chroma_host}:{self.chroma_port}"


class MissingConfiguration(RuntimeError):
    """Raised when required settings are absent, with a usable message."""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build (once) and return the settings object.

    Raises:
        MissingConfiguration: with the offending field names spelled out. A
            stack trace of pydantic internals is not a useful thing to hand
            someone whose only mistake was forgetting to copy .env.example.
    """
    try:
        return Settings()  # type: ignore[call-arg]  # values come from env
    except ValidationError as exc:
        missing = sorted({str(error["loc"][0]).upper() for error in exc.errors()})
        raise MissingConfiguration(
            "Owl Mind cannot start. Missing or invalid settings: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill it in."
        ) from None


def load_settings_or_exit() -> Settings:
    """Entry-point helper: report the problem plainly and exit non-zero.

    Used by the API process so a misconfigured container stops instead of
    serving a degraded app (FR4).
    """
    try:
        return get_settings()
    except MissingConfiguration as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
