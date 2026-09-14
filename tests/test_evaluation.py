"""Evaluation harness. ISSUE-009.

Everything here runs offline and free. The scoring path is pure functions over
recorded signals, so a fabricated record exercises the same code a paid run
would -- which is the point of separating collection from scoring.

The contamination test is the one that matters most: it is the only thing
standing between the corpus and a number that measures memorisation.
"""

from __future__ import annotations

import pytest

from owl_mind.evaluation import contamination, metrics
from owl_mind.evaluation.corpus import (
    THIN_CLASS_THRESHOLD,
    CorpusError,
    EvalCase,
    load,
    normalise,
    summarise,
)
from owl_mind.evaluation.harness import REGRESSION_MARGIN, SignalRecord, compare, score
from owl_mind.evaluation.sweep import answer_f13, sweep

# -- the corpus -------------------------------------------------------------


def test_corpus_loads_and_every_label_is_in_the_taxonomy() -> None:
    # load() raises on an unknown label; reaching here means every case maps to
    # a real intent, so none of them can score as a permanent miss.
    cases = load()
    assert len(cases) > 50


def test_corpus_has_the_shape_the_metrics_need() -> None:
    summary = summarise(load())
    assert summary.out_of_scope >= 15, "OOS is the failure mode that matters most"
    assert summary.composite >= 10, "without composites a switch statement would pass"
    assert summary.by_split["train"] and summary.by_split["test"]


def test_split_is_stable_under_corpus_growth() -> None:
    """A case must keep its split when other cases are added.

    Hashing the message rather than shuffling a list is what makes this hold.
    If the split moved when the corpus grew, every baseline comparison would be
    against a different test set and the margin would be meaningless.
    """
    case = EvalCase(
        message="explain how breadth-first search works",
        expected_intent="concept_explain",
    )
    assert case.split == EvalCase(message=case.message, expected_intent="other").split


def test_duplicate_cases_are_rejected(tmp_path) -> None:
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        '{"message": "quiz me on trees", "expected_intent": "quiz_request"}\n'
        '{"message": "Quiz me on trees.", "expected_intent": "quiz_request"}\n',
        encoding="utf-8",
    )
    # Normalised duplicates too: a repeated case is double-weighted in the mean.
    with pytest.raises(CorpusError, match="duplicates"):
        load(path)


def test_unknown_intent_is_rejected(tmp_path) -> None:
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        '{"message": "hello", "expected_intent": "not_a_real_intent"}\n',
        encoding="utf-8",
    )
    with pytest.raises(CorpusError, match="unknown intent"):
        load(path)


def test_thin_classes_are_reported_not_hidden() -> None:
    """The corpus is below the n=10 target and must say so itself."""
    summary = summarise(load())
    assert summary.thin_classes, (
        "every class is above the threshold -- update THIN_CLASS_THRESHOLD or "
        "this test is no longer telling the truth about corpus size"
    )
    for label in summary.thin_classes:
        assert summary.by_intent.get(label, 0) < THIN_CLASS_THRESHOLD


# -- contamination ----------------------------------------------------------


def test_the_shipped_corpus_is_not_contaminated() -> None:
    """The templates are the embedding index; overlap means it scores itself."""
    collisions = contamination.check_lexical(load())
    assert not collisions, "\n".join(c.describe() for c in collisions)


def test_contamination_catches_an_exact_template() -> None:
    case = EvalCase(message="quiz me on graph traversal", expected_intent="quiz_request")
    found = contamination.check_lexical([case])
    assert found and found[0].kind == "exact"


def test_contamination_catches_a_reworded_template() -> None:
    """The realistic failure: the test goes red, someone changes one word.

    An exact-match check would pass this. That is why Jaccard is here -- it was
    not hypothetical, it caught exactly this case in the shipped corpus on the
    check's first run.
    """
    case = EvalCase(
        message="quiz me on graph traversals",
        expected_intent="quiz_request",
    )
    found = contamination.check_lexical([case])
    assert found, "a one-word edit of a template slipped through"
    assert found[0].similarity >= contamination.JACCARD_THRESHOLD


def test_a_skipped_semantic_check_is_not_reported_as_clean() -> None:
    """Reporting a skipped check as a pass is how a check stops being one."""
    report = contamination.report([], semantic=True)
    if not report["semantic_ran"]:
        assert "semantic_note" in report


# -- metrics ----------------------------------------------------------------


def test_per_class_scores_carry_their_counts() -> None:
    report = metrics.score_intents(
        [
            ("quiz_request", "quiz_request"),
            ("quiz_request", "mock_exam"),
            ("mock_exam", "mock_exam"),
        ]
    )
    quiz = report.per_class["quiz_request"]
    assert quiz.support == 2
    assert quiz.recall == 0.5
    # An F1 of 0.67 resting on 2 cases must be visibly 2 cases.
    assert quiz.as_dict()["support"] == 2


def test_macro_f1_is_not_dominated_by_a_frequent_class() -> None:
    """Macro, not micro: corpus frequency is an artefact of who wrote it."""
    pairs = [("a", "a")] * 90 + [("b", "c")] * 10
    report = metrics.score_intents(pairs)
    assert report.accuracy == 0.9
    # One class perfect, one at zero, plus the false-positive class: macro
    # refuses to report 0.9.
    assert report.macro_f1 < 0.6


