"""POST /chat. ISSUE-008.

The tests below run the *real* stack -- recogniser, fusion, routing, dispatch,
composition -- against a fake Anthropic client. Only the network is faked, so a
break in routing or attribution fails here rather than passing on a mock that
was taught the expected answer.

The fake dispatches on ``output_config``: the intent call is the only one that
constrains its output to a schema, so that flag is enough to tell the classifier
apart from the agents without the fake needing to know what a component is.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from owl_mind.agents.orchestrator import PrimaryAgentFailed
from owl_mind.core.intent_recognizer import IntentCategory, IntentRecognizer
from owl_mind.core.llm_gateway import LLMTimeout
from tests.fakes import FakeResponse, FakeTextBlock, FakeUsage


def _intent_payload(*pairs: tuple[IntentCategory, float]) -> str:
    return json.dumps(
        {"intents": [{"intent": i.value, "confidence": c} for i, c in pairs]}
    )


class ScriptedAnthropic:
    """Returns a scripted intent classification, and prose for everything else."""

    def __init__(self, intent_json: str, *, stop_reason: str = "end_turn") -> None:
        self._intent_json = intent_json
        self._stop_reason = stop_reason
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if "output_config" in kwargs:
            return FakeResponse(
                content=[FakeTextBlock(text=self._intent_json)],
                stop_reason=self._stop_reason,
                usage=FakeUsage(input_tokens=120, output_tokens=30),
            )
        return FakeResponse(
            content=[FakeTextBlock(text="Here is an answer.")],
            usage=FakeUsage(input_tokens=200, output_tokens=80),
        )

    async def close(self) -> None:
        return None


def _client(fake: Any) -> TestClient:
    """App with the lifespan run, then the gateway's client swapped for a fake.

    The index is left as the lifespan built it -- unreachable in the suite, so
    the embedding signal is absent and fusion renormalises over the two signals
    that voted. That is the production shape when Chroma is down.
    """
    from owl_mind.api.main import app

    client = TestClient(app)
    client.__enter__()
    app.state.gateway._client = fake
    app.state.intent_recognizer = IntentRecognizer(app.state.gateway, index=None)
    return client


@pytest.fixture()
def single_agent_client() -> Any:
    fake = ScriptedAnthropic(_intent_payload((IntentCategory.CONCEPT_EXPLAIN, 0.92)))
    client = _client(fake)
    yield client, fake
    client.__exit__(None, None, None)


@pytest.fixture()
def composite_client() -> Any:
    fake = ScriptedAnthropic(
        _intent_payload(
            (IntentCategory.CONCEPT_EXPLAIN, 0.88),
            (IntentCategory.QUIZ_REQUEST, 0.74),
        )
    )
    client = _client(fake)
    yield client, fake
    client.__exit__(None, None, None)


# -- the happy paths --------------------------------------------------------


def test_single_intent_answers_with_one_agent(single_agent_client: Any) -> None:
    client, _ = single_agent_client
    response = client.post("/chat", json={"message": "explain BFS to me"})

    assert response.status_code == 200
    body = response.json()
    assert body["primary_agent"] == "concept"
    assert body["supporting_agents"] == []
    # Intent plus the one agent. The composer is not called for a single
    # answer -- paying a model call to reformat one reply is waste.
    assert body["usage"]["llm_calls"] == 2
    assert "composer" not in body["usage"]["tokens_by_component"]


def test_composite_request_fans_out_and_composes(composite_client: Any) -> None:
    client, _ = composite_client
    response = client.post("/chat", json={"message": "explain BFS then quiz me"})

    assert response.status_code == 200
    body = response.json()
    # Asserted as a set: the claim this issue has to defend is that one message
    # reaches both specialists. *Which* one leads is decided by fusion and is
    # genuinely close on this message -- see the pattern-override test below,
    # which pins that ordering separately so a change to it fails there, with
    # an explanation, rather than here.
    engaged = {body["primary_agent"], *body["supporting_agents"]}
    assert engaged == {"concept", "quiz"}

    # The claim under test is attribution, not just fan-out: every participant
    # must appear by name in the rollup, which is what makes per-turn cost
    # answerable without a metrics scrape.
    components = body["usage"]["tokens_by_component"]
    assert set(components) == {"intent", "agent:concept", "agent:quiz", "composer"}
    assert body["usage"]["llm_calls"] == 4
    assert body["usage"]["tokens_in"] > 0


def test_a_pattern_rule_can_outrank_the_model_on_the_primary(
    composite_client: Any,
) -> None:
    """Pins a live consequence of the 0.5/0.35/0.15 weights.

    On "explain BFS then quiz me" the model ranks concept_explain 0.88 over
    quiz_request 0.74. One pattern rule matches ``quiz me`` at 0.95 and nothing
    matches the explanation half, so with the embedding signal absent fusion
    renormalises over llm+pattern (0.65) and quiz finishes at 0.788 against
    concept's 0.677. A regex at weight 0.15 reversed a 0.14 model gap.

    Both agents still run -- the fan-out is unaffected -- but the *lead*
    answer is chosen by a keyword. Recorded rather than corrected here: the
    right fix needs the labelled corpus to say whether pattern deserves 0.15,
    and picking a number now would be guessing with extra steps. Filed against
    the evaluation issue.
    """
    client, _ = composite_client
    body = client.post("/chat", json={"message": "explain BFS then quiz me"}).json()
    assert body["primary_agent"] == "quiz"
    assert body["supporting_agents"] == ["concept"]


def test_routing_reason_explains_the_route(composite_client: Any) -> None:
    client, _ = composite_client
    body = client.post("/chat", json={"message": "explain BFS then quiz me"}).json()
    # A route nobody can explain after the fact cannot be debugged.
    assert "concept" in body["routing_reason"]
    assert body["request_id"]


# -- the three no-agent outcomes, all 200 -----------------------------------


def test_human_tutor_escalates_without_calling_an_agent() -> None:
    fake = ScriptedAnthropic(_intent_payload((IntentCategory.HUMAN_TUTOR, 0.95)))
    client = _client(fake)
    try:
        body = client.post("/chat", json={"message": "I want to talk to a TA"}).json()
        assert body["escalated"] is True
        # The handoff makes no model call, which is what keeps the escape
        # hatch working during a provider outage.
        assert body["usage"]["llm_calls"] == 1
    finally:
        client.__exit__(None, None, None)


def test_off_topic_declines_at_200_with_no_agent() -> None:
    fake = ScriptedAnthropic(_intent_payload((IntentCategory.OTHER, 0.9)))
    client = _client(fake)
    try:
        response = client.post("/chat", json={"message": "what is the weather"})
        # Not a 4xx: an off-topic question is not a malformed request, and
        # conflating the two would make a student's question look like a bug.
        assert response.status_code == 200
        body = response.json()
        assert body["primary_agent"] is None
        assert body["usage"]["llm_calls"] == 1
    finally:
        client.__exit__(None, None, None)


def test_low_confidence_asks_for_clarification_at_200() -> None:
    # Nothing clears PRIMARY_THRESHOLD, so the panel disagreed about what was
    # asked -- a different outcome from "off topic", and a different answer.
    fake = ScriptedAnthropic(
        _intent_payload(
            (IntentCategory.CONCEPT_EXPLAIN, 0.30),
            (IntentCategory.QUIZ_REQUEST, 0.28),
        )
    )
    client = _client(fake)
    try:
        response = client.post("/chat", json={"message": "the thing from before"})
        assert response.status_code == 200
        body = response.json()
        assert body["primary_agent"] is None
        assert body["usage"]["llm_calls"] == 1
    finally:
        client.__exit__(None, None, None)


# -- failure mapping --------------------------------------------------------


@pytest.mark.parametrize(
    ("raised", "expected_status"),
    [
        (PrimaryAgentFailed("concept agent did not answer"), 503),
        (LLMTimeout("intent exceeded the 60s deadline"), 504),
        (anthropic.APIConnectionError(request=httpx.Request("POST", "http://x")), 502),
    ],
)
def test_failures_map_to_status_codes_without_leaking(
    single_agent_client: Any, raised: Exception, expected_status: int
) -> None:
    client, _ = single_agent_client

    class Exploding:
        async def run(self, request: Any) -> Any:
            raise raised

    client.app.state.orchestrator = Exploding()
    response = client.post("/chat", json={"message": "explain BFS"})

    assert response.status_code == expected_status
    # The body must carry no internals. These are the three things that have
    # historically leaked out of an error handler: the exception text, the
    # prompt, and the key.
    body = response.text.lower()
    assert "traceback" not in body
    assert "anthropic" not in body
    assert "test-key-not-used" not in body
    for fragment in ("concept agent did not answer", "60s deadline"):
        assert fragment not in body


def test_message_length_is_bounded() -> None:
    fake = ScriptedAnthropic(_intent_payload((IntentCategory.CONCEPT_EXPLAIN, 0.9)))
    client = _client(fake)
    try:
        # An unbounded field on an unauthenticated endpoint is a token bill
        # with a URL: the request must be rejected before it reaches a model.
        response = client.post("/chat", json={"message": "x" * 4001})
        assert response.status_code == 422
        assert fake.calls == []
    finally:
        client.__exit__(None, None, None)


def test_empty_message_is_rejected() -> None:
    fake = ScriptedAnthropic(_intent_payload((IntentCategory.CONCEPT_EXPLAIN, 0.9)))
    client = _client(fake)
    try:
        assert client.post("/chat", json={"message": ""}).status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_session_id_is_echoed_but_does_nothing_yet(single_agent_client: Any) -> None:
    client, fake = single_agent_client
    # Memory is a later issue. This test exists so the field's inertness is a
    # stated property rather than something a reader has to infer.
    first = client.post("/chat", json={"message": "explain BFS", "session_id": "s1"})
    second = client.post("/chat", json={"message": "explain BFS", "session_id": "s1"})
    assert first.status_code == second.status_code == 200
    for call in fake.calls:
        assert "s1" not in json.dumps(call.get("messages", []))


# -- startup degradation ----------------------------------------------------


def test_boot_survives_an_unreachable_index(single_agent_client: Any) -> None:
    """FR3's failure path.

    The suite's CHROMA_PORT is unroutable, so every run of this file already
    boots with seeding failed -- this asserts that is the intended outcome
    rather than luck. A process that refused to start because a *degraded*
    dependency was down could not serve the /health endpoint whose entire job
    is to report that.
    """
    client, _ = single_agent_client
    assert client.app.state.intent_recognizer is not None
    health = client.get("/health").json()
    assert health["status"] == "degraded"
    assert health["dependencies"]["chroma"]["reachable"] is False
    # Still answers, on the two signals that remain.
    assert client.post("/chat", json={"message": "explain BFS"}).status_code == 200


# -- the one live test ------------------------------------------------------


@pytest.mark.live
def test_chat_against_the_real_model() -> None:
    """The first test in this repository that can fail for a real reason.

    Four issues of infrastructure have only ever been exercised by fakes that
    return what the code expects. Content-block shapes, stop_reason values and
    latency are all untested against reality until this runs. Asserts shape and
    non-emptiness only -- never wording, which would make it flaky and get it
    deleted rather than fixed.
    """
    from owl_mind.api.main import app

    with TestClient(app) as client:
        response = client.post(
            "/chat", json={"message": "In one sentence, what is a binary search tree?"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["response"].strip()
    assert body["primary_agent"] is not None
    assert body["usage"]["llm_calls"] >= 2
    assert body["usage"]["tokens_in"] > 0
    assert body["usage"]["tokens_out"] > 0
