"""LLMGateway -- the only module in Owl Mind permitted to call Anthropic.

Implements ISSUE-002. Plan reference: section 3.5, section 11.3 (highlight 9).

Why one door
------------
The reference implementation grew nine ``client.messages.create`` call sites
across five modules. Adding token accounting afterwards meant touching all nine
and hoping none was missed; per-component attribution was impossible without
editing every one. Owl Mind has one door, enforced by
``tests/test_guardrails.py``, which fails if ``messages.create`` appears
anywhere else under ``owl_mind/``.

What the gateway does
---------------------
- Requires a ``component`` label on every call, so no cost is unattributable.
- Captures token usage, including the cache fields, defensively.
- Holds a semaphore so a burst of parallel agents cannot exhaust the rate limit.
- Emits Prometheus counters, scraped at ``/metrics``.
- Accumulates a per-request rollup through a contextvar.

What it deliberately does not do
--------------------------------
- No retry. The SDK already retries 408/409/429/5xx with backoff; a second
  layer multiplies the wall-clock timeout for no benefit.
- No prompt construction. ``cache_control`` placement belongs to whoever builds
  the prompt, not here.
- No sampling parameters. ``temperature``, ``top_p``, and ``top_k`` are rejected
  with a 400 on the current models; reproducibility comes from deterministic
  tools instead (see agents/base.py).
- No domain knowledge. It does not know what an agent is.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import anthropic
from anthropic import AsyncAnthropic
from prometheus_client import Counter, Histogram

from owl_mind.core.config import Settings

logger = logging.getLogger(__name__)


# Every call must carry one of these. A label that is not on the list is a
# typo or an unplanned call site; either way it becomes cost nobody can
# attribute, so it is rejected before the request is made.
KNOWN_COMPONENTS: frozenset[str] = frozenset(
    {
        "intent",
        "agent:concept",
        "agent:practice",
        "agent:planner",
        "agent:quiz",
        "composer",
        "rewrite",
        "rerank",
        "profile",
        "compress",
        "merge",
        "judge",
    }
)


# ---------------------------------------------------------------------------
# Metrics
#
# Label cardinality is bounded on purpose: component and model are closed sets.
# Never add a user id, session id, or free text as a label -- that turns a
# counter into an unbounded series and takes the scrape down with it.
# ---------------------------------------------------------------------------

LLM_TOKENS = Counter(
    "llm_tokens_total",
    "Tokens consumed, by component and direction.",
    ["component", "model", "direction"],
)

LLM_CALLS = Counter(
    "llm_calls_total",
    "Model calls, by component and outcome.",
    ["component", "model", "outcome"],
)

LLM_LATENCY = Histogram(
    "llm_latency_ms",
    "Model call latency in milliseconds, by component.",
    ["component"],
    buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000),
)


@dataclass(frozen=True)
class TokenUsage:
    """Token counts for a single completion, or a summed rollup."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_creation_tokens=self.cache_creation_tokens + other.cache_creation_tokens,
        )

    @classmethod
    def from_response(cls, usage: Any) -> TokenUsage:
        """Build from an SDK usage object.

        Every field is read with a default. SDK versions and compatible
        providers omit fields, and a request must not fail over a metric.
        """
        return cls(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )


@dataclass
class RequestUsage:
    """Mutable rollup for one request, shared across concurrent agent calls."""

    llm_calls: int = 0
    total: TokenUsage = TokenUsage()
    by_component: dict[str, TokenUsage] = field(default_factory=dict)

    def record(self, component: str, usage: TokenUsage) -> None:
        self.llm_calls += 1
        self.total = self.total + usage
        self.by_component[component] = self.by_component.get(component, TokenUsage()) + usage

    @property
    def tokens_in(self) -> int:
        return self.total.input_tokens

    @property
    def tokens_out(self) -> int:
        return self.total.output_tokens

    def as_dict(self) -> dict[str, Any]:
        """Shape surfaced on the chat response by a later issue."""
        return {
            "llm_calls": self.llm_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_by_component": {
                component: usage.total for component, usage in self.by_component.items()
            },
        }


# The rollup is shared by reference. Tasks created inside a request scope copy
# the *binding*, not the object, so a gathered task mutating the rollup is
# visible to the parent. That is what makes parallel agent dispatch add up --
# and why the gather test in tests/test_llm_gateway.py matters: if this ever
# stops holding, totals are simply low and nothing raises.
_request_usage: contextvars.ContextVar[RequestUsage | None] = contextvars.ContextVar(
    "owl_mind_request_usage", default=None
)


class UnknownComponent(ValueError):
    """A call carried a component label that is not in KNOWN_COMPONENTS."""


class LLMTimeout(TimeoutError):
    """A call exceeded ``llm_timeout_seconds`` (ISSUE-006 F8).

    Distinct from ``anthropic.APITimeoutError``, which is the SDK's own
    per-request timeout and is *retried* by the SDK -- so its effective ceiling
    is ``timeout x (max_retries + 1)``. This one is a hard deadline enforced
    with ``asyncio.timeout`` and is not retried by anyone.
    """


