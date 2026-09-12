"""LLMGateway behaviour -- ISSUE-002 FR1-FR6, FR8.

Every test here runs against FakeAnthropic. The suite makes no network calls;
the one test that does is marked ``live`` and deselected by default.
"""

from __future__ import annotations

import asyncio

import anthropic
import httpx
import pytest

from owl_mind.core.config import get_settings
from owl_mind.core.llm_gateway import (
    KNOWN_COMPONENTS,
    LLM_CALLS,
    LLM_TOKENS,
    LLMGateway,
    TokenUsage,
    UnknownComponent,
)
from tests.fakes import FakeAnthropic, FakeResponse, FakeUsage


@pytest.fixture()
def gateway() -> LLMGateway:
    return LLMGateway(get_settings(), client=FakeAnthropic())


def _counter(metric, **labels) -> float:
    """Current value of one labelled counter, 0.0 if never incremented."""
    return metric.labels(**labels)._value.get()


# -- FR1: the chokepoint ---------------------------------------------------


async def test_rejects_unknown_component_before_calling(gateway):
    client: FakeAnthropic = gateway._client

    with pytest.raises(UnknownComponent):
        await gateway.complete(component="agent:nonexistent", max_tokens=16, messages=[])

    assert client.calls == [], "the request must not be made"


async def test_rejects_empty_component(gateway):
    with pytest.raises(UnknownComponent):
        await gateway.complete(component="", max_tokens=16, messages=[])


async def test_model_defaults_to_settings_and_is_overridable(gateway):
    client: FakeAnthropic = gateway._client

    await gateway.complete(component="intent", max_tokens=16, messages=[])
    assert client.calls[0]["model"] == get_settings().model

    await gateway.complete(
        component="intent", model="claude-haiku-4-5", max_tokens=16, messages=[]
    )
    assert client.calls[1]["model"] == "claude-haiku-4-5"


async def test_never_sends_sampling_parameters(gateway):
    """The gateway adds none of its own; a caller cannot inherit them either."""
    client: FakeAnthropic = gateway._client
    await gateway.complete(component="intent", max_tokens=16, messages=[])

    sent = client.calls[0]
    assert "temperature" not in sent
    assert "top_p" not in sent
    assert "top_k" not in sent


# -- FR2: token capture ----------------------------------------------------


def test_token_usage_reads_all_four_fields():
    usage = TokenUsage.from_response(
        FakeUsage(
            input_tokens=1000,
            output_tokens=200,
            cache_read_input_tokens=800,
            cache_creation_input_tokens=50,
        )
    )
    assert (usage.input_tokens, usage.output_tokens) == (1000, 200)
    assert (usage.cache_read_tokens, usage.cache_creation_tokens) == (800, 50)
    assert usage.total == 2050


def test_token_usage_survives_missing_fields():
    """A provider that omits the cache fields must not break a request."""

    class BareUsage:
        input_tokens = 10
        output_tokens = 5

    usage = TokenUsage.from_response(BareUsage())
    assert usage.cache_read_tokens == 0
    assert usage.cache_creation_tokens == 0


def test_token_usage_survives_no_usage_object_at_all():
    assert TokenUsage.from_response(None).total == 0


# -- FR3: backpressure -----------------------------------------------------


async def test_concurrency_ceiling_holds():
    settings = get_settings().model_copy(update={"llm_max_concurrency": 2})
    client = FakeAnthropic(delay=0.01)
    gateway = LLMGateway(settings, client=client)

    await asyncio.gather(
        *(
            gateway.complete(component="intent", max_tokens=16, messages=[])
            for _ in range(10)
        )
    )

    assert len(client.calls) == 10, "every call must still complete"
    assert client.peak_in_flight <= 2, (
        f"semaphore breached: {client.peak_in_flight} calls were in flight at once"
    )


# -- FR5: per-request rollup ----------------------------------------------


async def test_rollup_accumulates_across_components(gateway):
    async with gateway.request_scope() as usage:
        await gateway.complete(component="intent", max_tokens=16, messages=[])
        await gateway.complete(component="agent:concept", max_tokens=16, messages=[])

    assert usage.llm_calls == 2
    assert usage.tokens_in == 200
    assert usage.tokens_out == 100
    assert set(usage.by_component) == {"intent", "agent:concept"}


