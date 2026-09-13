"""The five agents. Implements ISSUE-005 FR1. Plan reference: section 3.1.

Every split here is a capability boundary, not a topic. Each role is forbidden
something another is allowed, and the forbidden thing is enforced before the
model is called rather than requested in a prompt:

    ConceptAgent       explains; cites materials, never invents an API
    PracticeAgent      hints; **never emits a complete solution**
    PlannerAgent       schedules; arithmetic is a tool, never a guess
    QuizAgent          generates and grades; grading is reproducible
    TutorHandoffAgent  escalates; **makes no model call at all**

That last one is the property worth protecting. An escape hatch that needs the
model is not an escape hatch -- it fails in exactly the incident it exists for.
``handle`` is overridden so there is no code path from a handoff to the gateway.

``tool_scope`` names come from plan section 3.3, and only the tools that can be
built without an external service. Two are deliberately absent until the issue
that gives them something to read:

    materials_search   MCP, not in-process -- it owns the Chroma client
    get_due_topics     reads Redis progress state, and memory is still a stub

A declared scope naming a tool that cannot work is the same lie as an
undeclared one, so those entries arrive with their backing store rather than
ahead of it. The FR7 contract fails the boot the moment scope and registry
disagree.
"""

from __future__ import annotations

from owl_mind.agents.base import (
    AgentProfile,
    AgentRequest,
    AgentResponse,
    AgentType,
    BaseAgent,
)
from owl_mind.agents.tools.shared import create_handoff_summary, render_handoff
from owl_mind.core.contracts import ContractViolation, contract

# Effort is identical across roles on purpose (ISSUE-002 FR7). Differentiating
# it is a cost/quality decision, and guessed values look considered without
# being so; the evaluation issue earns the right to change them.
_EFFORT = "medium"


PROFILES: dict[AgentType, AgentProfile] = {
    AgentType.CONCEPT: AgentProfile(
        role=(
            "You explain computer-science concepts to a student. Lead with the "
            "shortest correct explanation, then add one worked example or "
            "analogy. Prefer the course materials over your own recollection."
        ),
        tool_scope=("get_prerequisites", "inspect_request_context"),
        effort=_EFFORT,
        max_tokens=1200,
        risk_boundary=(
            "Never state an API, signature, or course fact you cannot support "
            "from the retrieved materials. Say what you do not know."
        ),
        handoff_conditions=("the student disputes the course materials themselves",),
    ),
    AgentType.PRACTICE: AgentProfile(
        role=(
            "You help a student get unstuck on a problem they are solving. "
            "Ask what they have tried, then give the smallest hint that "
            "unblocks the next step."
        ),
        tool_scope=("build_hint", "analyze_complexity", "inspect_request_context"),
        effort=_EFFORT,
        max_tokens=1000,
        risk_boundary=(
            "Never emit a complete solution, full working code, or a final "
            "answer to a problem the student is being assessed on. A hint "
            "moves them one step; it does not finish the work."
        ),
        handoff_conditions=(
            "the student asks for the answer after three hints",
            "the request looks like graded coursework being outsourced",
        ),
    ),
    AgentType.PLANNER: AgentProfile(
        role=(
            "You build study plans and schedule revision. Be concrete about "
            "what to study, in what order, and on which day."
        ),
        tool_scope=("schedule_review", "inspect_request_context"),
        effort=_EFFORT,
        max_tokens=800,
        risk_boundary=(
            "Never compute a review date or spacing interval yourself. "
            "Scheduling is arithmetic and comes from the tool, so that the "
            "same progress always yields the same plan."
        ),
        handoff_conditions=("the student reports a deadline they cannot meet",),
    ),
    AgentType.QUIZ: AgentProfile(
        role=(
            "You write quiz questions and grade answers. Questions test "
            "understanding rather than recall. Grading explains the gap, not "
            "just the verdict."
        ),
        tool_scope=("generate_quiz_spec", "grade_answer", "inspect_request_context"),
        effort=_EFFORT,
        max_tokens=1200,
        risk_boundary=(
            "Never grade by impression. A verdict comes from the rubric tool "
            "so the same answer always receives the same mark."
        ),
        handoff_conditions=("the student contests a grade twice",),
    ),
    AgentType.TUTOR_HANDOFF: AgentProfile(
        role="You hand the conversation to a human teaching assistant.",
        tool_scope=(),
        effort=_EFFORT,
        max_tokens=0,
        risk_boundary=(
            "Never answer the question. Summarise it for the human who will."
        ),
        handoff_conditions=("always -- this role exists to escalate",),
    ),
}


