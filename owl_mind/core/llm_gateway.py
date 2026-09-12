"""LLMGateway -- the only module in Owl Mind permitted to call Anthropic.

STUB. Implements ISSUE-001 FR5 (the chokepoint and its contract).
Internals land with the LLM gateway issue; see plan section 3.5.

Why this file exists before it does anything
--------------------------------------------
The reference implementation grew nine scattered ``client.messages.create``
call sites across five modules. Adding token accounting afterwards meant
touching all nine and hoping none was missed. Owl Mind starts with one door,
enforced by ``tests/test_guardrails.py``, which fails if ``messages.create``
appears anywhere else under ``owl_mind/``.

Responsibilities when implemented (plan 3.5)
--------------------------------------------
- ``asyncio.Semaphore`` sized by ``settings.llm_max_concurrency`` -- backpressure,
  which the reference implementation lacked entirely.
- Capture ``resp.usage``: input, output, cache_read, cache_creation. Read every
  field with ``getattr(usage, name, 0)``; compatible providers omit some.
- Prometheus counters: ``llm_tokens_total{component,model,direction}``,
  ``llm_calls_total{component,model}``, ``llm_latency_ms{component}``.
- Per-request rollup via ``contextvars``, surfaced on the chat response.

``component`` is the attribution label and is required, not optional. Known
values: intent, agent:concept, agent:practice, agent:planner, agent:quiz,
composer, rewrite, rerank, profile, compress, merge, judge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from owl_mind.core.config import Settings


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


class LLMGateway:
    """Single entry point for model calls. Not yet implemented."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def complete(self, *, component: str, **kwargs: Any) -> Any:
        """Run one completion, recording tokens and latency under ``component``.

        Args:
            component: attribution label; see module docstring for known values.
            **kwargs: forwarded to the Anthropic messages API.

        Raises:
            NotImplementedError: implemented by the LLM gateway issue.
        """
        raise NotImplementedError(
            "LLMGateway.complete is a scaffold stub (ISSUE-001). "
            "Implemented by the LLM gateway issue; see plan section 3.5."
        )
