"""The evaluation run. ISSUE-009 FR3-FR5, FR8.

Collect once, score many times
------------------------------
The expensive pass records **what each signal said** and stops there. Every
number in the report is computed afterwards from that record, which buys three
things:

- the weight sweep is free after the first run, and repeatable
- scoring changes can be re-applied to an old run without paying again
- the metrics are testable without a key, because they are pure functions over
  a record a test can fabricate

A harness that fused as it collected would have to re-call the model for every
weighting it wanted to try, which is how weight tuning becomes a thing nobody
ever actually does.

This module is **not a test.** It costs money and is run deliberately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from owl_mind.core.intent_recognizer import (
    WEIGHTS,
    IntentCategory,
    IntentRecognizer,
    _pattern_signal,
    assemble_intent,
)
from owl_mind.evaluation import contamination, metrics
from owl_mind.evaluation.corpus import EvalCase, load, summarise

logger = logging.getLogger(__name__)

RUNS_DIR = Path(__file__).resolve().parents[2] / "data" / "eval" / "runs"
BASELINE_PATH = Path(__file__).resolve().parents[2] / "data" / "eval" / "baseline.json"

# A per-class F1 drop larger than this fails a --compare run.
#
# 0.05 is chosen against the corpus size, not from taste: at ~5 cases per class
# a single case flipping moves F1 by roughly 0.1-0.2, so a tighter margin would
# fire on noise every run and be disabled within a week. It should tighten as
# the corpus grows -- the margin is a function of n, and n is currently small.
REGRESSION_MARGIN = 0.05


@dataclass
class SignalRecord:
    """What the three signals said about one case. The unit of a run file."""

    message: str
    expected_intent: str
    source: str
    expected_primary_agent: str = ""
    expected_supporting_agents: list[str] = field(default_factory=list)
    llm: dict[str, float] = field(default_factory=dict)
    embedding: dict[str, float] = field(default_factory=dict)
    pattern: dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0
    # Recorded so FR8 can report what the timeout should have been set from.
    error: str = ""


def _as_str_map(scores: dict[IntentCategory, float]) -> dict[str, float]:
    return {intent.value: round(score, 6) for intent, score in scores.items()}


def _as_intent_map(scores: dict[str, float]) -> dict[IntentCategory, float]:
    return {IntentCategory(k): v for k, v in scores.items()}


async def collect(
    cases: list[EvalCase], recognizer: IntentRecognizer
) -> list[SignalRecord]:
    """Run every case through the three signals. **This costs money.**

    Cases run sequentially rather than gathered. The gateway's semaphore would
    happily run them in parallel, but a burst of 100 classification calls is a
    good way to meet a rate limit during what is supposed to be a measurement,
    and the wall clock of a one-off run is not worth optimising.
    """
    records: list[SignalRecord] = []
    for number, case in enumerate(cases, 1):
        started = time.perf_counter()
        error = ""
        llm: dict[IntentCategory, float] = {}
        embedding: dict[IntentCategory, float] = {}
        try:
            llm, embedding = await asyncio.gather(
                recognizer._llm_signal(case.message),
                recognizer._embedding_signal(case.message),
            )
        except Exception as exc:  # noqa: BLE001 -- one bad case must not end the run
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("case %d failed: %s", number, error)

        records.append(
            SignalRecord(
                message=case.message,
                expected_intent=case.expected_intent,
                source=case.source,
                expected_primary_agent=case.expected_primary_agent,
                expected_supporting_agents=list(case.expected_supporting_agents),
                llm=_as_str_map(llm),
                embedding=_as_str_map(embedding),
                pattern=_as_str_map(_pattern_signal(case.message)),
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                error=error,
            )
        )
        if number % 10 == 0:
            logger.info("collected %d/%d", number, len(cases))
    return records


def _route(intent: Any) -> tuple[str, frozenset[str]]:
    """Primary and supporting agents for an intent, without calling an agent.

    Routing is a pure function of Intent (ISSUE-005 FR2), so the whole routing
    metric costs nothing beyond the intent call already recorded. An OTHER
    intent never reaches routing -- the orchestrator answers it first -- which
    is reproduced here rather than inferred.
    """
    from owl_mind.agents.orchestrator import AgentOrchestrator

    if intent.category is IntentCategory.OTHER:
        return "", frozenset()

    # _route_decision reads no instance state (it is pure by FR2), so it is
    # called unbound rather than standing up an orchestrator -- which would
    # construct five agents and a composer per case for a pure function.
    decision = AgentOrchestrator._route_decision(None, intent)  # type: ignore[arg-type]
    return (
        decision.primary_agent.value,
        frozenset(agent.value for agent in decision.supporting_agents),
    )


def score(
    records: list[SignalRecord], *, weights: dict[str, float] | None = None
) -> dict[str, Any]:
    """Compute every metric from a recorded run. Pure; no network, no cost."""
    pairs: list[tuple[str, str]] = []
    by_source: dict[str, list[tuple[str, str]]] = {}
    routing: list[tuple[str, str, frozenset[str], frozenset[str]]] = []
    latencies: list[float] = []
    failures = 0

    for record in records:
        if record.error:
            # Excluded rather than scored as a miss. A provider outage is not a
            # classifier error, and averaging it in would make infrastructure
            # trouble look like a quality regression.
            failures += 1
            continue

        intent = assemble_intent(
            record.message,
            llm=_as_intent_map(record.llm),
            embedding=_as_intent_map(record.embedding),
            pattern=_as_intent_map(record.pattern),
            weights=weights,
        )
        predicted = intent.category.value
        pairs.append((record.expected_intent, predicted))
        by_source.setdefault(record.source, []).append((record.expected_intent, predicted))
        latencies.append(record.latency_ms)

        primary, supporting = _route(intent)
        routing.append(
            (
                record.expected_primary_agent,
                primary,
                frozenset(record.expected_supporting_agents),
                supporting,
            )
        )

    ordered = sorted(latencies)
    p95 = ordered[int(len(ordered) * 0.95)] if ordered else 0.0

    return {
        "weights": dict(weights or WEIGHTS),
        "scored": len(pairs),
        "signal_failures": failures,
        "intent": metrics.score_intents(pairs).as_dict(),
        "scope": metrics.score_scope(pairs).as_dict(),
        "routing": metrics.score_routing(routing).as_dict(),
        "by_source": metrics.score_by_source(by_source),
        "latency": {
            "mean_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            "p95_ms": round(p95, 2),
            "max_ms": round(max(latencies), 2) if latencies else 0.0,
            # FR8: the timeout default was a guess. This is the number it
            # should have been derived from -- note it covers the intent call
            # only, so a request-level budget needs the agent calls too.
            "note": "intent signal only; agent and composer calls are not in this figure",
        },
    }


def write_run(report: dict[str, Any], directory: Path | None = None) -> Path:
    """Write a timestamped run. Never touches the baseline."""
    target = directory or RUNS_DIR
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = target / f"run-{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def compare(report: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Per-class F1 regressions past the margin, worst first.

    Compares class by class rather than on the headline figure: a macro-F1 that
    holds steady while one class collapses and another improves is the exact
    shape of a regression worth catching.
    """
    now = report["intent"]["per_class"]
    then = baseline["intent"]["per_class"]
    drops: list[tuple[float, str]] = []
    for label, before in then.items():
        after = now.get(label)
        if after is None:
            drops.append((1.0, f"{label}: present in baseline, absent now"))
            continue
        delta = before["f1"] - after["f1"]
        if delta > REGRESSION_MARGIN:
            drops.append(
                (
                    delta,
                    f"{label}: F1 {before['f1']:.3f} -> {after['f1']:.3f} "
                    f"(-{delta:.3f}, n={after['support']})",
                )
            )
    drops.sort(reverse=True)
    return [message for _, message in drops]


