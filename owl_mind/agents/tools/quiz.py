"""generate_quiz_spec and grade_answer. Implements ISSUE-007 FR5.

Plan section 3.1.1 is explicit that grading here is **stabilised, not
deterministic**, and this module is written to keep that claim honest:

  - the rubric is authored once, at question-generation time, and pinned. It is
    never regenerated at grading time, because two gradings of the same answer
    that disagree destroy the only fairness property self-testing has.
  - the one remaining judgement, "is rubric point N present in this answer",
    is decomposed into binary checks rather than a holistic score.
  - the weights are summed **here, in code**. The model never returns a total.

What is left non-deterministic is the binary checks themselves: they are model
judgements and can differ between runs. That residual is real and is reported
in the result rather than hidden, so a caller cannot mistake this for
arithmetic.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from owl_mind.agents.tools import AgentToolSpec, register

# A quiz "spec" is the structure, not the questions. Pinning the shape is what
# makes two quizzes on the same topic comparable -- without it, "quiz me on
# hash tables" twice yields two quizzes that cannot be scored against each
# other, and progress tracking measures the quiz rather than the student.
_MIX: dict[str, tuple[float, float, float]] = {
    # recall, application, analysis
    "easy": (0.6, 0.3, 0.1),
    "mixed": (0.3, 0.4, 0.3),
    "hard": (0.1, 0.4, 0.5),
}
_BANDS = ("recall", "application", "analysis")
MAX_QUESTIONS = 20


def allocate(count: int, difficulty: str) -> dict[str, int]:
    """Split ``count`` questions across the bands. Sums to count exactly.

    Largest-remainder, with ties broken by band order, so the same request
    always produces the same distribution -- floor-and-hope leaves a remainder
    whose placement would otherwise depend on dict ordering.
    """
    weights = _MIX.get(difficulty, _MIX["mixed"])
    exact = [count * weight for weight in weights]
    allocated = [int(value) for value in exact]
    remainder = count - sum(allocated)
    order = sorted(
        range(len(_BANDS)), key=lambda i: (-(exact[i] - allocated[i]), i)
    )
    for index in order[:remainder]:
        allocated[index] += 1
    return dict(zip(_BANDS, allocated, strict=True))


def _spec(request: Any, args: dict[str, Any]) -> dict[str, Any]:
    count = max(1, min(MAX_QUESTIONS, int(args.get("count", 5))))
    difficulty = str(args.get("difficulty", "mixed")).lower()
    if difficulty not in _MIX:
        difficulty = "mixed"
    return {
        "topic": str(args["topic"]),
        "count": count,
        "difficulty": difficulty,
        "distribution": allocate(count, difficulty),
        "instructions": (
            "Write one question per slot in the distribution. For each question also "
            "write its rubric: two to four independently checkable points, each with a "
            "weight. The rubric is pinned at this point and must not be rewritten when "
            "the answer is graded."
        ),
    }


register(
    AgentToolSpec(
        name="generate_quiz_spec",
        description=(
            "Get the structure of a quiz: how many questions, and how they split "
            "across recall, application, and analysis. Call this before writing any "
            "questions. It returns the shape only -- you write the questions and "
            "their rubrics from it."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "What the quiz covers."},
                "count": {"type": "integer", "description": "How many questions, 1 to 20."},
                "difficulty": {
                    "type": "string",
                    "description": "easy, mixed, or hard.",
                },
            },
            "required": ["topic"],
            "additionalProperties": False,
        },
        handler=_spec,
    )
)


# -- grading ----------------------------------------------------------------


def fingerprint(rubric: list[dict[str, Any]]) -> str:
    """Stable hash of a rubric, so a caller can prove it was not regenerated.

    Recorded at generation time and compared at grading time. If the two differ,
    the rubric was rewritten between the question and the mark, and the grade is
    not comparable to any other grade for that question.
    """
    canonical = json.dumps(
        [
            {"id": str(point["id"]), "weight": float(point["weight"])}
            for point in sorted(rubric, key=lambda point: str(point["id"]))
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def grade(rubric: list[dict[str, Any]], checks: dict[str, bool]) -> dict[str, Any]:
    """Sum the weights of the rubric points that were met. Arithmetic only."""
    met: list[str] = []
    missed: list[str] = []
    unchecked: list[str] = []
    score = 0.0
    total = 0.0

    for point in rubric:
        point_id = str(point["id"])
        weight = float(point["weight"])
        total += weight
        if point_id not in checks:
            # Reported rather than silently counted as a miss: a rubric point
            # nobody judged is a broken grading run, not a wrong answer.
            unchecked.append(point_id)
            missed.append(point_id)
        elif checks[point_id]:
            score += weight
            met.append(point_id)
        else:
            missed.append(point_id)

    return {
        "score": round(score, 4),
        "max_score": round(total, 4),
        "percentage": 0.0 if total == 0 else round(100 * score / total, 2),
        "met": met,
        "missed": missed,
        "unchecked": unchecked,
        "rubric_fingerprint": fingerprint(rubric),
        "determinism": (
            "The arithmetic is exact: the same checks against the same rubric always "
            "give the same mark. The checks themselves are model judgements and can "
            "differ between runs."
        ),
    }


register(
    AgentToolSpec(
        name="grade_answer",
        description=(
            "Turn per-rubric-point judgements into a mark. Judge each rubric point "
            "separately as met or not met, pass those judgements here, and report the "
            "score this returns. Never total the weights yourself, and never rewrite "
            "the rubric that was pinned when the question was written."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "rubric": {
                    "type": "array",
                    "description": "The pinned rubric: objects with id, weight, description.",
                },
                "checks": {
                    "type": "object",
                    "description": "One boolean per rubric point id: was it present?",
                },
            },
            "required": ["rubric", "checks"],
            "additionalProperties": False,
        },
        handler=lambda request, args: grade(list(args["rubric"]), dict(args["checks"])),
    )
)
