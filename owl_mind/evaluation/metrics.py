"""Scoring. Pure functions over recorded predictions.

Implements ISSUE-009 FR3 and FR4.

Nothing here calls a model or touches the network, which is the point: the
expensive pass records what each signal said, and every number below is
computed from that record. A weight sweep is therefore free after the first
run, and the metrics can be tested without spending anything.

Why per-class and not accuracy
------------------------------
One accuracy figure over 19 classes hides which pairs are confused, and the
confused pairs are the only actionable output. A classifier at 0.85 that
cannot tell ``answer_submission`` from ``homework_check`` needs a taxonomy fix;
one at 0.85 that is uniformly slightly wrong needs a weight change. The single
number cannot distinguish those.

Counts travel with every rate for the same reason -- an F1 of 0.80 that is 4/5
should be visibly 4/5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ClassScore:
    """Precision, recall and F1 for one intent, with the counts behind them."""

    label: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def support(self) -> int:
        """How many cases actually carry this label."""
        return self.true_positives + self.false_negatives

    @property
    def precision(self) -> float:
        predicted = self.true_positives + self.false_positives
        return self.true_positives / predicted if predicted else 0.0

    @property
    def recall(self) -> float:
        return self.true_positives / self.support if self.support else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "support": self.support,
            # The raw counts are carried so a reader can see that an impressive
            # rate rests on three cases.
            "tp": self.true_positives,
            "fp": self.false_positives,
            "fn": self.false_negatives,
        }


@dataclass
class IntentReport:
    """Per-class scores plus the confusion matrix that explains them."""

    per_class: dict[str, ClassScore] = field(default_factory=dict)
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    total: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def macro_f1(self) -> float:
        """Unweighted mean F1 across classes that have support.

        Macro rather than micro on purpose: micro-F1 is dominated by whichever
        intents happen to be frequent in the corpus, and corpus frequency here
        is an artefact of how many cases someone felt like writing, not of how
        often students ask.
        """
        scored = [s.f1 for s in self.per_class.values() if s.support]
        return sum(scored) / len(scored) if scored else 0.0

    def worst_confusions(self, limit: int = 10) -> list[tuple[str, str, int]]:
        """The most frequent (expected, predicted) mistakes, worst first.

        This is the actionable output of the whole harness: it names the pairs
        whose boundary is not holding.
        """
        pairs = [
            (expected, predicted, count)
            for expected, row in self.confusion.items()
            for predicted, count in row.items()
            if expected != predicted and count
        ]
        pairs.sort(key=lambda item: (-item[2], item[0], item[1]))
        return pairs[:limit]

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "correct": self.correct,
            "accuracy": round(self.accuracy, 4),
            "macro_f1": round(self.macro_f1, 4),
            "per_class": {
                label: score.as_dict()
                for label, score in sorted(self.per_class.items())
                # Classes with no cases and no predictions carry no information;
                # printing 19 rows of zeroes buries the ones that matter.
                if score.support or score.false_positives
            },
            "worst_confusions": [
                {"expected": e, "predicted": p, "count": c}
                for e, p, c in self.worst_confusions()
            ],
        }


def score_intents(pairs: list[tuple[str, str]]) -> IntentReport:
    """Score ``(expected, predicted)`` pairs.

    Labels are taken from the data rather than the enum so this stays usable
    for a sweep over a subset, and so a prediction outside the taxonomy shows
    up as its own row instead of being silently dropped.
    """
    report = IntentReport(total=len(pairs))
    labels = {label for pair in pairs for label in pair}
    tp = dict.fromkeys(labels, 0)
    fp = dict.fromkeys(labels, 0)
    fn = dict.fromkeys(labels, 0)

    for expected, predicted in pairs:
        report.confusion.setdefault(expected, {})
        report.confusion[expected][predicted] = (
            report.confusion[expected].get(predicted, 0) + 1
        )
        if expected == predicted:
            tp[expected] += 1
            report.correct += 1
        else:
            fp[predicted] += 1
            fn[expected] += 1

    report.per_class = {
        label: ClassScore(
            label=label,
            true_positives=tp[label],
            false_positives=fp[label],
            false_negatives=fn[label],
        )
        for label in sorted(labels)
    }
    return report


@dataclass
class ScopeReport:
    """In-scope vs out-of-scope, reported apart from the average.

    Folding OOS into overall accuracy hides the failure that matters most in
    production: a real student asks something the taxonomy does not cover, and
    the system answers confidently with the nearest wrong intent.
    """

    oos_total: int = 0
    oos_correct: int = 0
    in_scope_total: int = 0
    in_scope_wrongly_declined: int = 0

    @property
    def oos_recall(self) -> float:
        """Fraction of out-of-scope messages correctly sent to `other`."""
        return self.oos_correct / self.oos_total if self.oos_total else 0.0

    @property
    def false_decline_rate(self) -> float:
        """Fraction of real questions wrongly declined -- the costly direction.

        A missed OOS is an odd answer to an odd question. A false decline turns
        a student with a legitimate question away, which is worse.
        """
        return (
            self.in_scope_wrongly_declined / self.in_scope_total
            if self.in_scope_total
            else 0.0
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "oos_total": self.oos_total,
            "oos_recall": round(self.oos_recall, 4),
            "in_scope_total": self.in_scope_total,
            "false_declines": self.in_scope_wrongly_declined,
            "false_decline_rate": round(self.false_decline_rate, 4),
        }


def score_scope(pairs: list[tuple[str, str]]) -> ScopeReport:
    report = ScopeReport()
    for expected, predicted in pairs:
        if expected == "other":
            report.oos_total += 1
            report.oos_correct += predicted == "other"
        else:
            report.in_scope_total += 1
            report.in_scope_wrongly_declined += predicted == "other"
    return report


@dataclass
class RoutingReport:
    """Primary and supporting agents scored separately.

    They fail differently and the difference matters: a wrong primary is a
    wrong answer, a missing supporter is a thin one.
    """

    primary_total: int = 0
    primary_correct: int = 0
    supporting_total: int = 0
    supporting_exact: int = 0
    # How many requests engaged 1, 2, or 3 agents. If this is overwhelmingly
    # 1, the multi-agent design is not earning its complexity, and that is
    # worth knowing before defending it.
    fan_out: dict[int, int] = field(default_factory=dict)

    @property
    def primary_accuracy(self) -> float:
        return self.primary_correct / self.primary_total if self.primary_total else 0.0

    @property
    def supporting_accuracy(self) -> float:
        return (
            self.supporting_exact / self.supporting_total if self.supporting_total else 0.0
        )

    @property
    def multi_agent_share(self) -> float:
        total = sum(self.fan_out.values())
        multi = sum(count for size, count in self.fan_out.items() if size > 1)
        return multi / total if total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary_total": self.primary_total,
            "primary_accuracy": round(self.primary_accuracy, 4),
            "supporting_total": self.supporting_total,
            "supporting_set_accuracy": round(self.supporting_accuracy, 4),
            "fan_out": {str(k): v for k, v in sorted(self.fan_out.items())},
            "multi_agent_share": round(self.multi_agent_share, 4),
        }


def score_routing(
    observations: list[tuple[str, str, frozenset[str], frozenset[str]]],
) -> RoutingReport:
    """Score ``(expected_primary, actual_primary, expected_set, actual_set)``.

    An empty ``expected_primary`` means the case asserts intent only and is
    skipped for primary accuracy -- but still counted in the fan-out
    distribution, which is a property of every request.
    """
    report = RoutingReport()
    for expected_primary, actual_primary, expected_set, actual_set in observations:
        engaged = len(actual_set) + (1 if actual_primary else 0)
        report.fan_out[engaged] = report.fan_out.get(engaged, 0) + 1

        if expected_primary:
            report.primary_total += 1
            report.primary_correct += expected_primary == actual_primary
        if expected_set:
            report.supporting_total += 1
            report.supporting_exact += expected_set == actual_set
    return report


def score_by_source(
    pairs_by_source: dict[str, list[tuple[str, str]]],
) -> dict[str, dict[str, Any]]:
    """Score each provenance group separately.

    The reason this exists is in corpus.py: model-written cases measure
    self-consistency. Reporting them mixed with human-written cases produces
    one number that means neither thing.
    """
    return {
        source: score_intents(pairs).as_dict() for source, pairs in pairs_by_source.items()
    }
