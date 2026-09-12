"""Shared fixtures.

The environment is populated before ``owl_mind.core.config`` is imported by any
test, so the suite never depends on a developer's local .env.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "owl_mind"

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("APP_ENV", "local")
# Unroutable by design: dependency probes must fail fast rather than hang, and
# tests that care about reachability monkeypatch the probes directly.
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")
os.environ.setdefault("CHROMA_HOST", "127.0.0.1")
os.environ.setdefault("CHROMA_PORT", "8999")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def package_root() -> Path:
    return PACKAGE_ROOT


@pytest.fixture(scope="session")
def source_files() -> list[Path]:
    """Every Python file shipped in the package."""
    return sorted(PACKAGE_ROOT.rglob("*.py"))


@pytest.fixture()
def client() -> Iterator[object]:
    """TestClient with the app lifespan run, so app.state.settings exists."""
    from fastapi.testclient import TestClient

    from owl_mind.api.main import app

    with TestClient(app) as test_client:
        yield test_client