class LLMGateway:
    """Single entry point for model calls."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        # `client` is injected by tests. Production passes nothing and gets a
        # real AsyncAnthropic; constructing it performs no network I/O.
        self._client = client or AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._semaphore = asyncio.Semaphore(settings.llm_max_concurrency)
        self._timeout = settings.llm_timeout_seconds

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()

    @asynccontextmanager
    async def request_scope(self) -> AsyncIterator[RequestUsage]:
        """Accumulate usage for one request.

        Yields the rollup, which is readable after the block exits.

        Nesting reuses the active rollup rather than starting a new one. /chat
        opens a scope around intent recognition *and* orchestration, and the
        orchestrator opens one around its own fan-out. Re-binding on the inner
        scope would hand the agents a fresh rollup and leave the outer one
        holding the intent call alone -- so a composite turn would report the
        cost of its classifier and none of the three calls that answered it.
        One rollup per request, no matter how many layers ask for one.
        """
        existing = _request_usage.get()
        if existing is not None:
            yield existing
            return

        rollup = RequestUsage()
        token = _request_usage.set(rollup)
        try:
            yield rollup
        finally:
            _request_usage.reset(token)

    async def complete(self, *, component: str, **kwargs: Any) -> Any:
        """Run one completion, recording tokens and latency under ``component``.

        Args:
            component: attribution label; must be in KNOWN_COMPONENTS.
            **kwargs: forwarded to the Anthropic messages API. ``model``
                defaults to the configured model and may be overridden per call,
                so a later issue can route cheap components elsewhere without
                touching callers.

        Raises:
            UnknownComponent: before any network call, if the label is unknown.
            LLMTimeout: the call exceeded ``llm_timeout_seconds``.
            anthropic.APIError: propagated unchanged. The gateway observes
                failures; it does not decide what to do about them.
        """
        if component not in KNOWN_COMPONENTS:
            raise UnknownComponent(
                f"unknown component {component!r}. Add it to KNOWN_COMPONENTS in "
                "core/llm_gateway.py -- an unlabelled call is unattributable cost."
            )

        model = kwargs.pop("model", None) or self._settings.model
        started = time.monotonic()

        try:
            # The deadline covers the wait for a slot as well as the call. A
            # deadline on the call alone would leave queue time unbounded,
            # which is the half that actually grows under load: the semaphore
            # is per process, so a burst puts every caller behind it.
            #
            # This is deliberately not the SDK's `timeout` parameter. That one
            # is retried -- wall clock reaches timeout x (max_retries + 1) --
            # so it cannot state a ceiling. asyncio.timeout can.
            async with asyncio.timeout(self._timeout):
                # The semaphore is held for the API call only. Bookkeeping
                # below does not need a slot and holding one for it would
                # shrink the effective concurrency.
                async with self._semaphore:
                    response = await self._client.messages.create(model=model, **kwargs)
        except TimeoutError as exc:
            self._record_error(component, model, started)
            logger.warning(
                "llm call timed out after %.1fs: component=%s model=%s",
                self._timeout,
                component,
                model,
            )
            raise LLMTimeout(
                f"{component} exceeded the {self._timeout:.0f}s deadline"
            ) from exc
        except anthropic.APIStatusError as exc:
            self._record_error(component, model, started)
            logger.warning(
                "llm call failed: component=%s model=%s status=%s request_id=%s",
                component,
                model,
                exc.status_code,
                getattr(exc, "request_id", None),
            )
            raise
        except anthropic.APIConnectionError:
            self._record_error(component, model, started)
            logger.warning(
                "llm call failed to connect: component=%s model=%s", component, model
            )
            raise
        except Exception:
            self._record_error(component, model, started)
            logger.exception("llm call raised: component=%s model=%s", component, model)
            raise

        self._record_success(component, model, started, response)
        return response

    # -- bookkeeping --------------------------------------------------------

    def _record_error(self, component: str, model: str, started: float) -> None:
        LLM_CALLS.labels(component=component, model=model, outcome="error").inc()
        LLM_LATENCY.labels(component=component).observe(_elapsed_ms(started))

    def _record_success(
        self, component: str, model: str, started: float, response: Any
    ) -> None:
        usage = TokenUsage.from_response(getattr(response, "usage", None))

        # A truncated response is a third outcome, not a success and not an
        # error (ISSUE-006 F2). The HTTP call succeeded; the content is
        # incomplete. Without this label the failure is invisible: the caller
        # gets unparseable JSON, logs a warning, and returns an empty signal,
        # and the request still answers -- just worse, and silently.
        #
        # The label set stays closed: stop_reason has a handful of values and
        # only this one is folded in, so cardinality does not grow with traffic.
        stop_reason = getattr(response, "stop_reason", None)
        outcome = "truncated" if stop_reason == "max_tokens" else "success"
        if outcome == "truncated":
            logger.warning(
                "llm response truncated: component=%s model=%s -- raise max_tokens "
                "or disable thinking for this component",
                component,
                model,
            )

        LLM_CALLS.labels(component=component, model=model, outcome=outcome).inc()
        LLM_LATENCY.labels(component=component).observe(_elapsed_ms(started))
        for direction, amount in (
            ("input", usage.input_tokens),
            ("output", usage.output_tokens),
            ("cache_read", usage.cache_read_tokens),
            ("cache_creation", usage.cache_creation_tokens),
        ):
            if amount:
                LLM_TOKENS.labels(
                    component=component, model=model, direction=direction
                ).inc(amount)

        rollup = _request_usage.get()
        if rollup is not None:
            rollup.record(component, usage)

        logger.debug(
            "llm call ok: component=%s model=%s in=%d out=%d request_id=%s",
            component,
            model,
            usage.input_tokens,
            usage.output_tokens,
            getattr(response, "_request_id", None),
        )


def _elapsed_ms(started: float) -> float:
    """Monotonic elapsed milliseconds. Never time.time() -- it can go backwards."""
    return (time.monotonic() - started) * 1000
