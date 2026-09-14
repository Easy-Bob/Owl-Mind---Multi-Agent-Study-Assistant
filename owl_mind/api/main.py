"""FastAPI application: lifespan, logging, and /health.

Implements ISSUE-001 FR4.

This module deliberately contains no domain logic. Routing, agents, memory and
materials arrive in later issues; what is here is the shell they plug into and
the startup sequence that refuses to serve a misconfigured app.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from owl_mind import __version__

# Imported for their side effect: each registers startup contracts. Without the
# import the check is never registered, and drift surfaces as a confusing
# request-time failure rather than a failed boot. Note the sharp edge recorded
# as ISSUE-006 H2 -- a contract that was never imported is indistinguishable
# from one that passed, because nothing asserts how many should be registered.
from owl_mind.agents import roster  # noqa: F401
from owl_mind.agents.orchestrator import AgentOrchestrator
from owl_mind.api.chat import router as chat_router
from owl_mind.core.config import Settings, load_settings_or_exit
from owl_mind.core.contracts import registered_contracts, verify_startup_contracts
from owl_mind.core.intent_recognizer import ChromaTemplateIndex, IntentRecognizer
from owl_mind.core.llm_gateway import LLMGateway

logger = logging.getLogger("owl_mind")

# A dependency probe that hangs is worse than one that fails: /health must stay
# answerable when a backing service is down, because that is exactly when
# someone is reading it.
PROBE_TIMEOUT_SECONDS = 2.0


def configure_logging(level: str) -> None:
    """Configure root logging once, at startup."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        force=True,
    )


async def check_redis(settings: Settings) -> dict[str, Any]:
    """Ping Redis. Never raises -- an unreachable dependency is a result."""
    client = aioredis.from_url(
        settings.redis_url,
        socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
        socket_timeout=PROBE_TIMEOUT_SECONDS,
    )
    try:
        await client.ping()
        return {"reachable": True, "detail": settings.redis_url}
    except Exception as exc:
        return {"reachable": False, "detail": f"{type(exc).__name__}: {exc}"}
    finally:
        await client.aclose()


async def check_chroma(settings: Settings) -> dict[str, Any]:
    """Hit the Chroma heartbeat. Never raises."""
    # Chroma moved the heartbeat from /api/v1 to /api/v2; try the current path
    # first and fall back, so the probe does not depend on the image tag.
    paths = ("/api/v2/heartbeat", "/api/v1/heartbeat")
    try:
        async with httpx.AsyncClient(
            base_url=settings.chroma_url, timeout=PROBE_TIMEOUT_SECONDS
        ) as client:
            for path in paths:
                response = await client.get(path)
                if response.status_code == 200:
                    return {"reachable": True, "detail": f"{settings.chroma_url}{path}"}
            return {
                "reachable": False,
                "detail": f"heartbeat returned {response.status_code}",
            }
    except Exception as exc:
        return {"reachable": False, "detail": f"{type(exc).__name__}: {exc}"}


def registered_agents(app: FastAPI) -> list[str]:
    """Names of agents registered in the orchestrator pool.

    Reports what is *running*, not what is declared in ``AgentType`` -- a type
    with no instance cannot take a request, and /health should not imply
    otherwise. Read from the live pool rather than duplicated here: a hardcoded
    list would contradict the FR7 contract the moment the two disagreed, and
    /health is exactly where that lie would be believed (ISSUE-006 H1).
    """
    orchestrator: AgentOrchestrator | None = getattr(app.state, "orchestrator", None)
    return [] if orchestrator is None else orchestrator.registered_agents()


async def _build_template_index(settings: Settings) -> Any:
    """Seed the Chroma template index, or return None and run on two signals.

    ISSUE-008 FR3, decided: wire it. The alternative was to ship a recogniser
    whose WEIGHTS name a signal that never votes -- 0.35 of the designed panel
    silently absent on every production request.

    Seeding must not take the boot down with it. A startup that dies because a
    *degraded* dependency is unreachable contradicts /health's entire design:
    the endpoint exists to report Chroma as unreachable, which it cannot do
    from a process that refused to start. Intent recognition is correct without
    the index -- _fuse divides by the weight that actually contributed, so the
    thresholds still mean what they say on two signals. It is just less robust
    to paraphrase, which is a degradation, not an outage.
    """
    if not settings.intent_index_enabled:
        logger.info("intent template index disabled by config; two signals")
        return None

    try:
        import chromadb

        client = await asyncio.to_thread(
            chromadb.HttpClient, host=settings.chroma_host, port=settings.chroma_port
        )
        index = ChromaTemplateIndex(client)
        await index.seed()
    except Exception as exc:  # noqa: BLE001 -- any failure here is degradation
        logger.warning(
            "intent template index unavailable (%s: %s); running on two signals",
            type(exc).__name__,
            exc,
        )
        return None

    logger.info("intent template index seeded")
    return index


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load settings, configure logging, verify contracts. Abort on failure."""
    settings = load_settings_or_exit()
    configure_logging(settings.log_level)

    # Raises ContractViolation, which aborts startup. Serving an app whose
    # invariants do not hold is worse than not serving.
    verify_startup_contracts()

    app.state.settings = settings
    # One gateway for the process. Constructing the Anthropic client performs
    # no network I/O, so a misconfigured key still fails at call time with the
    # provider's own error rather than here.
    app.state.gateway = LLMGateway(settings)
    # One orchestrator, holding one instance per agent type. Constructed after
    # the contracts have verified the roster is complete, so a missing role is
    # a failed boot rather than a KeyError on the first request that needs it.
    app.state.orchestrator = AgentOrchestrator(app.state.gateway)
    # Intent recognition, with the template index attached if Chroma is up.
    app.state.intent_recognizer = IntentRecognizer(
        app.state.gateway,
        index=await _build_template_index(settings),
    )

    logger.info(
        "Owl Mind %s starting (env=%s, model=%s)",
        __version__,
        settings.app_env,
        settings.model,
    )
    try:
        yield
    finally:
        await app.state.gateway.aclose()
        logger.info("Owl Mind shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Owl Mind",
        description="Multi-agent computer-science study assistant",
        version=__version__,
        lifespan=lifespan,
    )

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, Any]:
        """Liveness plus dependency reachability.

        Always HTTP 200. A caller that cannot reach Redis needs a readable
        answer, not a 500 that tells them nothing about which dependency broke.
        """
        settings: Settings = app.state.settings
        dependencies = {
            "redis": await check_redis(settings),
            "chroma": await check_chroma(settings),
        }
        healthy = all(dep["reachable"] for dep in dependencies.values())
        return {
            "status": "ok" if healthy else "degraded",
            "app_env": settings.app_env,
            "version": __version__,
            "agents": registered_agents(app),
            "contracts": registered_contracts(),
            "dependencies": dependencies,
        }

    app.include_router(chat_router)

    @app.get("/metrics", tags=["ops"])
    async def metrics() -> Response:
        """Prometheus scrape endpoint.

        Unauthenticated. Fine locally; note that it exposes usage volume before
        this is deployed anywhere public.
        """
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
