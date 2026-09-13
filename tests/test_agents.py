"""Agents and orchestration -- ISSUE-005.

No network calls: every agent runs through a fake gateway. Routing is tested by
constructing ``Intent`` objects directly, because FR2 requires it to be a pure
function of the intent -- if a routing test needs a model, routing has acquired
an I/O dependency it is not allowed to have.

One trap worth naming: a hand-built ``Intent`` must set ``corroborated``.
Populate ``scores`` and forget it and every supporting list comes back empty,
which looks exactly like a routing bug. ``make_intent`` below sets it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from owl_mind.agents.base import (
    MAX_TOOL_ROUNDS,
    AgentProfile,
    AgentRequest,
    AgentType,
    BaseAgent,
    ToolValidationError,
    validate_tool_input,
)
from owl_mind.agents.composer import ResponseComposer
from owl_mind.agents.orchestrator import (
    AgentOrchestrator,
    PrimaryAgentFailed,
)
from owl_mind.agents.roster import (
    AGENT_CLASSES,
    PROFILES,
    TutorHandoffAgent,
    _check_roster_is_complete,
    _check_tool_scopes_resolve,
)
from owl_mind.agents.tools import REGISTRY, AgentToolSpec, register
from owl_mind.core.contracts import ContractViolation, registered_contracts
from owl_mind.core.intent_recognizer import (
    _INTENT_GROUPS,
    Intent,
    IntentCategory,
    UrgencyLevel,
)
from owl_mind.core.llm_gateway import KNOWN_COMPONENTS, LLMGateway
from tests.fakes import (
    FakeAnthropic,
    FakeResponse,
    FakeTextBlock,
    FakeToolUseBlock,
    ScriptedAnthropic,
)

# -- helpers ----------------------------------------------------------------


def make_intent(
    category: IntentCategory,
    *,
    supporting: tuple[IntentCategory, ...] = (),
    confidence: float = 0.9,
    urgency: UrgencyLevel = UrgencyLevel.LOW,
    entities: dict[str, Any] | None = None,
    fallback_from: IntentCategory | None = None,
    corroborated: frozenset[IntentCategory] | None = None,
) -> Intent:
    scores = {category: confidence}
    for extra in supporting:
        scores[extra] = 0.40
    return Intent(
        category=category,
        group=_INTENT_GROUPS[category],
        confidence=confidence,
        scores=scores,
        source_scores={"llm": confidence, "embedding": 0.0, "pattern": 0.0},
        urgency=urgency,
        entities=entities or {},
        fallback_from=fallback_from,
        corroborated=frozenset(scores) if corroborated is None else corroborated,
    )


def reply(text: str = "an answer") -> FakeResponse:
    return FakeResponse(content=[FakeTextBlock(text)])


def gateway(client: Any) -> LLMGateway:
    from owl_mind.core.config import get_settings

    return LLMGateway(get_settings(), client=client)


def orchestrator(client: Any) -> AgentOrchestrator:
    return AgentOrchestrator(gateway(client))


def request(message: str = "explain BFS", intent: Intent | None = None) -> AgentRequest:
    return AgentRequest(message=message, intent=intent, request_id="req-1")


@pytest.fixture()
def clean_registry():
    """The tool registry is module-global; a leaked fake breaks the next test."""
    saved = dict(REGISTRY)
    REGISTRY.clear()
    yield REGISTRY
    REGISTRY.clear()
    REGISTRY.update(saved)


def echo_tool(name: str = "echo") -> AgentToolSpec:
    return register(
        AgentToolSpec(
            name=name,
            description=f"Echo a value back. ({name})",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            handler=lambda req, args: {"echoed": args["value"]},
        )
    )


# -- FR1: the roster --------------------------------------------------------


def test_every_agent_type_has_a_profile_and_an_implementation():
    assert set(PROFILES) == set(AgentType)
    assert set(AGENT_CLASSES) == set(AgentType)
    for agent_type, cls in AGENT_CLASSES.items():
        assert cls.agent_type is agent_type


@pytest.mark.parametrize(
    ("agent_type", "max_tokens"),
    [
        (AgentType.CONCEPT, 1200),
        (AgentType.PRACTICE, 1000),
        (AgentType.PLANNER, 800),
        (AgentType.QUIZ, 1200),
    ],
)
def test_max_tokens_match_the_plan(agent_type: AgentType, max_tokens: int):
    """Per-role ceilings. The monolith alternative provisions for the worst case."""
    assert PROFILES[agent_type].max_tokens == max_tokens


def test_effort_is_not_differentiated_yet():
    """ISSUE-002 FR7: guessed per-role values look considered without being so."""
    assert len({profile.effort for profile in PROFILES.values()}) == 1


def test_every_role_declares_a_risk_boundary():
    """The profile field that carries the multi-agent argument must be populated."""
    for agent_type, profile in PROFILES.items():
        assert profile.risk_boundary.strip(), agent_type


def test_practice_agent_is_forbidden_from_emitting_a_solution():
    boundary = PROFILES[AgentType.PRACTICE].risk_boundary.lower()
    assert "never emit a complete solution" in boundary


def test_agent_components_are_all_known_to_the_gateway():
    """An unknown label is rejected before the call, so this would 500 in prod."""
    for agent_type, cls in AGENT_CLASSES.items():
        if agent_type is AgentType.TUTOR_HANDOFF:
            continue  # makes no model call, so it has no component
        agent = cls(gateway(FakeAnthropic()))
        assert agent.component in KNOWN_COMPONENTS


async def test_tutor_handoff_makes_no_model_call():
    """The property that keeps the escape hatch working during an outage.

    Not a configuration flag: handle() is overridden, so there is no branch
    that a later edit could flip back toward the gateway.
    """
    client = FakeAnthropic(response=reply())
    agent = TutorHandoffAgent(gateway(client))

    intent = make_intent(IntentCategory.HUMAN_TUTOR, entities={"topic": "bfs"})
    response = await agent.handle(request("I want to talk to a TA", intent))

    assert client.calls == []
    assert response.escalate is True
    assert "bfs" in response.content
    assert "I want to talk to a TA" in response.content


# -- FR4: the tool loop and the whitelist -----------------------------------


class ScopedAgent(BaseAgent):
    """A concept agent whose tool scope the test controls."""

    agent_type = AgentType.CONCEPT

    def __init__(self, gw: LLMGateway, scope: tuple[str, ...]) -> None:
        super().__init__(gw)
        self.profile = AgentProfile(role="test role", tool_scope=scope, max_tokens=100)


async def test_a_tool_outside_the_scope_never_appears_in_the_request(clean_registry):
    """The whitelist is applied when the payload is built, not on return.

    A tool the model is never offered cannot be called, argued for, or
    prompt-injected into. That is a different guarantee from rejecting it after
    the fact.
    """
    echo_tool("allowed")
    echo_tool("forbidden")
    client = FakeAnthropic(response=reply())

    agent = ScopedAgent(gateway(client), scope=("allowed",))
    await agent.handle(request())

    sent = client.calls[0]["tools"]
    assert [tool["name"] for tool in sent] == ["allowed"]

    other = ScopedAgent(gateway(client), scope=())
    await other.handle(request())
    assert "tools" not in client.calls[1]


async def test_traces_are_present_after_a_successful_tool_call(clean_registry):
    """The reference implementation's _last_tool_traces bug (plan 11.4 #3).

    Traces were assigned only when the loop ran out of rounds, so every request
    that *worked* reported none and the monitor's tool metrics measured
    nothing.
    """
    echo_tool()
    client = ScriptedAnthropic(
        [
            FakeResponse(
                content=[FakeToolUseBlock(name="echo", input={"value": "hi"})],
                stop_reason="tool_use",
            ),
            reply("done"),
        ]
    )
    agent = ScopedAgent(gateway(client), scope=("echo",))
    response = await agent.handle(request())

    assert response.success
    assert response.content == "done"
    assert response.tools_used == ("echo",)
    assert len(response.tool_traces) == 1
    assert response.tool_traces[0]["tool"] == "echo"
    assert response.tool_traces[0]["ok"] is True


async def test_a_hallucinated_argument_is_a_tool_result_not_a_stack_trace(clean_registry):
    """The model must be able to correct itself on the next round."""
    echo_tool()
    client = ScriptedAnthropic(
        [
            FakeResponse(
                content=[FakeToolUseBlock(name="echo", input={"wrong": "hi"})],
                stop_reason="tool_use",
            ),
            reply("recovered"),
        ]
    )
    agent = ScopedAgent(gateway(client), scope=("echo",))
    response = await agent.handle(request())

    assert response.success
    assert response.tool_traces[0]["ok"] is False
    assert "missing required" in response.tool_traces[0]["error"]

    # The error went back to the model as a tool_result, not as an exception.
    followup = client.calls[1]["messages"][-1]["content"][0]
    assert followup["type"] == "tool_result"
    assert followup["is_error"] is True


async def test_a_raising_tool_does_not_fail_the_turn(clean_registry):
    def boom(req, args):
        raise RuntimeError("tool exploded")

    register(
        AgentToolSpec(
            name="boom",
            description="always raises",
            input_schema={"type": "object", "properties": {}, "required": []},
            handler=boom,
        )
    )
    client = ScriptedAnthropic(
        [
            FakeResponse(
                content=[FakeToolUseBlock(name="boom", input={})], stop_reason="tool_use"
            ),
            reply("carried on"),
        ]
    )
    agent = ScopedAgent(gateway(client), scope=("boom",))
    response = await agent.handle(request())

    assert response.success
    assert response.content == "carried on"
    assert response.tool_traces[0]["ok"] is False
    assert "RuntimeError" in response.tool_traces[0]["error"]


async def test_the_tool_loop_is_bounded(clean_registry):
    """A model that never stops asking is a bill, not a feature."""
    echo_tool()
    always_asking = FakeResponse(
        content=[FakeToolUseBlock(name="echo", input={"value": "again"})],
        stop_reason="tool_use",
    )
    client = FakeAnthropic(response=always_asking)
    agent = ScopedAgent(gateway(client), scope=("echo",))
    response = await agent.handle(request())

    assert len(client.calls) == MAX_TOOL_ROUNDS
    # Traces survive exhaustion too -- that half the reference got right.
    assert len(response.tool_traces) == MAX_TOOL_ROUNDS


def test_validate_tool_input_rejects_the_shapes_that_matter():
    spec = AgentToolSpec(
        name="t",
        description="",
        input_schema={
            "type": "object",
            "properties": {"n": {"type": "integer"}, "s": {"type": "string"}},
            "required": ["n"],
            "additionalProperties": False,
        },
        handler=lambda req, args: args,
    )
    assert validate_tool_input(spec, {"n": 1}) == {"n": 1}

    for bad in ({}, {"n": "1"}, {"n": 1, "extra": 2}, {"n": True}, "not an object"):
        with pytest.raises(ToolValidationError):
            validate_tool_input(spec, bad)


# -- FR2: routing -----------------------------------------------------------


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        (IntentCategory.CONCEPT_EXPLAIN, AgentType.CONCEPT),
        (IntentCategory.PROBLEM_HELP, AgentType.PRACTICE),
        (IntentCategory.STUDY_PLAN, AgentType.PLANNER),
        (IntentCategory.QUIZ_REQUEST, AgentType.QUIZ),
        (IntentCategory.MOTIVATION, AgentType.CONCEPT),
        (IntentCategory.HUMAN_TUTOR, AgentType.TUTOR_HANDOFF),
    ],
)
def test_group_maps_to_a_primary_agent(category, expected):
    decision = orchestrator(FakeAnthropic())._route_decision(make_intent(category))
    assert decision.primary_agent is expected


def test_a_single_intent_routes_to_one_agent():
    decision = orchestrator(FakeAnthropic())._route_decision(
        make_intent(IntentCategory.CONCEPT_EXPLAIN)
    )
    assert decision.primary_agent is AgentType.CONCEPT
    assert decision.supporting_agents == []
    assert decision.multi_agent is False


def test_the_reason_carries_the_score_vector():
    """A route that cannot be explained after the fact cannot be debugged."""
    decision = orchestrator(FakeAnthropic())._route_decision(
        make_intent(IntentCategory.CONCEPT_EXPLAIN, supporting=(IntentCategory.QUIZ_REQUEST,))
    )
    assert "primary=concept" in decision.reason
    assert "supporting=quiz" in decision.reason
    assert "concept_explain=0.900" in decision.reason
    assert "quiz_request=0.400" in decision.reason


COMPOSITE_CASES = [
    (
        "explain BFS then quiz me",
        IntentCategory.CONCEPT_EXPLAIN,
        IntentCategory.QUIZ_REQUEST,
        [AgentType.CONCEPT, AgentType.QUIZ],
    ),
    (
        "I'm stuck on two-sum and what should I review this week",
        IntentCategory.PROBLEM_HELP,
        IntentCategory.STUDY_PLAN,
        [AgentType.PRACTICE, AgentType.PLANNER],
    ),
    (
        "why is my solution O(n^2), show me the concept I'm missing",
        IntentCategory.COMPLEXITY_ANALYSIS,
        IntentCategory.CONCEPT_EXPLAIN,
        [AgentType.PRACTICE, AgentType.CONCEPT],
    ),
    (
        "here is my answer, and what should I study next",
        IntentCategory.ANSWER_SUBMISSION,
        IntentCategory.STUDY_PLAN,
        [AgentType.QUIZ, AgentType.PLANNER],
    ),
    (
        "check my proof and then give me a practice exam",
        IntentCategory.HOMEWORK_CHECK,
        IntentCategory.MOCK_EXAM,
        [AgentType.PRACTICE, AgentType.QUIZ],
    ),
]


@pytest.mark.parametrize(
    ("message", "primary", "secondary", "expected"),
    COMPOSITE_CASES,
    ids=[case[0] for case in COMPOSITE_CASES],
)
def test_composite_requests_reach_several_agents(message, primary, secondary, expected):
    """Plan 11.2: if every request routes to one agent, the claim evaporates.

    Asserted on *agents*, not intents -- two intents that both map to the same
    specialist are still a single-agent request, and asserting on intents would
    hide that.
    """
    decision = orchestrator(FakeAnthropic())._route_decision(
        make_intent(primary, supporting=(secondary,))
    )
    assert decision.agent_types == expected
    assert decision.multi_agent is True


def test_two_intents_in_one_group_collapse_to_a_single_agent():
    """The failure mode plan 11.2 warns about, pinned rather than hoped about.

    progress_query and study_plan are both PLAN. Dispatching Planner twice
    would pay the composer to merge an answer with itself.
    """
    decision = orchestrator(FakeAnthropic())._route_decision(
        make_intent(IntentCategory.STUDY_PLAN, supporting=(IntentCategory.PROGRESS_QUERY,))
    )
    assert decision.agent_types == [AgentType.PLANNER]


def test_supporting_agents_are_capped_at_max_agents():
    decision = orchestrator(FakeAnthropic())._route_decision(
        make_intent(
            IntentCategory.CONCEPT_EXPLAIN,
            supporting=(
                IntentCategory.QUIZ_REQUEST,
                IntentCategory.STUDY_PLAN,
                IntentCategory.PROBLEM_HELP,
            ),
        )
    )
    assert len(decision.agent_types) == 3


def test_human_tutor_short_circuits_with_no_supporting_agents():
    decision = orchestrator(FakeAnthropic())._route_decision(
        make_intent(IntentCategory.HUMAN_TUTOR, supporting=(IntentCategory.CONCEPT_EXPLAIN,))
    )
    assert decision.primary_agent is AgentType.TUTOR_HANDOFF
    assert decision.supporting_agents == []


def test_a_deadline_does_not_escalate_to_a_human():
    """The regression this whole amendment exists for.

    compute_urgency returns CRITICAL for any due date within a day, and
    _extract_due_date sets one on the bare word "today". Keying the
    short-circuit on urgency answered "explain BFS to me today" with a handoff
    stub -- at the moment the student needed the most help, not the least.
    """
    intent = make_intent(
        IntentCategory.CONCEPT_EXPLAIN,
        urgency=UrgencyLevel.CRITICAL,
        entities={"due_date": "2026-09-14", "topic": "bfs"},
    )
    decision = orchestrator(FakeAnthropic())._route_decision(intent)

    assert decision.primary_agent is AgentType.CONCEPT
    assert decision.primary_agent is not AgentType.TUTOR_HANDOFF


def test_routing_is_pure(monkeypatch):
    """FR2: no model call, no I/O. Constructing an Intent must be enough."""
    client = FakeAnthropic()
    decision = orchestrator(client)._route_decision(
        make_intent(IntentCategory.QUIZ_REQUEST, supporting=(IntentCategory.STUDY_PLAN,))
    )
    assert client.calls == []
    assert decision.primary_agent is AgentType.QUIZ


# -- FR2a: the paths that never reach an agent ------------------------------


async def test_an_ambiguous_message_asks_which_and_costs_nothing():
    intent = make_intent(
        IntentCategory.OTHER,
        confidence=0.0,
        fallback_from=IntentCategory.CONCEPT_EXPLAIN,
    )
    # The distribution survives the fallback and is what the question is built from.
    intent = Intent(
        category=IntentCategory.OTHER,
        group=intent.group,
        confidence=0.0,
        scores={IntentCategory.CONCEPT_EXPLAIN: 0.30, IntentCategory.QUIZ_REQUEST: 0.28},
        fallback_from=IntentCategory.CONCEPT_EXPLAIN,
    )
    client = FakeAnthropic()
    result = await orchestrator(client).run(request("mmm trees", intent))

    assert client.calls == []
    assert result.primary_agent is None
    assert "an explanation" in result.response
    assert "a quiz" in result.response
    assert "ambiguous" in result.routing_reason


async def test_an_off_topic_message_declines_and_costs_nothing():
    intent = make_intent(IntentCategory.OTHER, confidence=0.95)
    client = FakeAnthropic()
    result = await orchestrator(client).run(request("what is the weather", intent))

    assert client.calls == []
    assert result.primary_agent is None
    assert "outside what I can help with" in result.response
    assert result.routing_reason == "no relevant study intent"


async def test_the_two_other_paths_differ():
    """Collapsing them would answer an ambiguous study question with a refusal."""
    orch = orchestrator(FakeAnthropic())
    ambiguous = await orch.run(
        request(
            "mmm",
            Intent(
                category=IntentCategory.OTHER,
                group=_INTENT_GROUPS[IntentCategory.OTHER],
                confidence=0.0,
                scores={IntentCategory.STUDY_PLAN: 0.3},
                fallback_from=IntentCategory.STUDY_PLAN,
            ),
        )
    )
    off_topic = await orch.run(request("weather", make_intent(IntentCategory.OTHER)))
    assert ambiguous.response != off_topic.response


async def test_a_human_tutor_request_records_zero_gateway_calls():
    client = FakeAnthropic(response=reply())
    result = await orchestrator(client).run(
        request("can I speak to a real person", make_intent(IntentCategory.HUMAN_TUTOR))
    )
    assert client.calls == []
    assert result.primary_agent is AgentType.TUTOR_HANDOFF
    assert result.supporting_agents == []
    assert result.escalated is True


# -- FR3: dispatch ----------------------------------------------------------


async def test_a_single_agent_request_skips_the_composer():
    """Paying for a model call to reformat one answer is waste."""
    client = FakeAnthropic(response=reply("just the one"))
    result = await orchestrator(client).run(
        request("explain BFS", make_intent(IntentCategory.CONCEPT_EXPLAIN))
    )

    assert result.response == "just the one"
    assert len(client.calls) == 1
    assert [c for c in client.calls if "merge" in str(c)] == []


async def test_a_composite_request_dispatches_concurrently():
    """Instrumented for overlap: sequential execution would peak at one."""
    client = FakeAnthropic(response=reply("answer"), delay=0.02)
    await orchestrator(client).run(
        request(
            "explain BFS then quiz me",
            make_intent(
                IntentCategory.CONCEPT_EXPLAIN, supporting=(IntentCategory.QUIZ_REQUEST,)
            ),
        )
    )
    assert client.peak_in_flight >= 2


async def test_the_rollup_covers_every_agent_and_the_composer():
    """FR3: the whole dispatch runs inside one request scope."""
    client = FakeAnthropic(response=reply("answer"))
    result = await orchestrator(client).run(
        request(
            "explain BFS then quiz me",
            make_intent(
                IntentCategory.CONCEPT_EXPLAIN, supporting=(IntentCategory.QUIZ_REQUEST,)
            ),
        )
    )

    assert result.usage["llm_calls"] == 3  # two agents plus the composer
    assert result.usage["tokens_in"] > 0
    by_component = result.usage["tokens_by_component"]
    assert set(by_component) == {"agent:concept", "agent:quiz", "composer"}


async def test_one_instance_per_agent_type():
    orch = orchestrator(FakeAnthropic())
    assert sorted(orch.registered_agents()) == sorted(a.value for a in AgentType)


# -- FR5: composition -------------------------------------------------------


async def test_the_composer_is_given_every_contribution():
    client = FakeAnthropic(response=reply("merged"))
    composer = ResponseComposer(gateway(client))

    from owl_mind.agents.base import AgentResponse

    merged = await composer.compose(
        "explain BFS then quiz me",
        [
            AgentResponse(agent_type=AgentType.CONCEPT, content="BFS explores level by level"),
            AgentResponse(agent_type=AgentType.QUIZ, content="Q1: what queue does BFS use?"),
        ],
    )

    assert merged == "merged"
    prompt = client.calls[0]["messages"][0]["content"]
    assert "BFS explores level by level" in prompt
    assert "Q1: what queue does BFS use?" in prompt
    assert client.calls[0]["system"].startswith("You merge answers")


async def test_the_composer_is_not_called_for_one_answer():
    client = FakeAnthropic(response=reply("x"))
    composer = ResponseComposer(gateway(client))

    from owl_mind.agents.base import AgentResponse

    merged = await composer.compose(
        "one thing", [AgentResponse(agent_type=AgentType.CONCEPT, content="only answer")]
    )
    assert merged == "only answer"
    assert client.calls == []


async def test_a_composer_that_returns_nothing_still_keeps_both_answers():
    """Worse prose beats an empty reply, which is what losing the parts means."""
    client = FakeAnthropic(response=FakeResponse(content=[]))
    composer = ResponseComposer(gateway(client))

    from owl_mind.agents.base import AgentResponse

    merged = await composer.compose(
        "two things",
        [
            AgentResponse(agent_type=AgentType.CONCEPT, content="first"),
            AgentResponse(agent_type=AgentType.QUIZ, content="second"),
        ],
    )
    assert "first" in merged and "second" in merged


# -- FR6: degradation -------------------------------------------------------


class Flaky(BaseAgent):
    agent_type = AgentType.QUIZ
    profile = PROFILES[AgentType.QUIZ]

    async def handle(self, req):
        raise RuntimeError("quiz agent exploded")


async def test_a_failed_supporting_agent_is_dropped_and_recorded():
    client = FakeAnthropic(response=reply("the explanation"))
    orch = orchestrator(client)
    orch._pool[AgentType.QUIZ] = [Flaky(gateway(client))]

    result = await orch.run(
        request(
            "explain BFS then quiz me",
            make_intent(
                IntentCategory.CONCEPT_EXPLAIN, supporting=(IntentCategory.QUIZ_REQUEST,)
            ),
        )
    )

    assert result.response == "the explanation"
    assert result.dropped_agents == [AgentType.QUIZ]
    assert result.supporting_agents == []
    assert "dropped=[quiz]" in result.routing_reason


async def test_a_failed_primary_surfaces_rather_than_being_answered_by_another_role():
    """Owl Mind has no general agent, and substituting one changes the boundaries.

    Handing a problem-help request to ConceptAgent would answer it -- including
    stating the solution PracticeAgent is forbidden from giving.
    """
    client = FakeAnthropic(response=reply())
    orch = orchestrator(client)
    orch._pool[AgentType.QUIZ] = [Flaky(gateway(client))]

    with pytest.raises(PrimaryAgentFailed):
        await orch.run(request("quiz me", make_intent(IntentCategory.QUIZ_REQUEST)))


async def test_one_failing_branch_does_not_cancel_its_siblings():
    """gather without return_exceptions turns one flaky agent into a failed request."""
    started: list[str] = []

    class Slow(BaseAgent):
        agent_type = AgentType.PLANNER
        profile = PROFILES[AgentType.PLANNER]

        async def handle(self, req):
            started.append("planner")
            await asyncio.sleep(0.03)
            from owl_mind.agents.base import AgentResponse

            return AgentResponse(agent_type=self.agent_type, content="a plan")

    client = FakeAnthropic(response=reply("primary answer"))
    orch = orchestrator(client)
    orch._pool[AgentType.QUIZ] = [Flaky(gateway(client))]
    orch._pool[AgentType.PLANNER] = [Slow(gateway(client))]

    result = await orch.run(
        request(
            "explain, quiz me, and plan my week",
            make_intent(
                IntentCategory.CONCEPT_EXPLAIN,
                supporting=(IntentCategory.QUIZ_REQUEST, IntentCategory.STUDY_PLAN),
            ),
        )
    )

    assert started == ["planner"]  # the slow sibling ran to completion
    assert result.dropped_agents == [AgentType.QUIZ]
    assert AgentType.PLANNER in result.supporting_agents


async def test_failures_are_counted_in_the_agent_stats():
    """The monitor's whole purpose is to notice an agent that started losing."""
    client = FakeAnthropic(raises=RuntimeError("api down"))
    orch = orchestrator(client)
    agent = orch._best_agent(AgentType.CONCEPT)

    with pytest.raises(RuntimeError):
        await agent.handle(request())

    assert agent.stats.total == 1
    assert agent.stats.success == 0
    assert agent.stats.success_rate == 0.0


