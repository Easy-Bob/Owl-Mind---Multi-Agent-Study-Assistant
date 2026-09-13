"""AgentOrchestrator: routing decision, parallel dispatch, degradation.

Implements ISSUE-005 FR2, FR2a, FR3, FR6. Plan sections 3.1 and 11.2.

Two selection layers, which are easy to conflate:

  1. Role selection -- ``_route_decision`` scores agent *types* and returns a
     primary plus any supporting roles. Composite requests ("explain BFS then
     quiz me") run their roles concurrently.
  2. Instance selection -- within one role, the pool member with the best
     routing_score handles the request. Deferred: one instance per role here.

Plan section 11.2 warns that layer 1 dies silently if the taxonomy never
produces composite requests: the orchestrator degrades into a switch statement
over five prompts and no test fails. The eval corpus guards this.

Three requests never reach an agent at all, and none of them costs a model
call: a human-tutor ask, an ambiguous message, and an off-topic one. See
``run``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from owl_mind.agents.base import AgentRequest, AgentResponse, AgentType, BaseAgent
from owl_mind.agents.composer import ResponseComposer
from owl_mind.agents.roster import AGENT_CLASSES
from owl_mind.core.intent_recognizer import (
    MAX_AGENTS,
    Intent,
    IntentCategory,
    IntentGroup,
)
from owl_mind.core.llm_gateway import LLMGateway

logger = logging.getLogger(__name__)


# Groups, not individual intents. Sixteen intents map onto four specialists;
# routing on the group is what keeps the table small enough to read.
_GROUP_AGENTS: dict[IntentGroup, AgentType] = {
    IntentGroup.LEARN: AgentType.CONCEPT,
    IntentGroup.PRACTICE: AgentType.PRACTICE,
    IntentGroup.PLAN: AgentType.PLANNER,
    IntentGroup.ASSESS: AgentType.QUIZ,
    IntentGroup.SUPPORT: AgentType.CONCEPT,
}


class PrimaryAgentFailed(RuntimeError):
    """The agent chosen to own the response did not produce one.

    FR6: this surfaces rather than being answered by a role that was not
    chosen. Owl Mind has no general agent to fall back to, and silently
    swapping in a specialist whose boundaries differ -- handing a
    problem-help request to ConceptAgent, which may state the answer -- would
    break the guarantee the roster exists to make.
    """


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
    # None when no agent handled the turn. An OTHER intent is answered before
    # routing -- a decline when the message is off-topic, a clarifying question
    # when the panel disagreed -- and neither costs a model call. Routing is
    # never asked to pick an agent for a request that has no intent, which is
    # why RoutingDecision.primary_agent stays non-optional.
    primary_agent: AgentType | None
    supporting_agents: list[AgentType] = field(default_factory=list)
    escalated: bool = False
    latency_ms: float = 0.0
    routing_reason: str = ""
    tools_used: list[str] = field(default_factory=list)
    tool_traces: list[dict[str, Any]] = field(default_factory=list)
    # Supporting agents that raised and were dropped. The reply is still
    # returned; this is how the omission stays visible instead of looking like
    # the router simply chose fewer agents.
    dropped_agents: list[AgentType] = field(default_factory=list)
    # Per-request token rollup, accumulated across the concurrent agents and
    # the composer. Carried here rather than only inside the gateway so FR3 is
    # observable, and so /chat has somewhere to read it from later.
    usage: dict[str, Any] = field(default_factory=dict)


class AgentOrchestrator:
    """Owns the agent pool and the routing decision."""

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway
        self._composer = ResponseComposer(gateway)
        # List per type from the start: same role, different model or different
        # online performance, is the extension this structure reserves room for.
        self._pool: dict[AgentType, list[BaseAgent]] = {
            agent_type: [cls(gateway)] for agent_type, cls in AGENT_CLASSES.items()
        }

    def registered_agents(self) -> list[str]:
        """Agent type names with at least one live instance."""
        return [agent_type.value for agent_type, pool in self._pool.items() if pool]

    # -- routing ------------------------------------------------------------

    def _route_decision(self, intent: Intent) -> RoutingDecision:
        """Pick the agents for an intent. Pure function: no model call, no I/O.

        Not called for an OTHER intent -- ``run`` answers those first, which is
        what lets ``primary_agent`` here stay non-optional.
        """
        if intent.category is IntentCategory.HUMAN_TUTOR:
            # Keyed on the category, never on urgency. compute_urgency returns
            # CRITICAL for any deadline within a day, so keying on urgency
            # escalated "explain BFS to me today" to a human and answered
            # nothing. A deadline is when a student needs the most help.
            return RoutingDecision(
                primary_agent=AgentType.TUTOR_HANDOFF,
                reason="explicit request for a human tutor",
                confidence=intent.confidence,
            )

        primary = _GROUP_AGENTS[intent.group]
        supporting: list[AgentType] = []
        for candidate in intent.supporting_candidates():
            agent = _GROUP_AGENTS[_group_of(candidate)]
            # Two intents in different groups can still map to one agent; a
            # duplicate would dispatch the same role twice and then pay the
            # composer to merge an answer with itself.
            if agent is primary or agent in supporting:
                continue
            supporting.append(agent)
            if len(supporting) + 1 >= MAX_AGENTS:
                break

        return RoutingDecision(
            primary_agent=primary,
            supporting_agents=supporting,
            reason=_explain(intent, primary, supporting),
            confidence=intent.confidence,
        )

    def _best_agent(self, agent_type: AgentType) -> BaseAgent | None:
        """Best instance of a role. One instance per role until the pool lands."""
        pool = self._pool.get(agent_type) or []
        if not pool:
            return None
        return max(pool, key=_routing_score)

    # -- dispatch -----------------------------------------------------------

    async def run(self, request: AgentRequest) -> OrchestratorResult:
        """Answer one turn end to end.

        Raises:
            PrimaryAgentFailed: the chosen agent did not answer (FR6).
        """
        started = time.monotonic()
        request_id = request.request_id or uuid.uuid4().hex
        intent = request.intent

        if intent is not None and intent.category is IntentCategory.OTHER:
            return self._answer_without_agents(request, request_id, started)

        decision = self._route_decision(intent)

        # The whole dispatch sits inside one scope, so concurrent agents and
        # the composer accumulate into a single per-request rollup. The
        # contextvar is copied by binding into each gathered task, so the
        # tasks mutate the same object the parent holds.
        async with self._gateway.request_scope() as rollup:
            results = await asyncio.gather(
                *(self._dispatch(agent_type, request) for agent_type in decision.agent_types),
                return_exceptions=True,
            )

            answers: list[AgentResponse] = []
            dropped: list[AgentType] = []
            for agent_type, result in zip(decision.agent_types, results, strict=True):
                if isinstance(result, AgentResponse) and result.success:
                    answers.append(result)
                    continue
                if agent_type is decision.primary_agent:
                    raise PrimaryAgentFailed(
                        f"{agent_type.value} failed and no other role may answer "
                        f"in its place: {result!r}"
                    )
                logger.warning("supporting agent dropped: agent=%s", agent_type.value)
                dropped.append(agent_type)

            reply = await self._compose(request.message, answers)

        return OrchestratorResult(
            request_id=request_id,
            response=reply,
            primary_agent=decision.primary_agent,
            supporting_agents=[a for a in decision.supporting_agents if a not in dropped],
            escalated=any(answer.escalate for answer in answers),
            latency_ms=round(_elapsed_ms(started), 2),
            routing_reason=_with_drops(decision.reason, dropped),
            tools_used=[tool for answer in answers for tool in answer.tools_used],
            tool_traces=[trace for answer in answers for trace in answer.tool_traces],
            dropped_agents=dropped,
            usage=rollup.as_dict(),
        )

    async def _dispatch(self, agent_type: AgentType, request: AgentRequest) -> AgentResponse:
        agent = self._best_agent(agent_type)
        if agent is None:
            # The FR7 contract makes this unreachable at boot; if it ever fires
            # it means the pool was mutated at runtime.
            raise PrimaryAgentFailed(f"no registered instance of {agent_type.value}")
        return await agent.handle(request)

    async def _compose(self, message: str, answers: list[AgentResponse]) -> str:
        """One answer is returned verbatim; several go through the composer."""
        if not answers:
            raise PrimaryAgentFailed("no agent produced an answer")
        if len(answers) == 1:
            return answers[0].content
        return await self._composer.compose(message, answers)

    # -- the paths that never reach an agent --------------------------------

    def _answer_without_agents(
        self, request: AgentRequest, request_id: str, started: float
    ) -> OrchestratorResult:
        """Answer an OTHER intent. Deterministic, and free.

        Two cases with opposite answers, which is why the recogniser records
        which one applies instead of leaving it to be inferred:

          is_fallback   something was asked and the panel disagreed about what.
                        Ask which, using the evidence already gathered.
          otherwise     the message is off topic, or no signal returned
                        anything. Decline and say what is on topic.

        Neither calls a model. Collapsing them would answer an ambiguous study
        question with a refusal.
        """
        intent = request.intent
        if intent.is_fallback:
            reply = _clarify(intent)
            reason = f"ambiguous: best candidate {intent.fallback_from.value} below threshold"
        else:
            reply = (
                "That is outside what I can help with. I cover computer-science "
                "study: explaining concepts, hints on problems you are working "
                "through, study plans, and quizzes."
            )
            reason = "no relevant study intent"

        return OrchestratorResult(
            request_id=request_id,
            response=reply,
            primary_agent=None,
            latency_ms=round(_elapsed_ms(started), 2),
            routing_reason=reason,
        )


# -- helpers ----------------------------------------------------------------


def _group_of(category: IntentCategory) -> IntentGroup:
    from owl_mind.core.intent_recognizer import _INTENT_GROUPS

    return _INTENT_GROUPS[category]


def _routing_score(agent: BaseAgent) -> float:
    """Success rate, penalised by the monitor. Latency joins when the pool does."""
    return agent.stats.success_rate - agent.stats.monitor_penalty


def _clarify(intent: Intent) -> str:
    """A clarifying question built from the evidence, not from a template.

    Names the top candidates so the student picks rather than rephrases. Two is
    enough; a list of five reads as a failure to understand anything.
    """
    ranked = sorted(intent.scores.items(), key=lambda pair: (-pair[1], pair[0].value))
    names = [_PHRASING.get(category, category.value) for category, _ in ranked[:2]]
    if len(names) >= 2:
        return (
            f"I am not sure whether you want {names[0]} or {names[1]}. Which would help?"
        )
    if names:
        return f"Did you want {names[0]}? Tell me a little more and I will start."
    return "I did not follow that. What would you like help with?"


# Intent names are identifiers; a student should be asked a question, not shown
# an enum. Only the intents a clarifying question can plausibly reach need one.
_PHRASING: dict[IntentCategory, str] = {
    IntentCategory.CONCEPT_EXPLAIN: "an explanation",
    IntentCategory.CONCEPT_COMPARE: "a comparison",
    IntentCategory.MATERIAL_SEARCH: "a pointer to the course materials",
    IntentCategory.PROBLEM_HELP: "a hint on a problem",
    IntentCategory.HOMEWORK_CHECK: "your answer checked",
    IntentCategory.CODE_REVIEW: "your code reviewed",
    IntentCategory.COMPLEXITY_ANALYSIS: "a complexity analysis",
    IntentCategory.STUDY_PLAN: "a study plan",
    IntentCategory.PROGRESS_QUERY: "your progress so far",
    IntentCategory.DEADLINE: "help with a deadline",
    IntentCategory.QUIZ_REQUEST: "a quiz",
    IntentCategory.ANSWER_SUBMISSION: "an answer marked",
    IntentCategory.MOCK_EXAM: "a practice exam",
    IntentCategory.EXPLAIN_BACK: "your understanding checked",
    IntentCategory.MOTIVATION: "to talk about how the course is going",
}


def _explain(
    intent: Intent, primary: AgentType, supporting: list[AgentType]
) -> str:
    """Why this route. A route that cannot be explained cannot be debugged."""
    vector = ", ".join(
        f"{category.value}={score:.3f}"
        for category, score in sorted(
            intent.scores.items(), key=lambda pair: (-pair[1], pair[0].value)
        )[:4]
    )
    names = ", ".join(agent.value for agent in supporting) or "none"
    return (
        f"primary={primary.value} supporting={names} "
        f"intent={intent.category.value} scores=[{vector}]"
    )


def _with_drops(reason: str, dropped: list[AgentType]) -> str:
    if not dropped:
        return reason
    return f"{reason} dropped=[{', '.join(agent.value for agent in dropped)}]"


def _elapsed_ms(started: float) -> float:
    """Monotonic elapsed milliseconds. Never time.time() -- it can go backwards."""
    return (time.monotonic() - started) * 1000
