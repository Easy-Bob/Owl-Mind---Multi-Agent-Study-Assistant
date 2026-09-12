"""End-to-end evaluation: intent metrics, judged dialogue quality, regression.

STUB. Implements ISSUE-001 FR2. Behaviour lands with the evaluation issue;
see plan section 4 (Day 4) and section 11.2.

Three requirements the reference implementation got wrong, recorded here so
they are not rediscovered:

  - The baseline was overwritten on every run, so "regression" meant "worse
    than last run" rather than "worse than the pinned release". Pin it.
  - Judge failures were averaged in as scores, quietly depressing results.
    Exclude them and report the failure count separately.
  - At least five of the dialogue cases must be composite (primary plus
    supporting agents), asserting supporting_agents is non-empty. Without that
    assertion a five-prompt switch statement passes the whole suite.
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
    """Runs the corpus and compares against the pinned baseline."""

    async def run(self) -> dict[str, Any]:
        raise NotImplementedError(
            "EndToEndEvaluator.run is a scaffold stub (ISSUE-001). "
            "Implemented by the evaluation issue."
        )
