"""Agent tools -- ISSUE-007.

These are the tests the whole issue exists for. Three merged issues assert that
Owl Mind's guarantees come from deterministic tools rather than from prompts;
until this file existed, nothing checked it.

Every test here runs without a model. That is the point: a property that needs
a model to verify is not a property the tools provide.
"""

from __future__ import annotations

from datetime import date

import pytest

from owl_mind.agents.base import AgentType
from owl_mind.agents.roster import (
    PROFILES,
    TutorHandoffAgent,
    _check_no_orphan_tools,
    _check_tool_scopes_resolve,
)
from owl_mind.agents.tools import REGISTRY
from owl_mind.agents.tools.concept import chain_for
from owl_mind.agents.tools.planner import DEFAULT_EASE, MIN_EASE, sm2
from owl_mind.agents.tools.practice import MAX_LEVEL, all_hints, analyze, family_for
from owl_mind.agents.tools.quiz import allocate, fingerprint, grade
from owl_mind.agents.tools.shared import create_handoff_summary
from owl_mind.core.contracts import ContractViolation
from tests.fakes import FakeAnthropic
from tests.test_agents import gateway, make_intent, request

from owl_mind.core.intent_recognizer import IntentCategory  # isort: skip

TODAY = date(2026, 9, 14)


def call(name: str, **args):
    """Invoke a registered tool the way the loop does."""
    return REGISTRY[name].handler(request(), args)


# -- FR2: get_prerequisites -------------------------------------------------


def test_prerequisites_are_returned_foundations_first():
    result = chain_for("red-black tree")
    assert result["known"] is True
    assert result["immediate"] == ["binary search tree", "tree rotation"]
    # Teaching order: a student meets "tree" before "binary search tree".
    chain = result["chain"]
    assert chain.index("tree") < chain.index("binary search tree")
    assert chain.index("binary tree") < chain.index("binary search tree")


def test_an_unknown_topic_says_so_rather_than_inventing_a_chain():
    """ConceptAgent's boundary is "never state a course fact you cannot support".

    A plausible invented prerequisite chain is exactly the failure this tool
    exists to prevent, so an honest miss is the useful answer.
    """
    result = chain_for("quantum tunnelling")
    assert result["known"] is False
    assert result["chain"] == []
    assert result["immediate"] == []


def test_the_chain_is_reproducible():
    assert chain_for("dijkstra") == chain_for("dijkstra")


def test_a_topic_reachable_by_two_paths_is_taught_once_and_early():
    """"recursion" is reachable via dfs and via memoization at different depths."""
    chain = chain_for("dynamic programming")["chain"]
    assert chain.count("recursion") == 1


def test_topic_lookup_is_case_and_space_insensitive():
    assert chain_for("  Red-Black Tree ")["known"] is True


# -- FR3: build_hint, and the boundary that matters -------------------------


def test_every_hint_the_tool_can_return_was_written_by_an_author():
    """The academic-integrity guarantee, stated structurally.

    The model picks a rung; it does not supply the words. So there is no path
    from model output to the returned string, and the tool cannot emit a
    solution for the same reason a lookup table cannot. That is stronger than
    scanning output for an answer, which only catches phrasings the test author
    imagined.
    """
    authored = all_hints()
    topics = ["bfs", "two-sum", "dynamic programming", "mutex", "quicksort", "", "unknown"]
    for topic in topics:
        for level in range(1, MAX_LEVEL + 1):
            assert call("build_hint", level=level, topic=topic)["hint"] in authored


def test_no_hint_level_contains_a_solution():
    """Spot-check alongside the structural test above."""
    forbidden = ("the answer is", "return ", "def solve", "= {}", "O(n log n)")
    for hint in all_hints():
        lowered = hint.lower()
        for marker in forbidden:
            assert marker.lower() not in lowered, hint


def test_there_is_no_level_four():
    """Escalation, not a fourth hint, is what follows level 3."""
    assert call("build_hint", level=3)["next_level_available"] is False
    # A model asking for level 9 gets level 3, never an index error.
    assert call("build_hint", level=9)["level"] == MAX_LEVEL
    assert call("build_hint", level=0)["level"] == 1


def test_the_hint_ladder_escalates_visibly():
    """The level is an argument, so a student being walked up shows in traces."""
    levels = [call("build_hint", level=n, topic="bfs") for n in (1, 2, 3)]
    assert len({hint["hint"] for hint in levels}) == 3
    assert all(hint["withholds_solution"] for hint in levels)


