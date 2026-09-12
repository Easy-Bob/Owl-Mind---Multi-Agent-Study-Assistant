"""Three-layer memory: working, episodic, user profile.

STUB. Implements ISSUE-001 FR2. Behaviour lands with the memory issue;
see plan section 11.1 (highlight 5).

  - working  (Redis)            the current session's recent turns
  - episodic (Chroma)           compressed summaries, merged rather than appended
  - profile  (Chroma)           long-lived per-user state

The three layers exist because their time scales differ; sharing one context
window makes them interfere. In Owl Mind the profile is load-bearing rather
than decorative: mastery and weak topics recorded here are read directly by
PlannerAgent to schedule reviews.

Note: Chroma generates the embeddings, not Anthropic. The reference
implementation's docstring claimed otherwise and the claim outlived the code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MemoryContext:
    """What the orchestrator receives for one turn."""

    working: list[dict[str, Any]]
    episodic_summary: str = ""
    user_profile: dict[str, Any] = None  # type: ignore[assignment]

    def to_prompt_text(self) -> str:
        raise NotImplementedError(
            "MemoryContext.to_prompt_text is a scaffold stub (ISSUE-001)."
        )


class MemoryManager:
    """Reads and writes the three layers."""

    async def get_context(self, session_id: str, user_id: str) -> MemoryContext:
        raise NotImplementedError(
            "MemoryManager.get_context is a scaffold stub (ISSUE-001). "
            "Implemented by the memory issue."
        )

    async def add_message(self, session_id: str, role: str, content: str) -> None:
        raise NotImplementedError(
            "MemoryManager.add_message is a scaffold stub (ISSUE-001)."
        )

    async def update_profile(self, user_id: str, turn: dict[str, Any]) -> None:
        raise NotImplementedError(
            "MemoryManager.update_profile is a scaffold stub (ISSUE-001)."
        )
