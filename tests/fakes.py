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
    _request_id: str = "req_fake_0001"


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
            return owner.response
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
