"""ToolManager -- client-side policy over MCP tool calls.

STUB. Implements ISSUE-001 FR2. Behaviour lands with the MCP issue;
see plan sections 3.4 and 11.3 (highlight 10).

This package is named ``toolkit`` and not ``mcp`` on purpose: a top-level
``mcp/`` package shadows the official SDK on sys.path, and the resulting import
failure is hard to read. ``tests/test_guardrails.py`` fails if anyone recreates
that directory.

ToolManager is the MCP *client's* policy layer -- cache, circuit breaker,
timeout, fallback, schema validation, query rewrite, parallel recall, dedup.
These primitives are only meaningful once there is a real process boundary that
can fail; in-process they guard a call that cannot realistically break.
"""

from __future__ import annotations

from typing import Any


class ToolManager:
    """Policy wrapper around an MCP client session."""

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one MCP tool under cache, timeout, breaker, and fallback."""
        raise NotImplementedError(
            "ToolManager.call is a scaffold stub (ISSUE-001). "
            "Implemented by the MCP issue; see plan section 3.4."
        )

    async def search_with_rewrite(
        self, name: str, query: str, top_k: int = 3
    ) -> list[dict[str, Any]]:
        """Rewrite the query, recall in parallel, merge and dedup results."""
        raise NotImplementedError(
            "ToolManager.search_with_rewrite is a scaffold stub (ISSUE-001)."
        )