async def test_rollup_survives_asyncio_gather():
    """The failure this guards is silent undercounting, not an exception.

    Parallel agent dispatch is the point of the routing layer, so if the
    contextvar does not reach gathered tasks the headline token numbers are
    quietly wrong and nothing complains.
    """
    gateway = LLMGateway(get_settings(), client=FakeAnthropic(delay=0.005))

    async with gateway.request_scope() as usage:
        await asyncio.gather(
            gateway.complete(component="agent:concept", max_tokens=16, messages=[]),
            gateway.complete(component="agent:quiz", max_tokens=16, messages=[]),
            gateway.complete(component="composer", max_tokens=16, messages=[]),
        )

    assert usage.llm_calls == 3
    assert usage.tokens_in == 300
    assert set(usage.by_component) == {"agent:concept", "agent:quiz", "composer"}


async def test_calls_outside_a_scope_do_not_raise(gateway):
    """Metrics still record; there is simply no rollup to attach to."""
    await gateway.complete(component="judge", max_tokens=16, messages=[])


async def test_scopes_do_not_leak_into_each_other(gateway):
    async with gateway.request_scope() as first:
        await gateway.complete(component="intent", max_tokens=16, messages=[])

    async with gateway.request_scope() as second:
        await gateway.complete(component="intent", max_tokens=16, messages=[])

    assert first.llm_calls == 1
    assert second.llm_calls == 1


async def test_rollup_as_dict_shape(gateway):
    async with gateway.request_scope() as usage:
        await gateway.complete(component="intent", max_tokens=16, messages=[])

    assert usage.as_dict() == {
        "llm_calls": 1,
        "tokens_in": 100,
        "tokens_out": 50,
        "tokens_by_component": {"intent": 150},
    }


# -- FR4/FR6: metrics and errors ------------------------------------------


async def test_success_increments_token_and_call_counters():
    model = "claude-sonnet-5"
    settings = get_settings().model_copy(update={"model": model})
    gateway = LLMGateway(
        settings,
        client=FakeAnthropic(
            response=FakeResponse(usage=FakeUsage(input_tokens=7, output_tokens=3))
        ),
    )

    before_out = _counter(
        LLM_TOKENS, component="judge", model=model, direction="output"
    )
    before_ok = _counter(LLM_CALLS, component="judge", model=model, outcome="success")

    await gateway.complete(component="judge", max_tokens=16, messages=[])

    after_out = _counter(LLM_TOKENS, component="judge", model=model, direction="output")
    after_ok = _counter(LLM_CALLS, component="judge", model=model, outcome="success")

    assert after_out - before_out == 3
    assert after_ok - before_ok == 1


async def test_error_is_counted_and_reraised(caplog):
    model = get_settings().model
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    rate_limited = anthropic.RateLimitError(
        "slow down",
        response=httpx.Response(429, request=request),
        body=None,
    )
    gateway = LLMGateway(get_settings(), client=FakeAnthropic(raises=rate_limited))

    before = _counter(LLM_CALLS, component="intent", model=model, outcome="error")

    with pytest.raises(anthropic.RateLimitError):
        await gateway.complete(component="intent", max_tokens=16, messages=[])

    after = _counter(LLM_CALLS, component="intent", model=model, outcome="error")
    assert after - before == 1
    assert "intent" in caplog.text


async def test_error_inside_a_scope_does_not_count_as_a_call():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    failure = anthropic.RateLimitError(
        "slow down", response=httpx.Response(429, request=request), body=None
    )
    gateway = LLMGateway(get_settings(), client=FakeAnthropic(raises=failure))

    async with gateway.request_scope() as usage:
        with pytest.raises(anthropic.RateLimitError):
            await gateway.complete(component="intent", max_tokens=16, messages=[])

    assert usage.llm_calls == 0
    assert usage.tokens_in == 0


# -- housekeeping ----------------------------------------------------------


def test_known_components_match_the_plan():
    """Plan section 3.5 lists the labels; drift here means unattributed cost."""
    assert KNOWN_COMPONENTS == {
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


async def test_aclose_closes_the_client(gateway):
    await gateway.aclose()
    assert gateway._client.closed is True


# -- the one test that costs money ----------------------------------------


@pytest.mark.live
async def test_live_call_against_the_real_api():
    """Opt in with `pytest -m live`. Makes exactly one real call.

    Deselected by default (see pyproject addopts) so the normal suite stays
    free and offline. Requires a real ANTHROPIC_API_KEY.
    """
    gateway = LLMGateway(get_settings())
    try:
        async with gateway.request_scope() as usage:
            response = await gateway.complete(
                component="intent",
                max_tokens=16,
                messages=[{"role": "user", "content": "Reply with the word: ok"}],
            )
        assert response.content
        assert usage.tokens_in > 0
        assert usage.tokens_out > 0
        print(f"\nlive call usage: {usage.as_dict()}")
    finally:
        await gateway.aclose()
