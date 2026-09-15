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
    LLM_UNATTRIBUTED,
    LLMGateway,
    LLMTimeout,
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

    # llm_calls stays paired with the token totals: a call with no usage
    # object must not make tokens-per-call wrong.
    assert usage.llm_calls == 0
    assert usage.tokens_in == 0
    # But the call still happened and may still have been billed, so it is
    # counted where a reader will see it (ISSUE-006 F10). Reporting nothing
    # here is what made failed-call spend structurally invisible.
    assert usage.unattributed_calls == 1
    assert usage.as_dict()["unattributed_by_component"] == {"intent": 1}


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


# -- ISSUE-008 FR4: truncation is its own outcome --------------------------


async def test_truncated_response_is_counted_apart_from_success(gateway):
    """ISSUE-006 F2.

    A response cut off at max_tokens is neither a success nor an error: the
    HTTP call worked and the content is incomplete. Counted as `success` it is
    invisible -- the caller parses garbage, logs a warning, degrades its
    signal, and the request still answers. This asserts the third label.
    """
    model = get_settings().model
    gateway._client.response = FakeResponse(stop_reason="max_tokens")

    before_trunc = _counter(
        LLM_CALLS, component="intent", model=model, outcome="truncated"
    )
    before_ok = _counter(LLM_CALLS, component="intent", model=model, outcome="success")

    await gateway.complete(component="intent", max_tokens=400, messages=[])

    assert (
        _counter(LLM_CALLS, component="intent", model=model, outcome="truncated")
        == before_trunc + 1
    )
    # The point of the label: it must not also land in `success`.
    assert (
        _counter(LLM_CALLS, component="intent", model=model, outcome="success")
        == before_ok
    )


async def test_tokens_are_still_recorded_for_a_truncated_call(gateway):
    """Truncated output was still generated, and still costs money."""
    gateway._client.response = FakeResponse(
        stop_reason="max_tokens", usage=FakeUsage(input_tokens=90, output_tokens=400)
    )
    async with gateway.request_scope() as rollup:
        await gateway.complete(component="intent", max_tokens=400, messages=[])
    assert rollup.tokens_out == 400


# -- ISSUE-008 FR5: the deadline -------------------------------------------


async def test_a_slow_call_raises_llm_timeout(gateway):
    """ISSUE-006 F8. Without this the ceiling is the SDK's ten minutes."""
    model = get_settings().model
    gateway._timeout = 0.05
    gateway._client.delay = 5.0

    before = _counter(LLM_CALLS, component="intent", model=model, outcome="error")
    with pytest.raises(LLMTimeout):
        await gateway.complete(component="intent", max_tokens=10, messages=[])

    # A timeout is a failed call and must show up as one; silence here would
    # make a hung provider look like reduced traffic.
    assert (
        _counter(LLM_CALLS, component="intent", model=model, outcome="error")
        == before + 1
    )


async def test_the_deadline_covers_waiting_for_a_slot(gateway):
    """The queue half is the half that grows under load.

    The semaphore is per process, so a burst puts callers behind it. A deadline
    that started only once a slot was held would leave that wait unbounded and
    report a healthy latency while users waited.
    """
    gateway._semaphore = asyncio.Semaphore(1)
    gateway._timeout = 0.15
    gateway._client.delay = 0.6

    async def call():
        return await gateway.complete(component="intent", max_tokens=10, messages=[])

    results = await asyncio.gather(call(), call(), return_exceptions=True)
    # Both fail: the first on its own call, the second while queued behind it.
    assert all(isinstance(r, LLMTimeout) for r in results)


# -- ISSUE-008: one rollup per request -------------------------------------


async def test_nested_scopes_share_one_rollup(gateway):
    """/chat opens a scope; the orchestrator opens another inside it.

    If the inner scope rebound the contextvar, the outer rollup would hold the
    intent call alone and every agent that answered the turn would be missing
    from the reported cost.
    """
    async with gateway.request_scope() as outer:
        await gateway.complete(component="intent", max_tokens=10, messages=[])
        async with gateway.request_scope() as inner:
            assert inner is outer
            await gateway.complete(component="agent:concept", max_tokens=10, messages=[])

    assert outer.llm_calls == 2
    assert set(outer.by_component) == {"intent", "agent:concept"}


# -- ISSUE-006 F10: the blind spot is bounded, not hidden ------------------


async def test_a_successful_request_reports_no_unattributed_field(gateway):
    """A field reading 0 on every healthy request is noise.

    It appears only when the token figures are actually incomplete, so its
    presence in a response body is itself the signal.
    """
    async with gateway.request_scope() as usage:
        await gateway.complete(component="intent", max_tokens=16, messages=[])
    assert "unattributed_calls" not in usage.as_dict()


async def test_a_timeout_is_counted_apart_from_a_rejection(gateway):
    """A timeout reached the provider and is probably billed; a 400 is not.

    Same blind spot, very different cost, so they do not share a label.
    """
    model = get_settings().model
    gateway._timeout = 0.05
    gateway._client.delay = 5.0

    before = _counter(
        LLM_UNATTRIBUTED, component="intent", model=model, reason="timeout"
    )
    with pytest.raises(LLMTimeout):
        await gateway.complete(component="intent", max_tokens=16, messages=[])

    assert (
        _counter(LLM_UNATTRIBUTED, component="intent", model=model, reason="timeout")
        == before + 1
    )


async def test_partial_failure_leaves_the_totals_marked_incomplete(gateway):
    """The case F10 is really about: a fan-out where one branch failed.

    ISSUE-005 FR6 drops a failed supporting agent and answers anyway, so this
    is a request that succeeded while spending money nobody can see. The
    response must not present its token totals as complete.
    """
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    failure = anthropic.RateLimitError(
        "slow down", response=httpx.Response(429, request=request), body=None
    )

    async with gateway.request_scope() as usage:
        await gateway.complete(component="agent:concept", max_tokens=16, messages=[])
        gateway._client.raises = failure
        with pytest.raises(anthropic.RateLimitError):
            await gateway.complete(component="agent:quiz", max_tokens=16, messages=[])

    payload = usage.as_dict()
    assert payload["llm_calls"] == 1
    assert payload["tokens_in"] > 0
    assert payload["unattributed_calls"] == 1
    assert payload["unattributed_by_component"] == {"agent:quiz": 1}
