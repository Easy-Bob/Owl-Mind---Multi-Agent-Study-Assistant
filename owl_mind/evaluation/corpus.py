"""The labelled corpus: schema, loading, and the held-out split.

Implements ISSUE-009 FR1. Plan reference: section 4 (Day 4), section 11.2.

Provenance is a field, not a footnote
-------------------------------------
Every case records who wrote it. This is not bookkeeping -- it is the single
most important caveat on any number this harness produces.

The seed cases were written by a Claude model. The classifier under test is a
Claude model. A corpus written by the system's own family measures
**self-consistency, not correctness**: it will ace phrasings its own family
finds natural and will systematically under-sample the ways real students
actually write, which is the population that matters.

So ``source`` is carried per case and every report breaks metrics down by it.
``human`` cases are the trustworthy ones. Until that subset is large enough to
stand on its own, the headline number is a smoke test, not evidence.

The split
---------
``train`` tunes, ``test`` reports. Seeded and deterministic, so the same case
lands in the same split on every machine and across runs -- a split that
wanders makes every comparison against the baseline meaningless.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

CORPUS_PATH = Path(__file__).resolve().parents[2] / "data" / "eval" / "corpus.jsonl"

# Fraction of cases held out for reporting. Fixed, and changing it invalidates
# every prior baseline -- the comparison would be against a different test set.
TEST_FRACTION = 0.4

# Salt for the split hash. Changing it reshuffles the split, which has the same
# effect as changing TEST_FRACTION: past baselines stop being comparable.
SPLIT_SALT = "owl-mind-eval-v1"

Source = Literal["seed", "human"]
Split = Literal["train", "test"]


class CorpusError(ValueError):
    """A corpus file that cannot be trusted to measure anything."""


@dataclass(frozen=True)
class EvalCase:
    """One labelled utterance."""

    message: str
    expected_intent: str
    # Written by a model ("seed") or by a person ("human"). See module docstring:
    # metrics are reported separately for each, because they do not mean the
    # same thing.
    source: Source = "seed"
    # Routing expectations. Only set where routing is the point of the case;
    # an empty primary means "this case only asserts intent".
    expected_primary_agent: str = ""
    expected_supporting_agents: tuple[str, ...] = ()
    # Free-text note on why the case is interesting -- usually the boundary it
    # probes. Read by a human staring at a confusion matrix.
    note: str = ""

    @property
    def is_composite(self) -> bool:
        return bool(self.expected_supporting_agents)

    @property
    def is_out_of_scope(self) -> bool:
        return self.expected_intent == "other"

    @property
    def split(self) -> Split:
        """Deterministic split on a hash of the message.

        Hashing the *message* rather than shuffling a list means a case keeps
        its split when cases are added, removed, or reordered. Appending to the
        corpus therefore does not silently move existing cases across the
        train/test line and invalidate the baseline.
        """
        digest = hashlib.sha256(f"{SPLIT_SALT}:{normalise(self.message)}".encode()).digest()
        bucket = int.from_bytes(digest[:8], "big") / 2**64
        return "test" if bucket < TEST_FRACTION else "train"


def normalise(message: str) -> str:
    """Casefold, collapse whitespace, drop terminal punctuation.

    Used for the split hash and for contamination checks, so it must stay
    conservative: aggressive normalisation (stemming, stopword removal) would
    collapse cases that are genuinely different and silently shrink the corpus.
    """
    text = re.sub(r"\s+", " ", message.strip().casefold())
    return text.rstrip(" .!?")


def load(path: Path | None = None) -> list[EvalCase]:
    """Read and validate the corpus.

    Raises:
        CorpusError: on a duplicate message or an unknown intent. Both are
            silent corruption otherwise -- a duplicate double-weights one case
            in the mean, and an unknown label scores as a permanent miss.
    """
    # Imported here rather than at module scope so the corpus module stays
    # importable by tooling that has no interest in the taxonomy.
    from owl_mind.core.intent_recognizer import IntentCategory

    source_path = path or CORPUS_PATH
    if not source_path.exists():
        raise CorpusError(f"no corpus at {source_path}")

    known = {intent.value for intent in IntentCategory}
    cases: list[EvalCase] = []
    seen: dict[str, int] = {}

    for number, line in enumerate(source_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        try:
            raw: dict[str, Any] = json.loads(line)
        except ValueError as exc:
            raise CorpusError(f"{source_path}:{number}: not valid JSON -- {exc}") from exc

        message = str(raw.get("message", "")).strip()
        intent = str(raw.get("expected_intent", ""))
        if not message:
            raise CorpusError(f"{source_path}:{number}: empty message")
        if intent not in known:
            raise CorpusError(
                f"{source_path}:{number}: unknown intent {intent!r}. "
                f"A label outside the taxonomy scores as a permanent miss."
            )

        key = normalise(message)
        if key in seen:
            raise CorpusError(
                f"{source_path}:{number}: duplicates line {seen[key]} -- "
                f"a repeated case is double-weighted in every mean."
            )
        seen[key] = number

        cases.append(
            EvalCase(
                message=message,
                expected_intent=intent,
                source=raw.get("source", "seed"),
                expected_primary_agent=raw.get("expected_primary_agent", ""),
                expected_supporting_agents=tuple(raw.get("expected_supporting_agents", [])),
                note=raw.get("note", ""),
            )
        )

    if not cases:
        raise CorpusError(f"{source_path} contains no cases")
    return cases


def split(cases: list[EvalCase], which: Split) -> list[EvalCase]:
    return [case for case in cases if case.split == which]


@dataclass
class CorpusSummary:
    """What the corpus contains, for the report header."""

    total: int = 0
    by_intent: dict[str, int] = field(default_factory=dict)
    by_source: dict[str, int] = field(default_factory=dict)
    by_split: dict[str, int] = field(default_factory=dict)
    composite: int = 0
    out_of_scope: int = 0
    thin_classes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_intent": self.by_intent,
            "by_source": self.by_source,
            "by_split": self.by_split,
            "composite": self.composite,
            "out_of_scope": self.out_of_scope,
            "thin_classes": self.thin_classes,
        }


# Below this many cases, a per-class rate is not a measurement. Reported so a
# reader sees which classes to distrust rather than having to count.
THIN_CLASS_THRESHOLD = 8


def summarise(cases: list[EvalCase]) -> CorpusSummary:
    summary = CorpusSummary(total=len(cases))
    for case in cases:
        summary.by_intent[case.expected_intent] = (
            summary.by_intent.get(case.expected_intent, 0) + 1
        )
        summary.by_source[case.source] = summary.by_source.get(case.source, 0) + 1
        summary.by_split[case.split] = summary.by_split.get(case.split, 0) + 1
        summary.composite += case.is_composite
        summary.out_of_scope += case.is_out_of_scope

    from owl_mind.core.intent_recognizer import IntentCategory

    summary.thin_classes = sorted(
        intent.value
        for intent in IntentCategory
        if summary.by_intent.get(intent.value, 0) < THIN_CLASS_THRESHOLD
    )
    return summary


def iter_messages(cases: list[EvalCase]) -> Iterator[str]:
    for case in cases:
        yield case.message