async def run(*, semantic_check: bool = False) -> dict[str, Any]:
    """Full run: contamination, collection, scoring. Costs money."""
    from owl_mind.core.config import get_settings
    from owl_mind.core.llm_gateway import LLMGateway

    cases = load()
    summary = summarise(cases)
    contamination_report = contamination.report(cases, semantic=semantic_check)

    collisions = (
        contamination_report["lexical_collisions"]
        + contamination_report["semantic_collisions"]
    )
    if collisions:
        # Refused rather than warned. A contaminated corpus produces a number
        # that looks like a measurement and is not one, and a warning at the
        # top of a long report is a warning nobody reads.
        raise contamination_error(collisions)

    gateway = LLMGateway(get_settings())
    try:
        recognizer = IntentRecognizer(gateway, index=await _index_or_none())
        records = await collect(cases, recognizer)
    finally:
        await gateway.aclose()

    report = score(records)
    report["corpus"] = summary.as_dict()
    report["contamination"] = contamination_report
    report["records"] = [asdict(record) for record in records]
    report["generated_at"] = datetime.now(UTC).isoformat()
    return report


def contamination_error(collisions: list[str]) -> Exception:
    joined = "\n  ".join(collisions)
    return RuntimeError(
        f"corpus is contaminated by {len(collisions)} template collision(s); "
        f"a score computed from it measures memorisation:\n  {joined}"
    )


async def _index_or_none() -> Any:
    from owl_mind.api.main import _build_template_index
    from owl_mind.core.config import get_settings

    return await _build_template_index(get_settings())
