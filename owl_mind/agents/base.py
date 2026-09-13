"""Agent base types: AgentType, AgentProfile, BaseAgent.

Implements ISSUE-001 FR2 and ISSUE-005 FR1/FR4. Plan reference: section 3.1.

The roster is declared here because the profile fields *are* the argument for
multi-agent: the agents differ in what they may do (tool_scope) and what they
are forbidden to emit (risk_boundary) -- not merely in what they talk about.

Note on determinism (ISSUE-002 FR7): reproducibility does not come from a
sampling parameter. It comes from moving the exact work into tools -- a grade
is computed by comparing against a rubric in code, a review date by SM-2
arithmetic. The model only phrases the result. Sampling parameters are not sent
at all; see core/llm_gateway.py.

The whitelist is the mechanism, not the prose
---------------------------------------------
``tool_scope`` is applied when the request payload is *built*. A tool outside
the tuple is absent from ``tools=``, so the model is never offered it and
cannot call it. That is a different guarantee from rejecting the call on
return: a prompt instruction can be argued with, an absent tool cannot.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from owl_mind.agents.tools import REGISTRY, AgentToolSpec
from owl_mind.core.llm_gateway import LLMGateway

logger = logging.getLogger(__name__)

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# The loop is bounded because an unbounded one is a bill, not a feature. Three
# rounds is the reference implementation's value and is enough for
# recall -> read -> answer; a fourth has never been observed to help.
MAX_TOOL_ROUNDS = 3


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
        effort: reasoning depth and token spend. Deliberately the same for every
            role until the eval corpus can justify differentiating them -- it is
            a cost/quality dial, and guessed values look considered without
            being so.
        max_tokens: per-role ceiling; the monolith alternative provisions every
            call for the worst case.
        risk_boundary: the one thing this role must never emit. Asserted
            deterministically in evaluation, not just stated in the prompt.
        handoff_conditions: when this role should route to a human.
    """

    role: str
    tool_scope: tuple[str, ...] = ()
    effort: Effort = "medium"
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

    @property
    def success_rate(self) -> float:
        """1.0 with no history: a fresh instance is not assumed broken.

        The pool issue must revisit this -- under max() selection a warm-up
        value of 1.0 makes a new instance capture all of a role's traffic.
        With one instance per type it cannot bite yet.
        """
        return 1.0 if self.total == 0 else self.success / self.total

    @property
    def average_ms(self) -> float:
        return 0.0 if self.total == 0 else self.total_ms / self.total

    def record(self, *, ok: bool, elapsed_ms: float) -> None:
        self.total += 1
        self.total_ms += elapsed_ms
        if ok:
            self.success += 1


@dataclass
class AgentRequest:
    """What an agent needs to answer one turn.

    Memory is not populated yet (ISSUE-005 Out of Scope); ``history`` exists so
    the memory issue fills a field rather than changing a signature.
    """

    message: str
    intent: Any = None
    request_id: str = ""
    session_id: str = ""
    user_id: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentResponse:
    """One agent's answer to one request."""

    agent_type: AgentType
    content: str
    success: bool = True
    escalate: bool = False
    tools_used: tuple[str, ...] = ()
    tool_traces: tuple[dict[str, Any], ...] = field(default_factory=tuple)


class ToolValidationError(ValueError):
    """A tool call's arguments do not satisfy its declared input schema."""


def validate_tool_input(spec: AgentToolSpec, arguments: Any) -> dict[str, Any]:
    """Check ``arguments`` against ``spec.input_schema``. Raise on a mismatch.

    A deliberately small subset of JSON Schema: object type, ``required``,
    declared property types, and ``additionalProperties: false``. That is what
    the agent tools actually declare, and it avoids a dependency whose full
    feature set this project would never use.

    The point is not to be a validator. It is that a model which hallucinates
    an argument gets a readable error *as a tool result* and can correct itself
    on the next round, instead of raising out of the handler and failing the
    whole turn.
    """
    schema = spec.input_schema
    if not isinstance(arguments, dict):
        raise ToolValidationError(f"{spec.name}: arguments must be an object")

    required = schema.get("required", ())
    missing = [key for key in required if key not in arguments]
    if missing:
        raise ToolValidationError(f"{spec.name}: missing required {sorted(missing)}")

    properties: dict[str, Any] = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        unknown = [key for key in arguments if key not in properties]
        if unknown:
            raise ToolValidationError(f"{spec.name}: unknown argument {sorted(unknown)}")

    json_types: dict[str, Any] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for key, value in arguments.items():
        declared = properties.get(key, {}).get("type")
        expected = json_types.get(declared) if declared else None
        if expected is None:
            continue
        # bool is a subclass of int; an integer field must not accept True.
        if declared in ("integer", "number") and isinstance(value, bool):
            raise ToolValidationError(f"{spec.name}: {key} must be {declared}")
        if not isinstance(value, expected):
            raise ToolValidationError(f"{spec.name}: {key} must be {declared}")
    return arguments


