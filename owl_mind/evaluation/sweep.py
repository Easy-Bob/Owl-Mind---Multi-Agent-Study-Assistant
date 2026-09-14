"""Weight sweep and the F13 question. ISSUE-009 FR5.

Everything here re-scores a run that already happened. It costs nothing, runs
in milliseconds, and can be re-run against an old run file whenever the
question changes -- which is the entire reason ``harness.collect`` records raw
signals instead of fused scores.

Two questions are answered:

1. **What should WEIGHTS be?** A grid over the three weights, scored on the
   train split, reported by macro-F1 and by the classes that move most.
2. **Should corroboration gate the primary?** ISSUE-006 F13. Supporting agents
   already require that the model or a pattern rule returned the intent;
   the primary does not, which is how a lone regex took the lead answer on
   "explain BFS then quiz me".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from owl_mind.core.intent_recognizer import (
    PRIMARY_THRESHOLD,
    IntentCategory,
    _fuse,
)
from owl_mind.evaluation.harness import SignalRecord, _as_intent_map, score

# Sampled rather than exhaustive: the three weights are constrained to sum to
# 1.0, so the space is two-dimensional, and a finer grid would report
# differences far below what ~5 cases per class can resolve.
GRID: tuple[dict[str, float], ...] = (
    {"llm": 0.7, "embedding": 0.2, "pattern": 0.1},  # the ISSUE-003 original
    {"llm": 0.6, "embedding": 0.25, "pattern": 0.15},
    {"llm": 0.5, "embedding": 0.35, "pattern": 0.15},  # current
    {"llm": 0.5, "embedding": 0.4, "pattern": 0.1},
    {"llm": 0.4, "embedding": 0.4, "pattern": 0.2},
    {"llm": 0.8, "embedding": 0.15, "pattern": 0.05},
    {"llm": 1.0, "embedding": 0.0, "pattern": 0.0},  # model alone, the control
)


def load_records(path: Path) -> list[SignalRecord]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [SignalRecord(**record) for record in payload["records"]]


def sweep(records: list[SignalRecord]) -> list[dict[str, Any]]:
    """Score every weighting in the grid. Free."""
    results = []
    for weights in GRID:
        report = score(records, weights=weights)
        results.append(
            {
                "weights": weights,
                "macro_f1": report["intent"]["macro_f1"],
                "accuracy": report["intent"]["accuracy"],
                "oos_recall": report["scope"]["oos_recall"],
                "false_decline_rate": report["scope"]["false_decline_rate"],
                "primary_accuracy": report["routing"]["primary_accuracy"],
                "multi_agent_share": report["routing"]["multi_agent_share"],
            }
        )
    results.sort(key=lambda row: -row["macro_f1"])
    return results


@dataclass(frozen=True)
class F13Result:
    """Primary accuracy with and without a corroboration gate on the primary."""

    ungated_accuracy: float
    gated_accuracy: float
    changed: int
    examples: tuple[str, ...]

    @property
    def verdict(self) -> str:
        delta = self.gated_accuracy - self.ungated_accuracy
        if self.changed == 0:
            return "no effect on this corpus -- no case has an uncorroborated primary"
        if delta > 0.01:
            return f"gate helps (+{delta:.3f}); adopt it"
        if delta < -0.01:
            return f"gate hurts ({delta:.3f}); leave the primary ungated"
        return f"no measurable difference over {self.changed} affected case(s)"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ungated_primary_accuracy": round(self.ungated_accuracy, 4),
            "gated_primary_accuracy": round(self.gated_accuracy, 4),
            "cases_changed": self.changed,
            "examples": list(self.examples),
            "verdict": self.verdict,
        }


def answer_f13(records: list[SignalRecord]) -> F13Result:
    """Would requiring corroboration for the primary improve routing?

    The gate: an intent may only *lead* if the model or a pattern rule returned
    it -- the same rule ``supporting_candidates`` already applies. Under it, an
    intent carried purely by embedding similarity can support but not lead.

    Note this is not the F13 case exactly: there, the winner (quiz_request) was
    corroborated *by the pattern rule itself*, so this particular gate would not
    have changed that outcome. Measured anyway, because the sweep needs to know
    whether the gate is worth having on its own terms before anyone reaches for
    a more aggressive one that down-weights pattern for the primary slot.
    """
    ungated = 0
    gated = 0
    total = 0
    changed = 0
    examples: list[str] = []

    for record in records:
        if record.error or not record.expected_primary_agent:
            continue
        total += 1

        llm = _as_intent_map(record.llm)
        pattern = _as_intent_map(record.pattern)
        embedding = _as_intent_map(record.embedding)
        fused = _fuse({"llm": llm, "embedding": embedding, "pattern": pattern})
        if not fused:
            continue

        corroborated = frozenset(llm) | frozenset(pattern)
        order = sorted(fused.items(), key=lambda pair: (-pair[1], pair[0].value))

        top = order[0][0]
        gated_top = next(
            (intent for intent, s in order if intent in corroborated and s >= PRIMARY_THRESHOLD),
            top,
        )
        ungated += _agent_for(top) == record.expected_primary_agent
        gated += _agent_for(gated_top) == record.expected_primary_agent
        if top is not gated_top:
            changed += 1
            if len(examples) < 5:
                examples.append(
                    f"{record.message!r}: {top.value} -> {gated_top.value} "
                    f"(expected {record.expected_primary_agent})"
                )

    return F13Result(
        ungated_accuracy=ungated / total if total else 0.0,
        gated_accuracy=gated / total if total else 0.0,
        changed=changed,
        examples=tuple(examples),
    )


def _agent_for(intent: IntentCategory) -> str:
    """Group-to-agent mapping, without constructing an orchestrator."""
    from owl_mind.agents.orchestrator import _GROUP_AGENTS
    from owl_mind.core.intent_recognizer import _INTENT_GROUPS

    if intent is IntentCategory.OTHER:
        return ""
    if intent is IntentCategory.HUMAN_TUTOR:
        return "tutor_handoff"
    return _GROUP_AGENTS[_INTENT_GROUPS[intent]].value


def report(records: list[SignalRecord]) -> dict[str, Any]:
    return {
        "grid": sweep(records),
        "f13": answer_f13(records).as_dict(),
        "note": (
            "Scored on the full run. Tune on train, report on test -- see "
            "corpus.split. With ~5 cases per class these differences are "
            "directional, not decisive."
        ),
    }


def format_grid(rows: list[dict[str, Any]]) -> str:
    lines = [
        f"{'llm':>5} {'emb':>5} {'pat':>5}  {'macroF1':>8} {'acc':>6} "
        f"{'oosRec':>7} {'falseDec':>9} {'primary':>8} {'multi':>6}"
    ]
    for row in rows:
        w = row["weights"]
        lines.append(
            f"{w['llm']:>5.2f} {w['embedding']:>5.2f} {w['pattern']:>5.2f}  "
            f"{row['macro_f1']:>8.4f} {row['accuracy']:>6.3f} "
            f"{row['oos_recall']:>7.3f} {row['false_decline_rate']:>9.3f} "
            f"{row['primary_accuracy']:>8.3f} {row['multi_agent_share']:>6.3f}"
        )
    return "\n".join(lines)
