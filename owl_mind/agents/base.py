"""Agent base types: AgentType, AgentProfile, BaseAgent.

STUB. Implements ISSUE-001 FR2. Behaviour lands with the agent issue;
see plan section 3.1.

The roster is declared here because the profile fields *are* the argument for
multi-agent: the agents differ in what they may do (tool_scope), how
deterministic they must be (temperature), and what they are forbidden to emit
(risk_boundary) -- not merely in what they talk about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AgentType(StrEnum):
    """The five roles from plan section 3.1."""

    CONCEPT = "concept"
    PRACTICE = "practice"
    PLANNER = "planner"
    QUIZ = "quiz"
    TUTOR_HANDOFF = "tutor_handoff"


@dataclass(frozen=True)
class AgentProfile:
    """Static contract for one role.

    Attributes:
        role: system-prompt role statement.
        tool_scope: whitelist. Enforced before dispatch, so a tool outside this
            tuple is not merely discouraged -- it is absent from the request.
        temperature: 0.0 where output must be reproducible (planner, quiz).
        max_tokens: per-role ceiling; the monolith alternative provisions every
            call for the worst case.
        risk_boundary: the one thing this role must never emit. Asserted
            deterministically in evaluation, not just stated in the prompt.
        handoff_conditions: when this role should route to a human.
    """

    role: str
    tool_scope: tuple[str, ...] = ()
    temperature: float = 0.3
    max_tokens: int = 1000
    risk_boundary: str = ""
    handoff_conditions: tuple[str, ...] = ()


@dataclass
class AgentStats:
    """Rolling per-instance statistics. Feeds routing_score and the monitor."""

    total: int = 0
    success: int = 0
    total_ms: float = 0.0
    monitor_penalty: float = 0.0


@dataclass
class AgentResponse:
    """One agent's answer to one request."""

    agent_type: AgentType
    content: str
    success: bool = True
    escalate: bool = False
    tools_used: tuple[str, ...] = ()
    tool_traces: tuple[dict[str, Any], ...] = field(default_factory=tuple)


class BaseAgent:
    """Common LLM call, tool loop, whitelist enforcement, and statistics."""

    agent_type: AgentType
    profile: AgentProfile

    async def handle(self, request: Any) -> AgentResponse:
        """Answer one request.

        Raises:
            NotImplementedError: implemented by the agent issue.
        """
        raise NotImplementedError(
            "BaseAgent.handle is a scaffold stub (ISSUE-001). "
            "Implemented by the agent issue; see plan section 3.1."
        )
