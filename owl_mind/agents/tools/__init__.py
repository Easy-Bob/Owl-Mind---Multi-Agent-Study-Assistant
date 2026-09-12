"""Agent-level tools: deterministic ``(request, args) -> dict`` functions.

STUB. Implements ISSUE-001 FR2. Tools land with the tools issue;
see plan section 3.3.

The split rule, from the plan: MCP is for tools with external dependencies or
reuse value; in-process is for pure functions over local request state. A tool
that reads the request (build_hint, get_prerequisites) gains nothing from a
process boundary except the cost of serialising the whole context.

Registration is explicit rather than by decorator scan, so ``tool_scope`` on a
profile can be diffed against this registry at boot -- that assertion is one of
the contracts listed in core/contracts.py.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

ToolResult = dict[str, Any]
ToolHandler = Callable[[Any, dict[str, Any]], ToolResult | Awaitable[ToolResult]]


@dataclass(frozen=True)
class AgentToolSpec:
    """One callable tool, its schema, and its handler.

    ``input_schema`` is validated before dispatch: a model that hallucinates an
    argument gets a validation error, not a stack trace from the handler.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


# name -> spec. Populated by the tools issue.
REGISTRY: dict[str, AgentToolSpec] = {}


def register(spec: AgentToolSpec) -> AgentToolSpec:
    """Add a tool to the registry, rejecting duplicate names."""
    if spec.name in REGISTRY:
        raise ValueError(f"duplicate tool name: {spec.name}")
    REGISTRY[spec.name] = spec
    return spec


def get(name: str) -> AgentToolSpec:
    """Look up a tool by name."""
    return REGISTRY[name]
