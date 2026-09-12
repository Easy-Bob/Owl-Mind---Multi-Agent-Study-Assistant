"""Intent recognition -- three-way fusion over a 16-intent CS-study taxonomy.

Implements ISSUE-003. Plan reference: sections 3.2, 11.2, 11.4.

Three independent signals vote, each covering the others' failure modes:

    pattern    regex, zero latency, high precision, low coverage      weight 0.1
    embedding  similarity against template anchors, handles paraphrase weight 0.2
    llm        semantics and context, the strongest signal             weight 0.7

The output is a *distribution*, not a label. Each signal returns every candidate
it saw with a score, and the vote sums across all of them. The reference
implementation collapsed each signal to argmax before voting, so its score map
held at most three entries -- which meant a composite request ("explain BFS then
quiz me") could only be noticed downstream, by keyword matching on the
lowest-weighted signal. The strongest signal read the second request and threw
it away. ``Intent.scores`` is what lets routing fan out on evidence instead.

``category`` stays single-valued: routing needs one agent to own the response and
the composer needs a spine to merge onto.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from owl_mind.core.contracts import ContractViolation, contract
from owl_mind.core.llm_gateway import LLMGateway

logger = logging.getLogger(__name__)


class IntentCategory(StrEnum):
    """The 16 intents from plan section 3.2."""

    # LEARN
    CONCEPT_EXPLAIN = "concept_explain"
    CONCEPT_COMPARE = "concept_compare"
    MATERIAL_SEARCH = "material_search"
    # PRACTICE
    PROBLEM_HELP = "problem_help"
    HOMEWORK_CHECK = "homework_check"
    CODE_REVIEW = "code_review"
    COMPLEXITY_ANALYSIS = "complexity_analysis"
    # PLAN
    STUDY_PLAN = "study_plan"
    PROGRESS_QUERY = "progress_query"
    DEADLINE = "deadline"
    # ASSESS
    QUIZ_REQUEST = "quiz_request"
    # SUPPORT
    MOTIVATION = "motivation"
    HUMAN_TUTOR = "human_tutor"
    GREETING = "greeting"
    FEEDBACK = "feedback"
    OTHER = "other"


class IntentGroup(StrEnum):
    """Routing operates on groups, not individual intents."""

    LEARN = "learn"
    PRACTICE = "practice"
    PLAN = "plan"
    ASSESS = "assess"
    SUPPORT = "support"


class UrgencyLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_INTENT_GROUPS: dict[IntentCategory, IntentGroup] = {
    IntentCategory.CONCEPT_EXPLAIN: IntentGroup.LEARN,
    IntentCategory.CONCEPT_COMPARE: IntentGroup.LEARN,
    IntentCategory.MATERIAL_SEARCH: IntentGroup.LEARN,
    IntentCategory.PROBLEM_HELP: IntentGroup.PRACTICE,
    IntentCategory.HOMEWORK_CHECK: IntentGroup.PRACTICE,
    IntentCategory.CODE_REVIEW: IntentGroup.PRACTICE,
    IntentCategory.COMPLEXITY_ANALYSIS: IntentGroup.PRACTICE,
    IntentCategory.STUDY_PLAN: IntentGroup.PLAN,
    IntentCategory.PROGRESS_QUERY: IntentGroup.PLAN,
    IntentCategory.DEADLINE: IntentGroup.PLAN,
    IntentCategory.QUIZ_REQUEST: IntentGroup.ASSESS,
    IntentCategory.MOTIVATION: IntentGroup.SUPPORT,
    IntentCategory.HUMAN_TUTOR: IntentGroup.SUPPORT,
    IntentCategory.GREETING: IntentGroup.SUPPORT,
    IntentCategory.FEEDBACK: IntentGroup.SUPPORT,
    IntentCategory.OTHER: IntentGroup.SUPPORT,
}


# Templates are parameters, not test data. A sentence used here must never
# appear in the eval corpus -- otherwise the corpus measures memorisation
# rather than generalisation. tests/test_intent_recognizer.py holds the
# placeholder assertion that the evaluation issue turns on.
_TEMPLATES: dict[IntentCategory, tuple[str, ...]] = {
    IntentCategory.CONCEPT_EXPLAIN: (
        "how does quicksort partitioning work",
        "explain what a red-black tree is",
        "I do not understand how virtual memory paging works",
    ),
    IntentCategory.CONCEPT_COMPARE: (
        "what is the difference between a process and a thread",
        "BFS versus DFS, when do I use each",
        "compare mutexes and semaphores",
    ),
    IntentCategory.MATERIAL_SEARCH: (
        "where do the course notes cover B-trees",
        "find the lecture slides about normalization",
        "which chapter talks about deadlock avoidance",
    ),
    IntentCategory.PROBLEM_HELP: (
        "I am stuck on the two-sum problem",
        "I cannot figure out how to start this dynamic programming question",
        "give me a hint for problem 4",
    ),
    IntentCategory.HOMEWORK_CHECK: (
        "is my answer to question 3 correct",
        "can you check whether my proof is right",
        "did I get this recurrence relation right",
    ),
    IntentCategory.CODE_REVIEW: (
        "why does my BFS implementation loop forever",
        "review my linked list insertion code",
        "my binary search returns the wrong index, what is wrong with it",
    ),
    IntentCategory.COMPLEXITY_ANALYSIS: (
        "what is the time complexity of this function",
        "why is my solution quadratic instead of linear",
        "how much space does this recursion use",
    ),
    IntentCategory.STUDY_PLAN: (
        "what should I study this week",
        "help me plan revision before the midterm",
        "build me a schedule for learning graph algorithms",
    ),
    IntentCategory.PROGRESS_QUERY: (
        "which topics am I weakest on",
        "how am I doing so far",
        "what have I already covered",
    ),
    IntentCategory.DEADLINE: (
        "my assignment is due on Friday",
        "when is the project deadline",
        "I have an exam next week",
    ),
    IntentCategory.QUIZ_REQUEST: (
        "quiz me on graph traversal",
        "test my understanding of hash tables",
        "give me some practice questions about recursion",
    ),
    IntentCategory.MOTIVATION: (
        "I will never understand this subject",
        "I feel like giving up on this course",
        "this is too hard and I am falling behind",
    ),
    IntentCategory.HUMAN_TUTOR: (
        "I want to talk to a TA",
        "can I speak to a real person",
        "please escalate this to my instructor",
    ),
    IntentCategory.GREETING: (
        "hi",
        "hello there",
        "good morning",
    ),
    IntentCategory.FEEDBACK: (
        "your hints are too vague",
        "this explanation was really helpful",
        "the quiz questions were too easy",
    ),
    IntentCategory.OTHER: (
        "what is the weather like",
        "tell me a joke",
        "who won the game last night",
    ),
}


# Patterns exist for precision, not coverage. A rule that fires on ambiguous
# input makes the fusion worse rather than better, so only unmistakable
# phrasings belong here. Every rule that matches contributes -- the signal
# returns all of them, not just the best.
_PATTERNS: tuple[tuple[re.Pattern[str], IntentCategory, float], ...] = (
    (re.compile(r"\b(quiz|test)\s+me\b", re.I), IntentCategory.QUIZ_REQUEST, 0.95),
    (re.compile(r"\bpractice questions?\b", re.I), IntentCategory.QUIZ_REQUEST, 0.85),
    (re.compile(r"\b(talk|speak)\s+to\s+(a\s+)?(ta|human|person|tutor|instructor)\b", re.I),
     IntentCategory.HUMAN_TUTOR, 0.95),
    (re.compile(r"\bescalate\b", re.I), IntentCategory.HUMAN_TUTOR, 0.8),
    (re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening))\b[\s!.]*$", re.I),
     IntentCategory.GREETING, 0.95),
    (re.compile(r"\btime complexity\b|\bbig[- ]?o\b", re.I),
     IntentCategory.COMPLEXITY_ANALYSIS, 0.85),
    (re.compile(r"\b(what should i|help me plan)\b.*\b(study|revise|learn)\b", re.I),
     IntentCategory.STUDY_PLAN, 0.8),
    (re.compile(r"\bdifference between\b|\bvs\.?\b|\bversus\b", re.I),
     IntentCategory.CONCEPT_COMPARE, 0.7),
    (re.compile(r"\b(i am|i'm)\s+stuck\b", re.I), IntentCategory.PROBLEM_HELP, 0.85),
    (re.compile(r"\bgive me a hint\b", re.I), IntentCategory.PROBLEM_HELP, 0.9),
)


# Untuned starting points, inherited from the reference implementation rather
# than derived. The evaluation issue tunes them against a held-out split;
# moving them before then is guessing with extra steps.
WEIGHTS: dict[str, float] = {"llm": 0.7, "embedding": 0.2, "pattern": 0.1}
PRIMARY_THRESHOLD = 0.35
SUPPORTING_FLOOR = 0.25
MAX_AGENTS = 3


@dataclass(frozen=True)
class Intent:
    """A structured judgement about one utterance."""

    category: IntentCategory
    group: IntentGroup
    confidence: float
    # The fused distribution. Routing reads this to select supporting agents;
    # it is what makes "one message, several agents" work on evidence rather
    # than on keyword matching.
    scores: dict[IntentCategory, float] = field(default_factory=dict)
    # Per-signal confidence in the winner. Populated on every path including
    # the fallback -- it is the only way to explain a route after the fact.
    source_scores: dict[str, float] = field(default_factory=dict)
    urgency: UrgencyLevel = UrgencyLevel.LOW
    entities: dict[str, Any] = field(default_factory=dict)

    def supporting_candidates(self) -> list[IntentCategory]:
        """Intents above the floor, excluding the primary, best first.

        Routing turns these into supporting agents (at most two -- see
        MAX_AGENTS). Ties break by name so the selection is deterministic and
        therefore testable.
        """
        others = [
            (intent, score)
            for intent, score in self.scores.items()
            if intent is not self.category and score >= SUPPORTING_FLOOR
        ]
        others.sort(key=lambda pair: (-pair[1], pair[0].value))
        return [intent for intent, _ in others]


class TemplateIndex(Protocol):
    """Similarity lookup over the intent templates."""

    async def search(self, message: str, k: int = 5) -> dict[IntentCategory, float]:
        """Return up to k nearest intents with similarity in [0, 1]."""
        ...


class ChromaTemplateIndex:
    """Chroma-backed template index.

    Uses the running Chroma service rather than adding an embedding dependency
    to this process. The collection is created with cosine distance
    **explicitly**: Chroma defaults to squared L2, and the reference
    implementation computed relevance as ``1.0 - distance``, which is only
    valid for cosine -- producing scores on the wrong scale that could go
    negative (plan section 11.4).
    """

    COLLECTION = "intent_templates"

    def __init__(self, client: Any) -> None:
        self._client = client
        self._collection: Any | None = None

    async def seed(self) -> None:
        """Create and populate the collection. Idempotent."""
        collection = await asyncio.to_thread(
            self._client.get_or_create_collection,
            name=self.COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        documents: list[str] = []
        ids: list[str] = []
        metadatas: list[dict[str, str]] = []
        for intent, examples in _TEMPLATES.items():
            for position, example in enumerate(examples):
                documents.append(example)
                ids.append(f"{intent.value}-{position}")
                metadatas.append({"intent": intent.value})
        await asyncio.to_thread(
            collection.upsert, ids=ids, documents=documents, metadatas=metadatas
        )
        self._collection = collection

    async def search(self, message: str, k: int = 5) -> dict[IntentCategory, float]:
        if self._collection is None:
            return {}
        result = await asyncio.to_thread(
            self._collection.query, query_texts=[message], n_results=k
        )
        scores: dict[IntentCategory, float] = {}
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        for metadata, distance in zip(metadatas, distances, strict=False):
            intent = IntentCategory(metadata["intent"])
            # Cosine distance in [0, 2]; clamp so a numerically noisy 1.0001
            # cannot produce a negative similarity.
            similarity = max(0.0, min(1.0, 1.0 - float(distance)))
            scores[intent] = max(scores.get(intent, 0.0), similarity)
        return scores


class IntentRecognizer:
    """Fuses three signals into one structured judgement."""

    def __init__(self, gateway: LLMGateway, index: TemplateIndex | None = None) -> None:
        self._gateway = gateway
        self._index = index

    async def recognize(self, message: str, *, now: datetime | None = None) -> Intent:
        moment = now or datetime.now(UTC)

        pattern_scores = _pattern_signal(message)
        # The model call is on the critical path, so the embedding lookup runs
        # beside it rather than behind it.
        llm_scores, embedding_scores = await asyncio.gather(
            self._llm_signal(message),
            self._embedding_signal(message),
        )

        scores = _fuse(
            {
                "llm": llm_scores,
                "embedding": embedding_scores,
                "pattern": pattern_scores,
            }
        )

        entities = extract_entities(message, now=moment)

        if scores:
            category = max(scores, key=lambda intent: (scores[intent], -ord(intent.value[0])))
            confidence = scores[category]
        else:
            category, confidence = IntentCategory.OTHER, 0.0

        if confidence < PRIMARY_THRESHOLD:
            category = IntentCategory.OTHER

        source_scores = {
            "llm": llm_scores.get(category, 0.0),
            "embedding": embedding_scores.get(category, 0.0),
            "pattern": pattern_scores.get(category, 0.0),
        }

        return Intent(
            category=category,
            group=_INTENT_GROUPS[category],
            confidence=round(confidence, 4),
            scores={intent: round(score, 4) for intent, score in scores.items()},
            source_scores=source_scores,
            urgency=compute_urgency(category, entities, now=moment),
            entities=entities,
        )

    # -- signals ------------------------------------------------------------

    async def _embedding_signal(self, message: str) -> dict[IntentCategory, float]:
        if self._index is None:
            return {}
        try:
            return await self._index.search(message)
        except Exception as exc:
            # A degraded classifier beats a 500. The other two signals carry
            # the request and source_scores records the absence.
            logger.warning("embedding signal unavailable: %s", exc)
            return {}

    async def _llm_signal(self, message: str) -> dict[IntentCategory, float]:
        try:
            response = await self._gateway.complete(
                component="intent",
                max_tokens=400,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": message}],
                output_config={"format": {"type": "json_schema", "schema": _LLM_SCHEMA}},
            )
        except Exception as exc:
            logger.warning("llm signal unavailable: %s", exc)
            return {}
        return _parse_llm_response(response)


def _pattern_signal(message: str) -> dict[IntentCategory, float]:
    """Every matching rule contributes, not just the best one."""
    scores: dict[IntentCategory, float] = {}
    for pattern, intent, score in _PATTERNS:
        if pattern.search(message):
            scores[intent] = max(scores.get(intent, 0.0), score)
    return scores


def _fuse(signals: dict[str, dict[IntentCategory, float]]) -> dict[IntentCategory, float]:
    """Weighted sum across every candidate every signal returned.

    Not a vote between three argmaxes -- that is the shape that made composite
    requests undetectable in the reference implementation.
    """
    fused: dict[IntentCategory, float] = {}
    for name, scores in signals.items():
        weight = WEIGHTS[name]
        for intent, score in scores.items():
            fused[intent] = fused.get(intent, 0.0) + weight * score
    return fused


# -- the model signal's prompt ---------------------------------------------

# The instructions and the example block are byte-stable across requests, and
# the student's message arrives separately as the user turn. That ordering is
# what makes the prefix cacheable later; interpolating the message into the
# system prompt would silently prevent it.
_SYSTEM_PROMPT = (
    "You classify a computer-science student's message into study intents.\n\n"
    "Return the intents that apply, ranked, with a confidence from 0 to 1. "
    "A message may genuinely carry more than one intent -- for example asking "
    "for an explanation and then a quiz. Return every intent that applies, at "
    "most three. Do not invent intents outside the list.\n\n"
    "Intents and example phrasings:\n"
    + "\n".join(
        f"- {intent.value}: " + "; ".join(examples)
        for intent, examples in _TEMPLATES.items()
    )
)

_LLM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string", "enum": [i.value for i in IntentCategory]},
                    "confidence": {"type": "number"},
                },
                "required": ["intent", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["intents"],
    "additionalProperties": False,
}


def _parse_llm_response(response: Any) -> dict[IntentCategory, float]:
    """Pull the ranked list out of a structured response.

    Tolerant by design: a malformed reply degrades this signal to nothing
    rather than failing the request, because two other signals remain.
    """
    text = ""
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "")
            break
    if not text:
        return {}

    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        logger.warning("llm signal returned unparseable output")
        return {}

    scores: dict[IntentCategory, float] = {}
    for entry in payload.get("intents", [])[:3]:
        try:
            intent = IntentCategory(entry["intent"])
            confidence = float(entry["confidence"])
        except (KeyError, TypeError, ValueError):
            continue
        scores[intent] = max(scores.get(intent, 0.0), max(0.0, min(1.0, confidence)))
    return scores


# -- entities ---------------------------------------------------------------
#
# Deliberately absent: an error-code rule. The reference implementation matched
# \b([45]\d{2})\b, which captures any three-digit number -- prices, counts,
# line numbers -- and sells the result as routing input (plan section 11.4).

_LANGUAGES = (
    "python", "java", "javascript", "typescript", "c++", "c#", "rust", "go",
    "sql", "haskell", "kotlin", "ruby",
)

_TOPICS = (
    "quicksort", "mergesort", "binary search", "linked list", "hash table",
    "red-black tree", "b-tree", "binary tree", "graph traversal", "bfs", "dfs",
    "dijkstra", "dynamic programming", "recursion", "deadlock", "mutex",
    "semaphore", "virtual memory", "paging", "normalization", "sql index",
    "tcp", "http", "concurrency", "big-o", "heap", "stack", "queue",
)

_PROBLEM_ID = re.compile(
    r"\b(?:problem|question|exercise|q|lc|leetcode)\s*#?\s*(\d{1,4})\b", re.I
)
_PROBLEM_SLUG = re.compile(r"\b([a-z]+(?:-[a-z]+){1,3})\b")
_KNOWN_SLUGS = frozenset({"two-sum", "three-sum", "word-ladder", "course-schedule"})
_COMPLEXITY = re.compile(r"\bO\s*\(\s*[^)]{1,20}\)", re.I)
_IN_DAYS = re.compile(r"\bin\s+(\d{1,2})\s+days?\b", re.I)
_WEEKDAYS = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
)


def extract_entities(message: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Rule-based extraction. An entity that does not match is absent, never guessed."""
    moment = now or datetime.now(UTC)
    lowered = message.lower()
    entities: dict[str, Any] = {}

    topic = next((t for t in _TOPICS if t in lowered), None)
    if topic:
        entities["topic"] = topic

    slug_match = next(
        (m.group(1) for m in _PROBLEM_SLUG.finditer(lowered) if m.group(1) in _KNOWN_SLUGS),
        None,
    )
    if slug_match:
        entities["problem_id"] = slug_match
    else:
        numbered = _PROBLEM_ID.search(message)
        if numbered:
            entities["problem_id"] = numbered.group(1)

    language = next((lang for lang in _LANGUAGES if lang in lowered), None)
    if language:
        entities["language"] = language

    complexity = _COMPLEXITY.search(message)
    if complexity:
        entities["complexity_class"] = complexity.group(0).replace(" ", "")

    due = _extract_due_date(lowered, moment)
    if due is not None:
        entities["due_date"] = due.isoformat()

    return entities