@pytest.mark.parametrize(
    ("topic", "family"),
    [
        ("BFS on a grid", "graph"),
        ("two-sum", "hashing"),
        ("knapsack DP", "dynamic programming"),
        ("deadlock between threads", "concurrency"),
        ("mergesort", "sorting"),
        ("something unrelated", ""),
    ],
)
def test_topics_map_to_technique_families(topic: str, family: str):
    assert family_for(topic) == family


# -- FR3: analyze_complexity ------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("def f(xs):\n    return len(xs)", "O(1)"),
        ("def f(xs):\n    for x in xs:\n        print(x)", "O(n)"),
        ("def f(xs):\n    for a in xs:\n        for b in xs:\n            print(a, b)", "O(n^2)"),
        ("def f(xs):\n    return sorted(xs)", "O(n log n)"),
        ("def f(xs, t):\n    while lo < hi:\n        mid = (lo + hi) // 2", "O(log n)"),
        ("def fib(n):\n    return fib(n - 1) + fib(n - 2)", "O(2^n)"),
    ],
)
def test_complexity_is_read_from_the_shape_of_the_source(code: str, expected: str):
    assert analyze(code)["complexity"] == expected


def test_analyze_complexity_does_not_execute_the_code():
    """Code arrives from a student; running it would be a sandbox escape."""
    result = analyze("import os\nos.abort()\nwhile True:\n    pass\n")
    assert result["complexity"].startswith("O(")


def test_the_complexity_result_carries_its_own_caveat():
    """A confident wrong answer here is worse than no tool."""
    result = analyze("def f(xs):\n    for x in xs:\n        helper(x)")
    assert "does not run the code" in result["caveat"]
    assert result["evidence"]


# -- FR4: schedule_review ---------------------------------------------------


def test_sm2_matches_the_published_ladder():
    """First success is 1 day, second 6, then interval * ease."""
    first = sm2(quality=5, ease=DEFAULT_EASE, interval=0, repetitions=0, today=TODAY)
    assert first["interval"] == 1
    assert first["due_date"] == "2026-09-15"

    second = sm2(quality=5, ease=first["ease"], interval=1, repetitions=1, today=TODAY)
    assert second["interval"] == 6

    third = sm2(quality=4, ease=2.5, interval=6, repetitions=2, today=TODAY)
    assert third["interval"] == round(6 * 2.5)


def test_the_same_progress_always_yields_the_same_date():
    """The property the whole tool exists for."""
    args = {"quality": 4, "ease": 2.36, "interval": 12, "repetitions": 5, "today": TODAY}
    assert sm2(**args) == sm2(**args)


def test_a_lapse_resets_the_ladder():
    """Not recalled is not "slightly harder" -- the interval starts over."""
    lapsed = sm2(quality=2, ease=2.5, interval=30, repetitions=7, today=TODAY)
    assert lapsed["repetitions"] == 0
    assert lapsed["interval"] == 1
    assert lapsed["lapsed"] is True
    assert lapsed["ease"] < 2.5  # ease updates on lapses too -- canonical SM-2


def test_ease_never_falls_below_the_floor():
    """Below ~1.3 the interval collapses and it stops being spaced repetition."""
    ease = DEFAULT_EASE
    for _ in range(20):
        ease = sm2(quality=0, ease=ease, interval=1, repetitions=0, today=TODAY)["ease"]
    assert ease == MIN_EASE


def test_the_clock_is_an_argument_not_a_hidden_read():
    """A hidden clock makes the determinism test pass today and drift after."""
    early = sm2(quality=5, ease=2.5, interval=0, repetitions=0, today=date(2020, 1, 1))
    assert early["due_date"] == "2020-01-02"


def test_schedule_review_is_reachable_through_the_registry():
    result = call("schedule_review", quality=5, today="2026-09-14")
    assert result["due_date"] == "2026-09-15"


# -- FR5: generate_quiz_spec ------------------------------------------------


@pytest.mark.parametrize("count", [1, 3, 5, 7, 10, 20])
@pytest.mark.parametrize("difficulty", ["easy", "mixed", "hard"])
def test_the_distribution_always_sums_to_the_count(count: int, difficulty: str):
    """Floor-and-hope leaves a remainder whose placement depends on dict order."""
    assert sum(allocate(count, difficulty).values()) == count


def test_the_distribution_is_reproducible():
    assert allocate(7, "mixed") == allocate(7, "mixed")


