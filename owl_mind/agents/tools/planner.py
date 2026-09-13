"""schedule_review -- SM-2 spaced repetition. Implements ISSUE-007 FR4.

Plan section 3.1: "Scheduling is exact because schedule_review is SM-2
arithmetic -- pure code, one correct answer per input."

This is the tool that makes that sentence true rather than aspirational. The
model never computes a date; it calls this and phrases the result.

``now`` is an argument and is never read inside. The property under test is
"the same progress always yields the same date", and a hidden clock makes that
untestable -- the test would pass on the day it was written and drift after.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from owl_mind.agents.tools import AgentToolSpec, register

# SM-2's floor. Below roughly 1.3 the interval growth collapses and the item is
# shown so often that the algorithm stops being spaced repetition.
MIN_EASE = 1.3
DEFAULT_EASE = 2.5


def sm2(
    *, quality: int, ease: float, interval: int, repetitions: int, today: date
) -> dict[str, Any]:
    """One SM-2 step. Pure arithmetic over its arguments.

    Args:
        quality: recall quality, 0 (blank) to 5 (perfect).
        ease: current ease factor.
        interval: current interval in days.
        repetitions: consecutive successful reviews so far.
        today: the date the review happened.

    Returns:
        The updated schedule. Same input, same output, always.
    """
    if quality < 3:
        # A lapse resets the ladder. The item is not "slightly harder"; it was
        # not recalled, and the next interval has to start over.
        repetitions = 0
        next_interval = 1
    else:
        repetitions += 1
        if repetitions == 1:
            next_interval = 1
        elif repetitions == 2:
            next_interval = 6
        else:
            next_interval = round(interval * ease)

    # The ease factor updates on every review including lapses -- that is
    # canonical SM-2, and the variant that only updates on success lets a
    # repeatedly-failed item keep an ease it has not earned.
    delta = 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
    next_ease = max(MIN_EASE, ease + delta)

    return {
        "interval": next_interval,
        # Rounded so two runs cannot differ in the sixteenth decimal place and
        # fail an equality assertion that is morally true.
        "ease": round(next_ease, 4),
        "repetitions": repetitions,
        "due_date": (today + timedelta(days=next_interval)).isoformat(),
        "lapsed": quality < 3,
    }


def _handler(request: Any, args: dict[str, Any]) -> dict[str, Any]:
    return sm2(
        quality=int(args["quality"]),
        ease=float(args.get("ease", DEFAULT_EASE)),
        interval=int(args.get("interval", 0)),
        repetitions=int(args.get("repetitions", 0)),
        today=date.fromisoformat(args["today"]),
    )


register(
    AgentToolSpec(
        name="schedule_review",
        description=(
            "Compute the next spaced-repetition review date with SM-2. Call this "
            "for every scheduling decision; never work out an interval or a date "
            "yourself. Takes the student's recall quality (0-5) and their current "
            "ease, interval, and repetition count, plus today's date."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "quality": {
                    "type": "integer",
                    "description": "Recall quality: 0 blank, 3 correct with effort, 5 perfect.",
                },
                "ease": {"type": "number", "description": "Current ease factor, default 2.5."},
                "interval": {"type": "integer", "description": "Current interval in days."},
                "repetitions": {
                    "type": "integer",
                    "description": "Consecutive successful reviews so far.",
                },
                "today": {"type": "string", "description": "Review date, YYYY-MM-DD."},
            },
            "required": ["quality", "today"],
            "additionalProperties": False,
        },
        handler=_handler,
    )
)