class BaseAgent:
    """Common LLM call, tool loop, whitelist enforcement, and statistics."""

    agent_type: AgentType
    profile: AgentProfile

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway
        self.stats = AgentStats()

    # -- request construction ----------------------------------------------

    @property
    def component(self) -> str:
        """Gateway attribution label. Must be in KNOWN_COMPONENTS."""
        return f"agent:{self.agent_type.value}"

    def allowed_tools(self) -> list[dict[str, Any]]:
        """Tool definitions this role may be offered, and no others.

        Built from ``profile.tool_scope``, so the whitelist is applied when the
        payload is constructed rather than when a result comes back. A name in
        the scope with no registered tool is skipped here and caught at boot by
        the FR7 contract -- this path must not raise mid-request.
        """
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
            }
            for name in self.profile.tool_scope
            if (spec := REGISTRY.get(name)) is not None
        ]

    def system_prompt(self) -> str:
        """Role statement plus the one boundary this role must not cross.

        Static in this issue. Skills injection is a later issue, which is why
        the boundary is also enforced structurally (tool_scope) rather than
        resting on this text.
        """
        prompt = self.profile.role
        if self.profile.risk_boundary:
            prompt += f"\n\nHard boundary: {self.profile.risk_boundary}"
        return prompt

    # -- the loop -----------------------------------------------------------

    async def handle(self, request: AgentRequest) -> AgentResponse:
        """Answer one request, running tools until the model stops asking.

        Statistics are recorded on every exit path, including failure, because
        the monitor's whole purpose is to notice an agent that has started
        losing.
        """
        started = time.monotonic()
        try:
            response = await self._run_loop(request)
        except Exception:
            self.stats.record(ok=False, elapsed_ms=_elapsed_ms(started))
            raise
        self.stats.record(ok=response.success, elapsed_ms=_elapsed_ms(started))
        return response

    async def _run_loop(self, request: AgentRequest) -> AgentResponse:
        tools = self.allowed_tools()
        messages: list[dict[str, Any]] = [{"role": "user", "content": request.message}]
        traces: list[dict[str, Any]] = []
        used: list[str] = []
        text = ""

        for _ in range(MAX_TOOL_ROUNDS):
            kwargs: dict[str, Any] = {
                "max_tokens": self.profile.max_tokens,
                "system": self.system_prompt(),
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools

            response = await self._gateway.complete(component=self.component, **kwargs)
            blocks = list(getattr(response, "content", None) or [])
            text = _first_text(blocks) or text

            calls = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
            if not calls or getattr(response, "stop_reason", None) != "tool_use":
                break

            messages.append({"role": "assistant", "content": blocks})
            results = []
            for call in calls:
                result, trace = await self._invoke(call, request)
                results.append(result)
                traces.append(trace)
                used.append(trace["tool"])
            messages.append({"role": "user", "content": results})
        else:
            # Loop exhausted: the model still wants tools after MAX_TOOL_ROUNDS.
            # Answer with what was gathered rather than raising -- and keep the
            # traces, which is the half the reference implementation got right.
            logger.warning(
                "tool loop exhausted: agent=%s rounds=%d", self.agent_type, MAX_TOOL_ROUNDS
            )

        # Assigned here, on the path every successful request takes. The
        # reference implementation set traces only on the exhaustion branch, so
        # /trace/tools was empty after every request that worked and the
        # monitor's tool metrics measured nothing (plan section 11.4 #3).
        return AgentResponse(
            agent_type=self.agent_type,
            content=text,
            success=True,
            tools_used=tuple(used),
            tool_traces=tuple(traces),
        )

    async def _invoke(
        self, call: Any, request: AgentRequest
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run one tool call, returning its tool_result block and its trace.

        Never raises. A tool that fails returns an error *result*, so the model
        can react to it on the next round; a raised exception would discard the
        work already done this turn.
        """
        name = getattr(call, "name", "")
        arguments = getattr(call, "input", {})
        started = time.monotonic()

        spec = REGISTRY.get(name)
        error: str | None = None
        payload: Any = None

        if spec is None:
            # Unreachable through the whitelist, since an unlisted tool is not
            # in the payload. Kept because "cannot happen" is a claim about
            # today's call sites, not about the next one.
            error = f"unknown tool {name!r}"
        else:
            try:
                validated = validate_tool_input(spec, arguments)
                result = spec.handler(request, validated)
                payload = await result if hasattr(result, "__await__") else result
            except ToolValidationError as exc:
                error = str(exc)
            except Exception as exc:
                logger.warning("tool failed: agent=%s tool=%s", self.agent_type, name)
                error = f"{type(exc).__name__}: {exc}"

        elapsed = _elapsed_ms(started)
        trace = {
            "tool": name,
            "arguments": arguments,
            "ok": error is None,
            "error": error,
            "elapsed_ms": round(elapsed, 2),
        }
        block = {
            "type": "tool_result",
            "tool_use_id": getattr(call, "id", ""),
            "content": error if error else _as_text(payload),
        }
        if error:
            block["is_error"] = True
        return block, trace


def _first_text(blocks: list[Any]) -> str:
    for block in blocks:
        if getattr(block, "type", None) == "text":
            return str(getattr(block, "text", ""))
    return ""


def _as_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return repr(payload)


def _elapsed_ms(started: float) -> float:
    """Monotonic elapsed milliseconds. Never time.time() -- it can go backwards."""
    return (time.monotonic() - started) * 1000
