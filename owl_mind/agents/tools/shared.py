"""Tools available to every role, and the handoff summary. ISSUE-007 FR2/FR6.

``create_handoff_summary`` is **not registered**. TutorHandoffAgent makes no
model call, so it has no tool loop and nothing to offer a tool to. Registering
it would put an entry in the registry that no request payload can ever contain,
and would invite someone to give that agent a ``tool_scope`` -- which is one
edit away from giving it a gateway call, and the whole point of that role is
that it keeps working when the gateway does not.

So it is an ordinary function, called directly from ``handle``.
"""

from __future__ import annotations

from typing import Any

from owl_mind.agents.tools import AgentToolSpec, register


def _context(request: Any, args: dict[str, Any]) -> dict[str, Any]:
    """What this agent can see about the current turn.

    Identifiers are reported as present or absent rather than by value. The
    result goes back into the model's context and from there into logs and
    traces, and a session id is not something an explanation needs.
    """
    intent = getattr(request, "intent", None)
    context: dict[str, Any] = {
        "message": getattr(request, "message", ""),
        "has_intent": intent is not None,
        "has_history": bool(getattr(request, "history", None)),
        "session_present": bool(getattr(request, "session_id", "")),
        "user_present": bool(getattr(request, "user_id", "")),
    }
    if intent is not None:
        context["intent"] = str(getattr(getattr(intent, "category", ""), "value", ""))
        context["confidence"] = getattr(intent, "confidence", 0.0)
        context["urgency"] = str(getattr(getattr(intent, "urgency", ""), "value", ""))
        context["entities"] = dict(getattr(intent, "entities", {}) or {})
    return context


register(
    AgentToolSpec(
        name="inspect_request_context",
        description=(
            "See what is known about the current turn: the message, the recognised "
            "intent and its confidence, any extracted entities such as topic or due "
            "date, and whether conversation history exists. Use it when the message "
            "alone is ambiguous, before asking the student to repeat themselves."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_context,
    )
)


# -- not a tool -------------------------------------------------------------


def create_handoff_summary(request: Any) -> dict[str, Any]:
    """Structured handoff for a human teaching assistant.

    Deterministic and model-free, so it works during an outage -- which is the
    only condition under which anyone needs it urgently.

    The shape is for the human, not the student: what was asked, what the system
    understood, and what it had already tried, so the TA does not start by asking
    questions the transcript already answers.
    """
    intent = getattr(request, "intent", None)
    entities = dict(getattr(intent, "entities", {}) or {}) if intent is not None else {}

    return {
        "question": getattr(request, "message", ""),
        "topic": entities.get("topic", ""),
        "problem_id": entities.get("problem_id", ""),
        "due_date": entities.get("due_date", ""),
        "recognised_intent": (
            str(getattr(getattr(intent, "category", ""), "value", ""))
            if intent is not None
            else ""
        ),
        "urgency": (
            str(getattr(getattr(intent, "urgency", ""), "value", ""))
            if intent is not None
            else ""
        ),
        "session_id": getattr(request, "session_id", ""),
        "assistant_attempted": False,
    }


def render_handoff(summary: dict[str, Any]) -> str:
    """The handoff as text for the student, confirming what was passed on."""
    lines = ["Handing this to a human teaching assistant."]
    for label, key in (
        ("Topic", "topic"),
        ("Problem", "problem_id"),
        ("Due", "due_date"),
        ("Urgency", "urgency"),
    ):
        if summary.get(key):
            lines.append(f"{label}: {summary[key]}")
    lines.append(f"What the student asked: {summary.get('question', '')}")
    return "\n".join(lines)
