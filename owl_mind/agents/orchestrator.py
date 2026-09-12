"""AgentOrchestrator: routing decision, parallel dispatch, degradation.

STUB. Implements ISSUE-001 FR2. Behaviour lands with the routing issue;
see plan sections 3.1 and 11.2.

Two selection layers, which are easy to conflate:

  1. Role selection -- ``_route_decision`` scores agent *types* and returns a
     primary plus any supporting roles. Composite requests ("explain BFS then
     quiz me") run their roles concurrently.
  2. Instance selection -- within one role, the pool member with the best
     routing_score handles the request. Always one instance per role.

Plan section 11.2 warns that layer 1 dies silently if the taxonomy never
produces composite requests: the orchestrator degrades into a switch statement
over five prompts and no test fails. The eval corpus guards this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from owl_mind.agents.base import AgentType, BaseAgent


@dataclass
class RoutingDecision:
    """Structured routing outcome. ``reason`` is surfaced for debuggability."""

    primary_agent: AgentType
    supporting_agents: list[AgentType] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0

    @property
    def agent_types(self) -> list[AgentType]:
        return [self.primary_agent] + self.supporting_agents

    @property
    def multi_agent(self) -> bool:
        return bool(self.supporting_agents)


@dataclass
class OrchestratorResult:
    """What the API returns for one chat turn."""

    request_id: str
    response: str
    primary_agent: AgentType
    supporting_agents: list[AgentType] = field(default_factory=list)
    escalated: bool = False
    latency_ms: float = 0.0
    routing_reason: str = ""
    tools_used: list[str] = field(default_factory=list)
    tool_traces: list[dict[str, Any]] = field(default_factory=list)


class AgentOrchestrator:
    """Owns the agent pool and the routing decision."""

    def __init__(self) -> None:
        # List per type from the start: same role, different model or different
        # online performance, is the extension this structure reserves room for.
        self._pool: dict[AgentType, list[BaseAgent]] = {}

    def registered_agents(self) -> list[str]:
        """Agent type names with at least one live instance."""
        return [agent_type.value for agent_type, pool in self._pool.items() if pool]

    def _route_decision(self, request: Any) -> RoutingDecision:
        raise NotImplementedError(
            "AgentOrchestrator._route_decision is a scaffold stub (ISSUE-001). "
            "Implemented by the routing issue; see plan section 3.2."
        )

    def _best_agent(self, agent_type: AgentType) -> BaseAgent | None:
        raise NotImplementedError(
            "AgentOrchestrator._best_agent is a scaffold stub (ISSUE-001). "
            "Implemented by the routing issue."
        )

    async def run(self, request: Any) -> OrchestratorResult:
        raise NotImplementedError(
            "AgentOrchestrator.run is a scaffold stub (ISSUE-001). "
            "Implemented by the routing issue; see plan section 3.1."
        )
