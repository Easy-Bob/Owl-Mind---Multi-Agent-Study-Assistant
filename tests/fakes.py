"""Test doubles. ISSUE-002 FR8: the default suite makes no network calls.

FakeAnthropic mimics only the surface the gateway touches --
``client.messages.create(...)`` returning an object with ``usage``. It also
tracks peak concurrency, which is how the semaphore ceiling is verified.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeUsage:
    """Mirrors the SDK usage object.

    Fields default to None rather than 0 so a test can model a provider that
    omits them entirely -- the case TokenUsage.from_response defends against.
    """

    input_tokens: int | None = 100
    output_tokens: int | None = 50
    cache_read_input_tokens: int | None = 0
    cache_creation_input_tokens: int | None = 0


@dataclass
class FakeResponse:
    usage: Any = field(default_factory=FakeUsage)
    content: list[Any] = field(default_factory=list)
    # The agent tool loop continues only while the model asks for tools, so a
    # fake that never sets this ends the loop after one round -- which is the
    # right default for the many tests that do not exercise tools at all.
    stop_reason: str = "end_turn"
    _request_id: str = "req_fake_0001"


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeToolUseBlock:
    """Mirrors a tool_use content block: id, name, and parsed input."""

    name: str
    input: dict[str, Any] = field(default_factory=dict)
    id: str = "toolu_fake_0001"
    type: str = "tool_use"


class _FakeMessages:
    def __init__(self, owner: FakeAnthropic) -> None:
        self._owner = owner

    async def create(self, **kwargs: Any) -> Any:
        owner = self._owner
        owner.calls.append(kwargs)

        owner.in_flight += 1
        owner.peak_in_flight = max(owner.peak_in_flight, owner.in_flight)
        try:
            if owner.delay:
                await asyncio.sleep(owner.delay)
            else:
                # Yield control so concurrent callers actually interleave;
                # without this the ceiling test could pass trivially.
                await asyncio.sleep(0)
            if owner.raises is not None:
                raise owner.raises
            picker = getattr(owner, "next_response", None)
            return picker() if picker is not None else owner.response
        finally:
            owner.in_flight -= 1


class FakeAnthropic:
    """Stand-in for AsyncAnthropic."""

    def __init__(
        self,
        response: Any | None = None,
        raises: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.response = response if response is not None else FakeResponse()
        self.raises = raises
        self.delay = delay
        self.calls: list[dict[str, Any]] = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self.closed = False
        self.messages = _FakeMessages(self)

    async def close(self) -> None:
        self.closed = True


class ScriptedAnthropic(FakeAnthropic):
    """Returns a different response per call, so a tool loop can be scripted.

    The single-response FakeAnthropic cannot express "ask for a tool, then
    answer": it would ask for the same tool forever and the loop would only
    ever exit by exhaustion.
    """

    def __init__(self, responses: list[Any], delay: float = 0.0) -> None:
        super().__init__(response=responses[0], delay=delay)
        self._responses = list(responses)

    def next_response(self) -> Any:
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]
