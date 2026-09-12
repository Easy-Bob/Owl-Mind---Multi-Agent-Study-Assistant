"""Materials store -- Chroma-backed course content.

STUB. Implements ISSUE-001 FR2. Behaviour lands with the materials issue;
see plan sections 3.3 and 3.4.

Ownership note: once the MCP materials server exists, *it* owns the Chroma
client and this module talks to it through the session. Chroma leaving the API
process is a free win -- its calls stop consuming the asyncio thread-pool
executor.

Two corrections inherited from the reference implementation (plan 11.4):

  - Relevance was computed as ``1.0 - distance``, which assumes cosine while
    Chroma defaults to squared L2. Scores were on the wrong scale and could go
    negative. Read the collection's configured space and convert accordingly.
  - Chunking was 500 *characters* against an embedding model that truncates at
    ~256 word-piece tokens. English prose fits; code-heavy chunks tokenize
    denser and must be verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Material:
    """One retrievable document chunk."""

    doc_id: str
    title: str
    content: str
    metadata: dict[str, Any]


class MaterialsStore:
    """Add, search, and describe the course-materials collection."""

    COLLECTION = "materials"

    async def search(self, query: str, top_k: int = 3) -> list[Material]:
        raise NotImplementedError(
            "MaterialsStore.search is a scaffold stub (ISSUE-001). "
            "Implemented by the materials issue."
        )

    async def add(self, documents: list[dict[str, Any]]) -> int:
        raise NotImplementedError("MaterialsStore.add is a scaffold stub (ISSUE-001).")

    async def stats(self) -> dict[str, Any]:
        raise NotImplementedError("MaterialsStore.stats is a scaffold stub (ISSUE-001).")