def test_worst_confusions_names_the_pair() -> None:
    pairs = [("answer_submission", "homework_check")] * 4 + [("greeting", "greeting")]
    worst = metrics.score_intents(pairs).worst_confusions()
    assert worst[0] == ("answer_submission", "homework_check", 4)


def test_scope_separates_the_two_directions() -> None:
    """A false decline is worse than a missed OOS and is counted apart."""
    report = metrics.score_scope(
        [("other", "other"), ("other", "quiz_request"), ("quiz_request", "other")]
    )
    assert report.oos_recall == 0.5
    assert report.false_decline_rate == 1.0


def test_routing_counts_fan_out() -> None:
    report = metrics.score_routing(
        [
            ("concept", "concept", frozenset(), frozenset()),
            ("concept", "concept", frozenset({"quiz"}), frozenset({"quiz"})),
        ]
    )
    assert report.primary_accuracy == 1.0
    assert report.supporting_accuracy == 1.0
    assert report.fan_out == {1: 1, 2: 1}
    assert report.multi_agent_share == 0.5


# -- scoring a run ----------------------------------------------------------


def _record(**kwargs) -> SignalRecord:
    base = {
        "message": "explain BFS",
        "expected_intent": "concept_explain",
        "source": "seed",
        "expected_primary_agent": "concept",
        "llm": {"concept_explain": 0.9},
    }
    return SignalRecord(**{**base, **kwargs})


def test_scoring_is_free_and_reproduces_routing() -> None:
    report = score([_record()])
    assert report["scored"] == 1
    assert report["routing"]["primary_accuracy"] == 1.0


def test_a_failed_signal_is_excluded_not_scored_as_a_miss() -> None:
    """A provider outage is not a classifier error.

    Scored as a miss it would depress every class it touched and look like a
    quality regression, which is the reference implementation's judge bug in a
    different costume.
    """
    report = score([_record(), _record(message="x", error="APIStatusError: 529")])
    assert report["scored"] == 1
    assert report["signal_failures"] == 1
    assert report["intent"]["accuracy"] == 1.0


def test_provenance_is_scored_separately() -> None:
    report = score(
        [_record(), _record(message="a different one", source="human", llm={"other": 0.9})]
    )
    assert set(report["by_source"]) == {"seed", "human"}


def test_the_weight_sweep_runs_offline_and_is_ordered() -> None:
    rows = sweep([_record(), _record(message="quiz me", expected_intent="quiz_request",
                                     expected_primary_agent="quiz", llm={"quiz_request": 0.9})])
    assert len(rows) == 7
    assert rows == sorted(rows, key=lambda r: -r["macro_f1"])


def test_f13_reports_a_verdict_even_when_nothing_changes() -> None:
    result = answer_f13([_record()])
    assert result.verdict


def test_the_pattern_override_is_visible_in_the_sweep() -> None:
    """The F13 case, scored: the weighting decides who leads.

    llm ranks concept_explain over quiz_request; one pattern rule matches the
    quiz half. At weights that give pattern 0.15 the regex wins the primary; at
    model-only weights it does not. This is the measurement the issue exists to
    produce, on one case rather than a corpus.
    """
    record = _record(
        message="explain BFS then quiz me",
        llm={"concept_explain": 0.88, "quiz_request": 0.74},
        pattern={"quiz_request": 0.95},
    )
    by_weights = {
        tuple(row["weights"].values()): row["primary_accuracy"] for row in sweep([record])
    }
    model_only = by_weights[(1.0, 0.0, 0.0)]
    current = by_weights[(0.5, 0.35, 0.15)]
    assert model_only == 1.0, "the model alone routes this to concept, as expected"
    assert current == 0.0, "with pattern at 0.15 the regex takes the primary"


# -- the baseline -----------------------------------------------------------


def test_compare_flags_a_per_class_drop() -> None:
    baseline = {"intent": {"per_class": {"quiz_request": {"f1": 0.9, "support": 5}}}}
    now = {"intent": {"per_class": {"quiz_request": {"f1": 0.5, "support": 5}}}}
    drops = compare(now, baseline)
    assert drops and "quiz_request" in drops[0]


def test_compare_tolerates_movement_inside_the_margin() -> None:
    baseline = {"intent": {"per_class": {"a": {"f1": 0.90, "support": 5}}}}
    now = {"intent": {"per_class": {"a": {"f1": 0.90 - REGRESSION_MARGIN / 2, "support": 5}}}}
    assert compare(now, baseline) == []


def test_compare_catches_a_class_that_vanished() -> None:
    """Macro-F1 can hold steady while one class disappears entirely."""
    baseline = {"intent": {"per_class": {"a": {"f1": 0.9, "support": 5}}}}
    assert compare({"intent": {"per_class": {}}}, baseline)


def test_normalise_is_conservative() -> None:
    # Aggressive normalisation would collapse genuinely different cases and
    # silently shrink the corpus.
    assert normalise("  Quiz me on Trees! ") == "quiz me on trees"
    assert normalise("a stack") != normalise("a stacks")
