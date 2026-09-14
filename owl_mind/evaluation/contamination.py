"""Contamination checks. ISSUE-009 FR2.

Why this is load-bearing
------------------------
``_TEMPLATES`` **is** the embedding index. A corpus case that also appears
there means the 0.35-weighted signal is being scored against its own training
material, and the resulting F1 measures memorisation rather than
classification. Because the templates are seeded into Chroma at startup, this
is not a hypothetical: the index would retrieve the case itself.

Two layers, because they catch different things
-----------------------------------------------
**Lexical** (always on, no dependencies, milliseconds). Normalised equality
plus token Jaccard. This catches the realistic failure: someone adds a corpus
case by copying a template, the test goes red, and they change one word to make
it pass. An exact-match check would let that through; Jaccard will not.

**Semantic** (opt-in, needs the local MiniLM model). Catches paraphrase --
"explain BFS" against "what is breadth-first search". For a *retrieval* index
this is the contamination that actually matters, since the index matches by
meaning rather than by string. It is separated because it needs an 80MB model
download, and a test suite that cannot run offline on a fresh checkout is worse
than one that checks a bit less.

Neither layer is a substitute for the other, and the report says which ran.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from owl_mind.evaluation.corpus import EvalCase, normalise

logger = logging.getLogger(__name__)

# Token overlap above this is treated as the same case reworded. 0.8 admits
# genuine near-misses ("explain BFS" vs "explain BFS to me" is 0.75) while
# catching a one-word edit of a longer template.
JACCARD_THRESHOLD = 0.8

# Character-level similarity, which catches what token overlap cannot.
# Jaccard is blind to inflection: "quiz me on graph traversal" against
# "...traversals" is two different tokens out of five, scoring 0.67 and passing
# a 0.8 bar. Adding an "s" is a smaller edit than any word swap and must not be
# the way through. 0.9 is tight enough that genuinely different sentences of
# similar shape do not trip it.
RATIO_THRESHOLD = 0.9

# Cosine similarity above this is treated as a paraphrase. Deliberately high:
# MiniLM puts two unrelated CS-study sentences around 0.5-0.6, so a lower bar
# would flag the whole corpus and the check would be turned off within a day.
COSINE_THRESHOLD = 0.92


@dataclass(frozen=True)
class Collision:
    """One corpus case too close to one template."""

    message: str
    template: str
    similarity: float
    kind: str

    def describe(self) -> str:
        return (
            f"[{self.kind} {self.similarity:.2f}] corpus {self.message!r} "
            f"vs template {self.template!r}"
        )


def _tokens(text: str) -> frozenset[str]:
    return frozenset(normalise(text).split())


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def all_templates() -> list[str]:
    """Every template string, flattened."""
    from owl_mind.core.intent_recognizer import _TEMPLATES

    return [text for group in _TEMPLATES.values() for text in group]


def check_lexical(cases: list[EvalCase], templates: list[str] | None = None) -> list[Collision]:
    """Normalised equality plus token Jaccard. Always available."""
    pool = templates if templates is not None else all_templates()
    template_tokens = [(text, _tokens(text)) for text in pool]
    exact = {normalise(text): text for text in pool}

    collisions: list[Collision] = []
    for case in cases:
        key = normalise(case.message)
        if key in exact:
            collisions.append(
                Collision(case.message, exact[key], 1.0, "exact")
            )
            continue
        case_tokens = _tokens(case.message)
        case_key = normalise(case.message)
        for text, tokens in template_tokens:
            score = _jaccard(case_tokens, tokens)
            if score >= JACCARD_THRESHOLD:
                collisions.append(Collision(case.message, text, score, "jaccard"))
                break
            ratio = SequenceMatcher(None, case_key, normalise(text)).ratio()
            if ratio >= RATIO_THRESHOLD:
                collisions.append(Collision(case.message, text, ratio, "ratio"))
                break
    return collisions


def semantic_available() -> bool:
    """True when the local embedding model can be loaded without a download.

    Checked rather than attempted so the caller can report "not run" instead of
    silently reporting "no collisions found" -- which is the same output as a
    clean corpus and would quietly retire the check.
    """
    try:
        from pathlib import Path

        import chromadb.utils.embedding_functions  # noqa: F401

        cache = Path.home() / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2"
        return cache.exists()
    except Exception:  # noqa: BLE001 -- availability probe, never fatal
        return False


def check_semantic(
    cases: list[EvalCase], templates: list[str] | None = None
) -> list[Collision]:
    """Cosine similarity against the same model the index uses.

    Raises:
        RuntimeError: if the model is unavailable. The caller must decide
            whether that is acceptable; this function will not pretend.
    """
    from chromadb.utils import embedding_functions

    if not cases:
        return []
    pool = templates if templates is not None else all_templates()
    embed = embedding_functions.DefaultEmbeddingFunction()
    if embed is None:  # pragma: no cover -- defensive
        raise RuntimeError("no local embedding function available")

    case_vectors = embed([case.message for case in cases])
    template_vectors = embed(pool)

    def cosine(a: Any, b: Any) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    collisions: list[Collision] = []
    for case, vector in zip(cases, case_vectors, strict=True):
        best, best_text = 0.0, ""
        for text, template_vector in zip(pool, template_vectors, strict=True):
            score = cosine(vector, template_vector)
            if score > best:
                best, best_text = score, text
        if best >= COSINE_THRESHOLD:
            collisions.append(Collision(case.message, best_text, best, "cosine"))
    return collisions


def report(cases: list[EvalCase], *, semantic: bool = False) -> dict[str, Any]:
    """Run the checks and describe what ran, not only what was found."""
    lexical = check_lexical(cases)
    result: dict[str, Any] = {
        "lexical_ran": True,
        "lexical_collisions": [c.describe() for c in lexical],
        "semantic_ran": False,
        "semantic_collisions": [],
    }
    if semantic:
        if not semantic_available():
            # Distinguished from "clean" on purpose. Reporting a skipped check
            # as a pass is how a check stops being one.
            result["semantic_note"] = (
                "skipped: local embedding model not cached; run the harness once "
                "with network access to populate it"
            )
        else:
            found = check_semantic(cases)
            result["semantic_ran"] = True
            result["semantic_collisions"] = [c.describe() for c in found]
    return result
