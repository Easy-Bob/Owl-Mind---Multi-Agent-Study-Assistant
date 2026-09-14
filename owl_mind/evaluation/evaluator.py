"""End-to-end evaluation: intent metrics, judged dialogue quality, regression.

STUB. Implements ISSUE-001 FR2. Behaviour lands with the evaluation issue;
see plan section 4 (Day 4) and section 11.2.

Three requirements the reference implementation got wrong, recorded here so
they are not rediscovered:

  - The baseline was overwritten on every run, so "regression" meant "worse
    than last run" rather than "worse than the pinned release". Pin it.
    DONE (ISSUE-009 FR7): runs are timestamped and never touch the baseline;
    promoting one is an explicit `promote` subcommand.
  - Judge failures were averaged in as scores, quietly depressing results.
    Exclude them and report the failure count separately.
    DONE (ISSUE-009): harness.score excludes any record carrying an error and
    reports `signal_failures` alongside. The judge itself is still deferred.
  - At least five of the dialogue cases must be composite (primary plus
    supporting agents), asserting supporting_agents is non-empty. Without that
    assertion a five-prompt switch statement passes the whole suite.
    DONE (ISSUE-009 FR4): the corpus carries 10 composite cases and a test
    asserts the count; routing reports the fan-out distribution, so a system
    that answered everything with one agent is visible rather than inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IntentTestCase:
    """One labelled utterance."""

    message: str
    expected_intent: str


@dataclass(frozen=True)
class DialogTestCase:
    """One dialogue turn, optionally asserting the routing shape."""

    message: str
    expected_primary_agent: str
    expected_supporting_agents: list[str]


class EndToEndEvaluator:
    """Runs the corpus and compares against the pinned baseline.

    Thin wrapper kept for the name the scaffold introduced. The work lives in
    ``harness`` and ``sweep``, which are split so that collection (expensive,
    once) and scoring (free, repeatable) are separate operations.
    """

    async def run(self) -> dict[str, Any]:
        from owl_mind.evaluation.harness import run as _run

        return await _run()