def test_harder_quizzes_lean_away_from_recall():
    assert allocate(10, "hard")["recall"] < allocate(10, "easy")["recall"]
    assert allocate(10, "hard")["analysis"] > allocate(10, "easy")["analysis"]


def test_the_spec_is_a_shape_not_a_set_of_questions():
    """What makes two quizzes on one topic comparable."""
    spec = call("generate_quiz_spec", topic="hash tables", count=6, difficulty="mixed")
    assert spec["count"] == 6
    assert sum(spec["distribution"].values()) == 6
    assert "questions" not in spec
    assert "rubric" in spec["instructions"]


def test_an_unknown_difficulty_falls_back_rather_than_raising():
    assert call("generate_quiz_spec", topic="x", difficulty="impossible")["difficulty"] == "mixed"


# -- FR5: grade_answer ------------------------------------------------------

RUBRIC = [
    {"id": "uses-queue", "weight": 2.0, "description": "names a queue"},
    {"id": "visited-set", "weight": 2.0, "description": "mentions a visited set"},
    {"id": "level-order", "weight": 1.0, "description": "explains level order"},
]


def test_weights_are_summed_in_code_not_by_the_model():
    result = grade(RUBRIC, {"uses-queue": True, "visited-set": True, "level-order": False})
    assert result["score"] == 4.0
    assert result["max_score"] == 5.0
    assert result["percentage"] == 80.0
    assert result["missed"] == ["level-order"]


def test_the_same_answer_and_rubric_always_give_the_same_mark():
    """The fairness property self-testing depends on."""
    checks = {"uses-queue": True, "visited-set": False, "level-order": True}
    assert grade(RUBRIC, checks) == grade(RUBRIC, checks)


def test_a_rubric_point_nobody_judged_is_reported_not_silently_failed():
    """An unchecked point is a broken grading run, not a wrong answer."""
    result = grade(RUBRIC, {"uses-queue": True})
    assert result["unchecked"] == ["visited-set", "level-order"]
    assert "visited-set" in result["missed"]


def test_the_fingerprint_detects_a_rubric_that_was_regenerated():
    """Plan 3.1.1: regenerating the rubric at grading time destroys fairness."""
    pinned = fingerprint(RUBRIC)
    assert fingerprint(list(reversed(RUBRIC))) == pinned  # order is not meaning
    rewritten = [*RUBRIC[:2], {"id": "level-order", "weight": 3.0, "description": "x"}]
    assert fingerprint(rewritten) != pinned


def test_grading_states_its_own_residual_non_determinism():
    """"Stabilised, not deterministic" is the honest claim -- so the tool says it."""
    result = grade(RUBRIC, {"uses-queue": True, "visited-set": True, "level-order": True})
    assert "model judgements" in result["determinism"]


def test_an_empty_rubric_does_not_divide_by_zero():
    assert grade([], {})["percentage"] == 0.0


# -- FR6: the handoff summary is not a tool ---------------------------------


def test_create_handoff_summary_is_not_registered():
    """TutorHandoff has no tool loop, so a registered tool could never be called.

    Registering it would also invite a tool_scope on that agent, which is one
    edit from giving it a gateway call -- and the role exists to work when the
    gateway does not.
    """
    assert "create_handoff_summary" not in REGISTRY


def test_the_handoff_agent_has_an_empty_tool_scope():
    assert PROFILES[AgentType.TUTOR_HANDOFF].tool_scope == ()


async def test_the_handoff_carries_what_the_ta_needs_and_costs_nothing():
    client = FakeAnthropic()
    agent = TutorHandoffAgent(gateway(client))
    intent = make_intent(
        IntentCategory.HUMAN_TUTOR,
        entities={"topic": "bfs", "problem_id": "two-sum", "due_date": "2026-09-15"},
    )
    response = await agent.handle(request("I need a TA", intent))

    assert client.calls == []
    assert response.escalate is True
    assert "bfs" in response.content
    summary = response.tool_traces[0]["summary"]
    assert summary["question"] == "I need a TA"
    assert summary["problem_id"] == "two-sum"
    assert summary["due_date"] == "2026-09-15"


def test_the_summary_survives_a_request_with_no_intent():
    """It runs during an outage, which is when intent recognition is also down."""
    summary = create_handoff_summary(request("help"))
    assert summary["question"] == "help"
    assert summary["recognised_intent"] == ""


# -- FR2: inspect_request_context -------------------------------------------