class ConceptAgent(BaseAgent):
    agent_type = AgentType.CONCEPT
    profile = PROFILES[AgentType.CONCEPT]


class PracticeAgent(BaseAgent):
    agent_type = AgentType.PRACTICE
    profile = PROFILES[AgentType.PRACTICE]


class PlannerAgent(BaseAgent):
    agent_type = AgentType.PLANNER
    profile = PROFILES[AgentType.PLANNER]


class QuizAgent(BaseAgent):
    agent_type = AgentType.QUIZ
    profile = PROFILES[AgentType.QUIZ]


class TutorHandoffAgent(BaseAgent):
    """Escalation to a human. Makes no model call, by construction.

    ``handle`` is overridden rather than configured, so there is no flag, no
    branch, and no future edit that can quietly reintroduce a gateway call on
    this path. ``tests/test_agents.py`` asserts the client recorded zero calls;
    that test is the reason the escape hatch survives a model outage.
    """

    agent_type = AgentType.TUTOR_HANDOFF
    profile = PROFILES[AgentType.TUTOR_HANDOFF]

    async def handle(self, request: AgentRequest) -> AgentResponse:
        # create_handoff_summary is a plain function, not a registered tool.
        # This agent has no tool loop to offer one to, and registering it would
        # invite a tool_scope here -- one edit away from a gateway call.
        summary = create_handoff_summary(request)
        self.stats.record(ok=True, elapsed_ms=0.0)
        return AgentResponse(
            agent_type=self.agent_type,
            content=render_handoff(summary),
            success=True,
            escalate=True,
            tool_traces=(
                {"tool": "create_handoff_summary", "ok": True, "summary": summary},
            ),
        )


AGENT_CLASSES: dict[AgentType, type[BaseAgent]] = {
    AgentType.CONCEPT: ConceptAgent,
    AgentType.PRACTICE: PracticeAgent,
    AgentType.PLANNER: PlannerAgent,
    AgentType.QUIZ: QuizAgent,
    AgentType.TUTOR_HANDOFF: TutorHandoffAgent,
}


# -- startup contracts (ISSUE-005 FR7) --------------------------------------


@contract("every AgentType has a profile and an implementation")
def _check_roster_is_complete() -> None:
    """A role declared but not built must fail the boot, not a request.

    Routing maps a group to an AgentType; a type with no class behind it is a
    KeyError at 3am on whichever request first reaches that group.
    """
    problems = []
    missing_profile = sorted(a.value for a in AgentType if a not in PROFILES)
    missing_class = sorted(a.value for a in AgentType if a not in AGENT_CLASSES)
    if missing_profile:
        problems.append(f"no profile: {missing_profile}")
    if missing_class:
        problems.append(f"no implementation: {missing_class}")
    if problems:
        raise ContractViolation("; ".join(problems))


@contract("every tool_scope name exists in the tool registry")
def _check_tool_scopes_resolve() -> None:
    """Trivially true while the registry is empty, and that is the point.

    Registered now so the tools issue cannot drift: the moment it adds a tool
    under a name no profile claims -- or renames one a profile does -- the boot
    fails naming both sides, instead of the tool silently never being offered.
    """
    from owl_mind.agents.tools import REGISTRY

    if not REGISTRY:
        return
    unknown = sorted(
        f"{agent.value}:{name}"
        for agent, profile in PROFILES.items()
        for name in profile.tool_scope
        if name not in REGISTRY
    )
    if unknown:
        raise ContractViolation(f"tool_scope names with no registered tool: {unknown}")


@contract("every registered tool is reachable from some agent's tool_scope")
def _check_no_orphan_tools() -> None:
    """The other direction of the scope check, and the one nobody thinks of.

    A registered tool that no profile claims is never offered to any model, so
    it is dead weight that still costs a reader time to understand and a
    maintainer time to keep working. It is usually a half-finished thought: the
    tool was written and the scope entry forgotten.

    ISSUE-007 FR7 asked for "tool names unique across agents". That is not
    implementable as written -- inspect_request_context is deliberately shared
    by every role -- and ``register`` already rejects a duplicate name outright,
    so uniqueness within the registry is structural rather than checkable. This
    is the invariant that was actually missing.
    """
    from owl_mind.agents.tools import REGISTRY

    claimed = {name for profile in PROFILES.values() for name in profile.tool_scope}
    orphans = sorted(set(REGISTRY) - claimed)
    if orphans:
        raise ContractViolation(
            f"registered but in no tool_scope, so never offered to a model: {orphans}"
        )
