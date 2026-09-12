"""/health behaviour -- ISSUE-001 FR4.

The contract worth protecting: /health answers even when a dependency is down,
because that is precisely when someone is reading it.
"""

from __future__ import annotations

import pytest

from owl_mind import __version__
from owl_mind.api import main


def test_health_reports_structure(client):
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {
        "status",
        "app_env",
        "version",
        "agents",
        "contracts",
        "dependencies",
    }
    assert body["version"] == __version__
    assert body["status"] in {"ok", "degraded"}
    assert set(body["dependencies"]) == {"redis", "chroma"}


def test_health_reports_no_agents_before_the_agent_issue(client):
    """The roster is empty until agents are registered in the pool.

    /health reports what is running, not what is declared in AgentType.
    """
    assert client.get("/health").json()["agents"] == []


@pytest.mark.parametrize("broken", ["redis", "chroma"])
def test_health_degrades_without_crashing(client, monkeypatch, broken):
    """A dead dependency yields 200 + degraded, never a 500 or a timeout."""

    async def unreachable(settings):
        return {"reachable": False, "detail": "ConnectionError: simulated"}

    async def reachable(settings):
        return {"reachable": True, "detail": "simulated"}

    monkeypatch.setattr(main, "check_redis", unreachable if broken == "redis" else reachable)
    monkeypatch.setattr(main, "check_chroma", unreachable if broken == "chroma" else reachable)

    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"][broken]["reachable"] is False
    assert "simulated" in body["dependencies"][broken]["detail"]


def test_health_is_ok_when_every_dependency_answers(client, monkeypatch):
    async def reachable(settings):
        return {"reachable": True, "detail": "simulated"}

    monkeypatch.setattr(main, "check_redis", reachable)
    monkeypatch.setattr(main, "check_chroma", reachable)

    assert client.get("/health").json()["status"] == "ok"


def test_metrics_endpoint_serves_prometheus_text(client):
    """ISSUE-002 FR4. The scrape target for per-component token accounting."""
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]

    body = response.text
    for family in ("llm_tokens_total", "llm_calls_total", "llm_latency_ms"):
        assert family in body, f"{family} missing from /metrics"


def test_gateway_is_available_on_app_state(client):
    """The lifespan builds one gateway for the process, not one per request."""
    from owl_mind.api.main import app

    assert app.state.gateway is not None