def test_the_context_tool_reports_identifiers_as_present_not_by_value():
    """The result re-enters the model's context and from there the logs."""
    from owl_mind.agents.base import AgentRequest

    req = AgentRequest(message="hi", session_id="sess-secret", user_id="user-secret")
    context = REGISTRY["inspect_request_context"].handler(req, {})

    assert context["session_present"] is True
    assert "sess-secret" not in str(context)
    assert "user-secret" not in str(context)


def test_the_context_tool_surfaces_entities_when_intent_exists():
    intent = make_intent(IntentCategory.CONCEPT_EXPLAIN, entities={"topic": "bfs"})
    context = REGISTRY["inspect_request_context"].handler(request("x", intent), {})
    assert context["entities"] == {"topic": "bfs"}
    assert context["intent"] == "concept_explain"


# -- FR7: registration and the contracts ------------------------------------


def test_every_tool_declares_a_validatable_schema():
    """The ISSUE-005 validator only enforces this subset; omitting it opts out."""
    for name, spec in REGISTRY.items():
        schema = spec.input_schema
        assert schema.get("type") == "object", name
        assert schema.get("additionalProperties") is False, name
        assert "properties" in schema, name
        for field, declared in schema["properties"].items():
            assert "type" in declared, f"{name}.{field}"
            assert "description" in declared, f"{name}.{field}"


def test_every_tool_description_says_when_to_call_it():
    """Prescriptive descriptions lift should-call rate; "what it does" does not.

    A proxy, and knowingly so: it looks for directive phrasing rather than
    understanding the sentence. It catches the regression that matters -- a
    description rewritten into a bare noun phrase ("Grades an answer.") -- and
    will not catch a directive that is merely bad advice.
    """
    directives = ("Call this", "Use this", "Use it", "Never ", " before ", "instead of")
    for name, spec in REGISTRY.items():
        assert len(spec.description) > 80, name
        assert any(cue in spec.description for cue in directives), name


def test_the_scope_contract_is_now_load_bearing():
    """Dormant while the registry was empty; the first tool woke it."""
    assert REGISTRY
    _check_tool_scopes_resolve()  # the real roster resolves


def test_a_scope_naming_a_missing_tool_fails_the_boot(monkeypatch):
    monkeypatch.delitem(REGISTRY, "build_hint")
    with pytest.raises(ContractViolation) as excinfo:
        _check_tool_scopes_resolve()
    assert "practice:build_hint" in str(excinfo.value)


def test_an_orphan_tool_fails_the_boot(monkeypatch):
    """A tool in no scope is never offered to any model -- dead weight that reads
    as a half-finished thought."""
    from owl_mind.agents.tools import AgentToolSpec

    monkeypatch.setitem(
        REGISTRY,
        "forgotten",
        AgentToolSpec(
            name="forgotten",
            description="registered but claimed by nobody",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=lambda req, args: {},
        ),
    )
    with pytest.raises(ContractViolation) as excinfo:
        _check_no_orphan_tools()
    assert "forgotten" in str(excinfo.value)


def test_registering_a_duplicate_name_is_rejected():
    from owl_mind.agents.tools import AgentToolSpec, register

    with pytest.raises(ValueError):
        register(
            AgentToolSpec(
                name="build_hint",
                description="duplicate",
                input_schema={"type": "object", "properties": {}},
                handler=lambda req, args: {},
            )
        )


# -- the whitelist, now that the tools are real -----------------------------


@pytest.mark.parametrize(
    ("agent_type", "expected"),
    [
        (AgentType.CONCEPT, {"get_prerequisites", "inspect_request_context"}),
        (
            AgentType.PRACTICE,
            {"build_hint", "analyze_complexity", "inspect_request_context"},
        ),
        (AgentType.PLANNER, {"schedule_review", "inspect_request_context"}),
        (
            AgentType.QUIZ,
            {"generate_quiz_spec", "grade_answer", "inspect_request_context"},
        ),
    ],
)
def test_each_role_is_offered_exactly_its_own_tools(agent_type, expected):
    """PracticeAgent is offered nothing that could produce a solution."""
    from owl_mind.agents.roster import AGENT_CLASSES

    agent = AGENT_CLASSES[agent_type](gateway(FakeAnthropic()))
    assert {tool["name"] for tool in agent.allowed_tools()} == expected


def test_no_role_can_reach_another_roles_tools():
    from owl_mind.agents.roster import AGENT_CLASSES

    practice = AGENT_CLASSES[AgentType.PRACTICE](gateway(FakeAnthropic()))
    offered = {tool["name"] for tool in practice.allowed_tools()}
    assert "grade_answer" not in offered
    assert "schedule_review" not in offered