def _extract_due_date(lowered: str, moment: datetime) -> date | None:
    today = moment.date()
    if "tomorrow" in lowered:
        return today + timedelta(days=1)
    if "today" in lowered or "tonight" in lowered:
        return today
    in_days = _IN_DAYS.search(lowered)
    if in_days:
        return today + timedelta(days=int(in_days.group(1)))
    if "next week" in lowered:
        return today + timedelta(days=7)
    for offset, weekday in enumerate(_WEEKDAYS):
        if weekday in lowered:
            ahead = (offset - today.weekday()) % 7 or 7
            return today + timedelta(days=ahead)
    return None


def compute_urgency(
    category: IntentCategory,
    entities: dict[str, Any],
    *,
    now: datetime | None = None,
) -> UrgencyLevel:
    """Urgency from an explicit escalation request or deadline proximity.

    Plan section 11.2 defines a fourth trigger -- the third failed hint on the
    same problem_id -- which raises urgency to HIGH. It reads working memory,
    and MemoryManager is still a stub, so it is deferred to the memory issue
    rather than shipped half-working. It is the most defensible of the four,
    being a function of conversation state rather than of keywords.
    """
    if category is IntentCategory.HUMAN_TUTOR:
        return UrgencyLevel.CRITICAL

    due_iso = entities.get("due_date")
    if due_iso:
        moment = now or datetime.now(UTC)
        days_left = (date.fromisoformat(due_iso) - moment.date()).days
        if days_left <= 1:
            return UrgencyLevel.CRITICAL
        if days_left <= 3:
            return UrgencyLevel.HIGH
        return UrgencyLevel.MEDIUM

    if category in (IntentCategory.HOMEWORK_CHECK, IntentCategory.QUIZ_REQUEST):
        return UrgencyLevel.MEDIUM
    return UrgencyLevel.LOW


# -- startup contract -------------------------------------------------------


@contract("every IntentCategory has templates and a group")
def _check_taxonomy_is_complete() -> None:
    """Taxonomy drift must fail the boot, not one request at 3am."""
    missing_templates = sorted(
        intent.value for intent in IntentCategory if not _TEMPLATES.get(intent)
    )
    missing_groups = sorted(
        intent.value for intent in IntentCategory if intent not in _INTENT_GROUPS
    )
    problems = []
    if missing_templates:
        problems.append(f"no templates: {missing_templates}")
    if missing_groups:
        problems.append(f"no group: {missing_groups}")
    if problems:
        raise ContractViolation("; ".join(problems))