def test_a_fresh_agent_is_not_assumed_broken():
    agent = orchestrator(FakeAnthropic())._best_agent(AgentType.CONCEPT)
    assert agent.stats.success_rate == 1.0


# -- FR7: startup contracts -------------------------------------------------


def test_the_roster_contracts_are_registered():
    descriptions = registered_contracts()
    assert "every AgentType has a profile and an implementation" in descriptions
    assert "every tool_scope name exists in the tool registry" in descriptions


def test_a_missing_agent_type_aborts_the_boot(monkeypatch):
    monkeypatch.delitem(AGENT_CLASSES, AgentType.QUIZ)
    with pytest.raises(ContractViolation) as excinfo:
        _check_roster_is_complete()
    assert "quiz" in str(excinfo.value)


def test_a_tool_scope_naming_an_unregistered_tool_aborts_the_boot(clean_registry):
    """Trivially true while the registry is empty; load-bearing next issue."""
    _check_tool_scopes_resolve()  # empty registry: nothing to contradict

    echo_tool("something_else")
    with pytest.raises(ContractViolation) as excinfo:
        _check_tool_scopes_resolve()
    assert "search_materials" in str(excinfo.value)


# -- guard rails still hold -------------------------------------------------


def test_agent_requests_send_no_sampling_parameters():
    """Rejected with a 400 on the current models; a guard rail also greps for it."""
    client = FakeAnthropic(response=reply())
    asyncio.run(
        orchestrator(client).run(
            request("explain BFS", make_intent(IntentCategory.CONCEPT_EXPLAIN))
        )
    )
    sent = json.dumps(client.calls, default=str)
    for parameter in ("temperature", "top_p", "top_k"):
        assert parameter not in sent
