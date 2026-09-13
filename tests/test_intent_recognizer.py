"""Intent recognition -- ISSUE-003.

No network calls: the model signal runs through a fake gateway, the embedding
signal through a fake index. Fusion arithmetic is tested with all three signals
stubbed, so it is verified independently of any model.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from owl_mind.core.contracts import ContractViolation
from owl_mind.core.intent_recognizer import (
    _INTENT_GROUPS,
    _TEMPLATES,
    PRIMARY_THRESHOLD,
    SUPPORTING_FLOOR,
    WEIGHTS,
    ChromaTemplateIndex,
    Intent,
    IntentCategory,
    IntentGroup,
    IntentRecognizer,
    UrgencyLevel,
    _check_taxonomy_is_complete,
    _fuse,
    _parse_llm_response,
    _pattern_signal,
    compute_urgency,
    extract_entities,
)
from owl_mind.core.llm_gateway import LLMGateway
from tests.fakes import FakeAnthropic, FakeResponse

NOW = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)  # a Monday


# -- fakes ------------------------------------------------------------------


class FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


def llm_reply(**intents: float) -> FakeResponse:
    """A structured model reply naming intents with confidences."""
    payload = {"intents": [{"intent": k, "confidence": v} for k, v in intents.items()]}
    return FakeResponse(content=[FakeTextBlock(json.dumps(payload))])


class FakeIndex:
    def __init__(self, scores: dict[IntentCategory, float] | None = None,
                 raises: BaseException | None = None) -> None:
        self._scores = scores or {}
        self._raises = raises

    async def search(self, message: str, k: int = 5) -> dict[IntentCategory, float]:
        if self._raises is not None:
            raise self._raises
        return dict(self._scores)


def recognizer(response: Any, index: Any = None, settings=None) -> IntentRecognizer:
    from owl_mind.core.config import get_settings

    gateway = LLMGateway(settings or get_settings(), client=FakeAnthropic(response=response))
    return IntentRecognizer(gateway, index=index)


# -- FR1/FR2: taxonomy ------------------------------------------------------


def test_every_intent_is_in_exactly_one_group():
    assert len(IntentCategory) == 19
    assert set(_INTENT_GROUPS) == set(IntentCategory)
    assert set(_INTENT_GROUPS.values()) == set(IntentGroup)


def test_assess_group_has_several_intents():
    """A group of one is just an intent.

    ASSESS shipped with only quiz_request, which made the group a poor
    attractor -- assessment phrasings drifted into PRACTICE and SUPPORT. The
    additions cover the rest of the loop: submitting an answer, sitting a mock
    exam, and explaining a concept back to be checked.
    """
    assess = {i for i, g in _INTENT_GROUPS.items() if g is IntentGroup.ASSESS}
    assert len(assess) >= 3
    assert IntentCategory.ANSWER_SUBMISSION in assess


def test_every_intent_has_at_least_three_templates():
    for intent in IntentCategory:
        assert len(_TEMPLATES[intent]) >= 3, f"{intent.value} needs more examples"


def test_templates_are_unique_across_intents():
    """A sentence anchoring two intents teaches the embedding signal nothing."""
    seen: dict[str, IntentCategory] = {}
    for intent, examples in _TEMPLATES.items():
        for example in examples:
            assert example not in seen, f"{example!r} is used by {seen.get(example)} and {intent}"
            seen[example] = intent


@pytest.mark.skip(reason="turned on by the evaluation issue, once a corpus exists")
def test_templates_do_not_appear_in_the_eval_corpus():
    """Templates are parameters, not test data.

    A template that also appears as an eval case turns the accuracy number into
    a measure of memorisation. There is no corpus yet; this assertion is the
    placeholder the evaluation issue activates.
    """
    raise AssertionError("implement alongside the eval corpus")


# -- FR10: the startup contract --------------------------------------------


def test_taxonomy_contract_passes_as_shipped():
    _check_taxonomy_is_complete()


def test_taxonomy_contract_fails_when_an_intent_lacks_templates(monkeypatch):
    incomplete = dict(_TEMPLATES)
    del incomplete[IntentCategory.QUIZ_REQUEST]
    monkeypatch.setattr(
        "owl_mind.core.intent_recognizer._TEMPLATES", incomplete
    )

    with pytest.raises(ContractViolation) as excinfo:
        _check_taxonomy_is_complete()

    assert "quiz_request" in str(excinfo.value)


# -- FR3: pattern signal ----------------------------------------------------


def test_patterns_return_every_match_not_just_the_best():
    scores = _pattern_signal("what is the difference between BFS and DFS, and quiz me on it")
    assert IntentCategory.CONCEPT_COMPARE in scores
    assert IntentCategory.QUIZ_REQUEST in scores


def test_patterns_stay_silent_on_ambiguous_input():
    """Precision, not coverage. A rule firing here would poison the fusion."""
    assert _pattern_signal("I have been thinking about trees lately") == {}


def test_greeting_pattern_does_not_fire_mid_sentence():
    assert IntentCategory.GREETING not in _pattern_signal("say hi to the algorithm")


# -- FR6: fusion ------------------------------------------------------------


def test_fusion_sums_across_all_candidates():
    """Hand-calculated, with no model involved."""
    fused = _fuse(
        {
            "llm": {IntentCategory.CONCEPT_EXPLAIN: 0.6, IntentCategory.QUIZ_REQUEST: 0.4},
            "embedding": {IntentCategory.CONCEPT_EXPLAIN: 0.8},
            "pattern": {IntentCategory.QUIZ_REQUEST: 0.9},
        }
    )
    # All three signals contributed, so the divisor is the full 1.0 and the
    # weighted mean and the weighted sum coincide.
    assert fused[IntentCategory.CONCEPT_EXPLAIN] == pytest.approx(0.6 * 0.5 + 0.8 * 0.35)
    assert fused[IntentCategory.QUIZ_REQUEST] == pytest.approx(0.4 * 0.5 + 0.9 * 0.15)


def test_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_fusion_divides_by_the_signals_that_contributed():
    """A silent signal forfeits its vote; it does not cap everyone else's.

    Summing with an implicit divisor of 1.0 meant a missing signal held the
    whole distribution below its own ceiling -- with the model absent, nothing
    could clear the primary threshold no matter how certain the other two were.
    """
    full = _fuse(
        {
            "llm": {IntentCategory.QUIZ_REQUEST: 1.0},
            "embedding": {IntentCategory.QUIZ_REQUEST: 1.0},
            "pattern": {IntentCategory.QUIZ_REQUEST: 1.0},
        }
    )
    degraded = _fuse(
        {
            "llm": {},
            "embedding": {IntentCategory.QUIZ_REQUEST: 1.0},
            "pattern": {IntentCategory.QUIZ_REQUEST: 1.0},
        }
    )

    assert full[IntentCategory.QUIZ_REQUEST] == pytest.approx(1.0)
    # Two certain signals are certain, whatever the third would have said.
    assert degraded[IntentCategory.QUIZ_REQUEST] == pytest.approx(1.0)
    assert degraded[IntentCategory.QUIZ_REQUEST] >= PRIMARY_THRESHOLD


def test_fusion_of_nothing_is_empty_not_a_division_by_zero():
    assert _fuse({"llm": {}, "embedding": {}, "pattern": {}}) == {}


# -- FR5/FR7: end to end through the fakes ---------------------------------


async def test_single_intent_message():
    rec = recognizer(
        llm_reply(concept_explain=0.95),
        index=FakeIndex({IntentCategory.CONCEPT_EXPLAIN: 0.8}),
    )
    intent = await rec.recognize("how does quicksort work", now=NOW)

    assert intent.category is IntentCategory.CONCEPT_EXPLAIN
    assert intent.group is IntentGroup.LEARN
    assert set(intent.source_scores) == {"llm", "embedding", "pattern"}
    assert intent.supporting_candidates() == []


async def test_composite_message_surfaces_a_second_intent():
    """The requirement: one message, several agents, decided on intent evidence.

    quiz_request must clear the supporting floor from the *intent result* --
    not from keyword matching downstream.
    """
    rec = recognizer(
        llm_reply(concept_explain=0.7, quiz_request=0.5),
        index=FakeIndex(
            {IntentCategory.CONCEPT_EXPLAIN: 0.7, IntentCategory.QUIZ_REQUEST: 0.5}
        ),
    )
    intent = await rec.recognize("explain BFS and then quiz me on it", now=NOW)

    assert intent.category is IntentCategory.CONCEPT_EXPLAIN
    assert intent.scores[IntentCategory.QUIZ_REQUEST] >= SUPPORTING_FLOOR
    assert IntentCategory.QUIZ_REQUEST in intent.supporting_candidates()


async def test_supporting_candidates_are_ordered_and_deterministic():
    rec = recognizer(
        llm_reply(concept_explain=0.8, quiz_request=0.6, study_plan=0.5),
        index=FakeIndex({}),
    )
    intent = await rec.recognize("a message touching several things", now=NOW)

    candidates = intent.supporting_candidates()
    assert candidates == [IntentCategory.QUIZ_REQUEST, IntentCategory.STUDY_PLAN]
    # Stable across repeated evaluation.
    assert candidates == intent.supporting_candidates()


async def test_primary_tie_breaks_by_name_not_dict_order():
    """FR6.1 fixes the tie order; two intents can share a first letter.

    concept_compare and concept_explain tie here. An argmax that only compares
    the first character cannot separate them, so the winner falls out of dict
    insertion order -- which flips with the order the signals happened to
    return. Routing has to be reproducible to be testable.
    """
    rec = recognizer(
        llm_reply(concept_explain=0.6, concept_compare=0.6), index=FakeIndex({})
    )
    forward = await rec.recognize("compare and explain", now=NOW)

    rec_reversed = recognizer(
        llm_reply(concept_compare=0.6, concept_explain=0.6), index=FakeIndex({})
    )
    backward = await rec_reversed.recognize("compare and explain", now=NOW)

    assert forward.category is IntentCategory.CONCEPT_COMPARE
    assert backward.category is forward.category


async def test_low_confidence_falls_back_to_other():
    rec = recognizer(llm_reply(concept_explain=0.2), index=FakeIndex({}))
    intent = await rec.recognize("mmm", now=NOW)

    assert intent.category is IntentCategory.OTHER
    assert intent.confidence < PRIMARY_THRESHOLD
    assert set(intent.source_scores) == {"llm", "embedding", "pattern"}


async def test_embedding_failure_degrades_rather_than_raising():
    rec = recognizer(
        llm_reply(concept_explain=0.9),
        index=FakeIndex(raises=RuntimeError("chroma down")),
    )
    intent = await rec.recognize("explain paging", now=NOW)

    assert intent.category is IntentCategory.CONCEPT_EXPLAIN
    assert intent.source_scores["embedding"] == 0.0


async def test_model_failure_degrades_to_the_other_signals():
    from owl_mind.core.config import get_settings

    gateway = LLMGateway(
        get_settings(), client=FakeAnthropic(raises=RuntimeError("api down"))
    )
    rec = IntentRecognizer(gateway, index=FakeIndex({IntentCategory.QUIZ_REQUEST: 0.9}))
    intent = await rec.recognize("quiz me on hash tables", now=NOW)

    # The two surviving signals agree, and the divisor is their weight rather
    # than the full panel, so they can still clear the primary threshold on
    # their own. Under the reference implementation's implicit divisor of 1.0
    # this capped out at 0.275 and every request during a model outage came
    # back OTHER -- a degraded classifier reported as an unclassifiable user.
    assert intent.source_scores["llm"] == 0.0
    assert intent.category is IntentCategory.QUIZ_REQUEST
    assert intent.confidence >= PRIMARY_THRESHOLD
    assert intent.scores[IntentCategory.QUIZ_REQUEST] > 0


async def test_no_network_and_component_label_is_intent():
    rec = recognizer(llm_reply(greeting=0.9), index=FakeIndex({}))
    await rec.recognize("hi", now=NOW)

    sent = rec._gateway._client.calls[0]
    assert "temperature" not in sent
    assert sent["max_tokens"] == 400


def test_malformed_model_output_is_tolerated():
    assert _parse_llm_response(FakeResponse(content=[FakeTextBlock("not json")])) == {}
    assert _parse_llm_response(FakeResponse(content=[])) == {}


def test_unknown_intent_from_the_model_is_dropped():
    reply = FakeResponse(
        content=[FakeTextBlock(json.dumps({"intents": [{"intent": "nonsense", "confidence": 1}]}))]
    )
    assert _parse_llm_response(reply) == {}


# -- FR8: entities ----------------------------------------------------------


def test_extracts_problem_and_due_date():
    entities = extract_entities("I'm stuck on two-sum and it's due tomorrow", now=NOW)
    assert entities["problem_id"] == "two-sum"
    assert entities["due_date"] == "2026-09-15"


def test_extracts_numbered_problem_language_and_complexity():
    entities = extract_entities(
        "my Python answer to question 12 runs in O(n log n)", now=NOW
    )
    assert entities["problem_id"] == "12"
    assert entities["language"] == "python"
    assert entities["complexity_class"] == "O(nlogn)"


def test_three_digit_numbers_are_not_error_codes():
    """The regression the reference implementation's \\b([45]\\d{2})\\b caused.

    Prices, counts, and line numbers all matched it and were sold downstream as
    structured routing input.
    """
    entities = extract_entities("the array has 500 elements and line 404 crashes", now=NOW)
    assert "error_code" not in entities
    assert all("error" not in key for key in entities)


def test_absent_entities_are_omitted_not_guessed():
    assert extract_entities("hello", now=NOW) == {}


# -- FR9: urgency -----------------------------------------------------------


def test_human_tutor_is_critical():
    assert compute_urgency(IntentCategory.HUMAN_TUTOR, {}, now=NOW) is UrgencyLevel.CRITICAL


@pytest.mark.parametrize(
    ("due", "expected"),
    [
        ("2026-09-14", UrgencyLevel.CRITICAL),  # today
        ("2026-09-15", UrgencyLevel.CRITICAL),  # tomorrow
        ("2026-09-16", UrgencyLevel.HIGH),      # within 3 days
        ("2026-09-30", UrgencyLevel.MEDIUM),    # further out
    ],
)
def test_urgency_tracks_deadline_proximity(due, expected):
    entities = {"due_date": due}
    assert compute_urgency(IntentCategory.DEADLINE, entities, now=NOW) is expected


def test_plain_question_is_low_urgency():
    assert compute_urgency(IntentCategory.CONCEPT_EXPLAIN, {}, now=NOW) is UrgencyLevel.LOW


async def test_deadline_in_message_raises_urgency_end_to_end():
    rec = recognizer(llm_reply(problem_help=0.9), index=FakeIndex({}))
    intent = await rec.recognize("stuck on two-sum, due tomorrow", now=NOW)

    assert intent.urgency is UrgencyLevel.CRITICAL
    assert intent.entities["due_date"] == "2026-09-15"


# -- FR4: the cosine/L2 trap ------------------------------------------------


class FakeChromaCollection:
    def __init__(self, distances: list[float]) -> None:
        self.distances = distances
        self.upserted: dict[str, Any] = {}

    def upsert(self, **kwargs: Any) -> None:
        self.upserted = kwargs

    def query(self, query_texts: list[str], n_results: int) -> dict[str, Any]:
        intents = [IntentCategory.CONCEPT_EXPLAIN.value, IntentCategory.QUIZ_REQUEST.value]
        return {
            "metadatas": [[{"intent": i} for i in intents[: len(self.distances)]]],
            "distances": [self.distances],
        }


class FakeChromaClient:
    def __init__(self, collection: FakeChromaCollection) -> None:
        self.collection = collection
        self.create_kwargs: dict[str, Any] = {}

    def get_or_create_collection(self, **kwargs: Any) -> FakeChromaCollection:
        self.create_kwargs = kwargs
        return self.collection


async def test_collection_is_created_with_cosine_distance():
    """Chroma defaults to squared L2; 1.0 - distance is only valid for cosine.

    The reference implementation got this wrong and produced relevance scores on
    the wrong scale that could go negative (plan section 11.4).
    """
    client = FakeChromaClient(FakeChromaCollection([0.1]))
    index = ChromaTemplateIndex(client)
    await index.seed()

    assert client.create_kwargs["metadata"] == {"hnsw:space": "cosine"}


async def test_similarity_scores_stay_within_zero_and_one():
    # 1.7 would yield -0.7 under a naive 1.0 - distance.
    client = FakeChromaClient(FakeChromaCollection([0.05, 1.7]))
    index = ChromaTemplateIndex(client)
    await index.seed()

    scores = await index.search("anything")
    assert scores, "expected at least one match"
    assert all(0.0 <= score <= 1.0 for score in scores.values())


async def test_seed_uploads_every_template():
    client = FakeChromaClient(FakeChromaCollection([0.1]))
    index = ChromaTemplateIndex(client)
    await index.seed()

    expected = sum(len(examples) for examples in _TEMPLATES.values())
    assert len(client.collection.upserted["documents"]) == expected


async def test_unseeded_index_returns_nothing_rather_than_raising():
    assert await ChromaTemplateIndex(FakeChromaClient(FakeChromaCollection([]))).search("x") == {}


# -- housekeeping -----------------------------------------------------------


def test_intent_is_frozen():
    intent = Intent(category=IntentCategory.GREETING, group=IntentGroup.SUPPORT, confidence=1.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        intent.category = IntentCategory.OTHER  # type: ignore[misc]


# -- ISSUE-005 pre-work: the three contracts routing depends on -------------
#
# These pin decisions taken before the orchestrator was written. Each one
# changes what routing does, so each is asserted on the Intent rather than
# left to the orchestrator to rediscover.


async def test_embedding_similarity_alone_cannot_promote_an_agent():
    """The cost gate: index noise must not buy a second agent.

    concept_compare here has no model support at all -- only a plausible
    cosine similarity against a short template. It clears SUPPORTING_FLOOR on
    the fused score (0.35 * 0.62 / 0.85 = 0.255) and would otherwise become a
    supporting agent, which also forces the composer call that merging two
    answers requires: two extra model calls on a single-intent request.
    """
    rec = recognizer(
        llm_reply(concept_explain=0.95),
        index=FakeIndex(
            {IntentCategory.CONCEPT_EXPLAIN: 0.8, IntentCategory.CONCEPT_COMPARE: 0.62}
        ),
    )
    intent = await rec.recognize("how does quicksort partitioning work", now=NOW)

    assert intent.category is IntentCategory.CONCEPT_EXPLAIN
    # The evidence is still recorded -- it is the suppression that is asserted,
    # not the absence of the score.
    assert intent.scores[IntentCategory.CONCEPT_COMPARE] >= SUPPORTING_FLOOR
    assert IntentCategory.CONCEPT_COMPARE not in intent.corroborated
    assert intent.supporting_candidates() == []


async def test_a_pattern_rule_corroborates_even_when_the_model_misses_it():
    """Patterns exist for precision, so they corroborate; embeddings do not.

    "quiz me" is an unmistakable phrasing. The rule that exists exactly for
    that case counts as evidence a second request was made, whereas surface
    similarity to a template does not.
    """
    rec = recognizer(llm_reply(concept_explain=0.9), index=FakeIndex({}))
    intent = await rec.recognize("explain BFS and then quiz me on it", now=NOW)

    assert intent.category is IntentCategory.CONCEPT_EXPLAIN
    assert IntentCategory.QUIZ_REQUEST in intent.corroborated


def test_the_pattern_signal_cannot_reach_the_supporting_floor_alone():
    """A limit of the current weights, pinned so ISSUE-004 tunes against it.

    At weight 0.15 the pattern signal's ceiling is 0.15/(0.5+0.15) = 0.2308
    when the model also votes, and 0.15 when all three do -- both under
    SUPPORTING_FLOOR. So a pattern rule can move the *primary* but can never
    add a second agent on its own: composite routing rests entirely on the
    model returning both intents. Promoting a lone pattern hit would need its
    weight raised to about 0.217.

    The corroboration gate in supporting_candidates() therefore admits pattern
    hits that cannot currently arrive. That is deliberate -- the gate encodes
    which signals count as evidence, and stays correct if the weights move.
    """
    ceiling = WEIGHTS["pattern"] / (WEIGHTS["llm"] + WEIGHTS["pattern"])
    assert ceiling < SUPPORTING_FLOOR

    fused = _fuse(
        {
            "llm": {IntentCategory.CONCEPT_EXPLAIN: 0.9},
            "embedding": {},
            "pattern": {IntentCategory.QUIZ_REQUEST: 1.0},
        }
    )
    assert fused[IntentCategory.QUIZ_REQUEST] < SUPPORTING_FLOOR


async def test_low_confidence_other_records_what_it_displaced():
    """"Evidence below bar": something was asked, the panel disagreed.

    The orchestrator asks which candidate was meant, so the candidate and its
    per-signal scores have to survive. Reporting the displaced score against
    OTHER described neither intent.
    """
    rec = recognizer(llm_reply(concept_explain=0.25), index=FakeIndex({}))
    intent = await rec.recognize("mmm something about trees maybe", now=NOW)

    assert intent.category is IntentCategory.OTHER
    assert intent.is_fallback
    assert intent.fallback_from is IntentCategory.CONCEPT_EXPLAIN
    # confidence describes `category`, and OTHER was never scored.
    assert intent.confidence == 0.0
    # source_scores describes the displaced candidate, not OTHER.
    assert intent.source_scores["llm"] == pytest.approx(0.25)
    # The distribution survives for the clarifying question.
    assert intent.scores[IntentCategory.CONCEPT_EXPLAIN] > 0


async def test_confident_other_is_not_a_fallback():
    """"No relevant intent": the model classified the message as off-topic.

    Routing declines this; it asks a clarifying question for the fallback
    above. Collapsing the two loses the distinction.
    """
    rec = recognizer(llm_reply(other=0.95), index=FakeIndex({}))
    intent = await rec.recognize("what is the weather like", now=NOW)

    assert intent.category is IntentCategory.OTHER
    assert not intent.is_fallback
    assert intent.fallback_from is None
    assert intent.confidence >= PRIMARY_THRESHOLD


async def test_no_signal_returns_anything_is_not_a_fallback():
    """Every signal silent is absence of evidence, not ambiguous evidence."""
    from owl_mind.core.config import get_settings

    gateway = LLMGateway(get_settings(), client=FakeAnthropic(raises=RuntimeError("down")))
    rec = IntentRecognizer(gateway, index=FakeIndex({}))
    intent = await rec.recognize("zzzzz", now=NOW)

    assert intent.category is IntentCategory.OTHER
    assert not intent.is_fallback
    assert intent.scores == {}
    assert intent.supporting_candidates() == []


async def test_an_intent_too_weak_to_lead_is_too_weak_to_support():
    rec = recognizer(
        llm_reply(concept_explain=0.3, quiz_request=0.3), index=FakeIndex({})
    )
    intent = await rec.recognize("unclear", now=NOW)

    assert intent.is_fallback
    assert intent.supporting_candidates() == []


# -- FR8 regression: substring and declaration-order matching ---------------


@pytest.mark.parametrize(
    ("message", "key", "absent"),
    [
        # "al-go-rithm" tagged every algorithm question as Go.
        ("explain the algorithm behind quicksort", "language", "go"),
        # "f-rust-rated" tagged the MOTIVATION phrasings as Rust.
        ("I am so frustrated with this recursion problem", "language", "rust"),
        # "de-queue" matched queue.
        ("how do I dequeue from a linked list", "topic", "queue"),
    ],
)
def test_substrings_are_not_entities(message: str, key: str, absent: str):
    assert extract_entities(message, now=NOW).get(key) != absent


@pytest.mark.parametrize(
    ("message", "key", "expected"),
    [
        # "java" is declared before "javascript"; longest match must win.
        ("why does my javascript closure not work", "language", "javascript"),
        # "binary search" is declared before the tree topics.
        ("explain binary search trees to me", "topic", "binary search tree"),
        # Plurals resolve to the singular term.
        ("quiz me on hash tables", "topic", "hash table"),
        ("how do mutexes work", "topic", "mutex"),
    ],
)
def test_the_most_specific_term_wins(message: str, key: str, expected: str):
    assert extract_entities(message, now=NOW)[key] == expected


def test_one_topic_is_reported_and_the_longest_wins():
    """A known limit, pinned rather than left incidental.

    ``topic`` is singular, so a message naming two topics reports one of them,
    and longest-first ordering picks the longer term -- not the one the student
    is actually asking about. This is no worse than the declaration-order
    behaviour it replaced, but it is still arbitrary. Whoever consumes ``topic``
    as a tool argument should decide whether to return all matches.
    """
    entities = extract_entities("how do mutexes differ from semaphores", now=NOW)
    assert entities["topic"] == "semaphore"


# -- ISSUE-008 FR4 / ISSUE-006 F2 ------------------------------------------


async def test_intent_call_disables_thinking() -> None:
    """The classifier must not spend its token budget reasoning.

    On the configured model, omitting `thinking` runs *adaptive* thinking, and
    max_tokens caps thinking and output together. At max_tokens=400 that could
    spend the budget before the JSON was finished: the parse would fail, this
    signal would return {} on a call that looked successful, and fusion would
    quietly renormalise over the two weaker signals. Classification against a
    fixed taxonomy with a schema-constrained answer has nothing for thinking to
    improve, so it is switched off rather than budgeted around.
    """
    from owl_mind.core.config import get_settings
    from owl_mind.core.llm_gateway import LLMGateway
    from tests.fakes import FakeAnthropic

    fake = FakeAnthropic()
    recognizer = IntentRecognizer(LLMGateway(get_settings(), client=fake), index=None)
    await recognizer.recognize("explain BFS")

    assert fake.calls, "the intent signal never called the model"
    assert fake.calls[0]["thinking"] == {"type": "disabled"}
