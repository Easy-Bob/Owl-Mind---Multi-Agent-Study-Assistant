# [Task] Intent Recognition — Three-Way Fusion, Entities, and Urgency

> Template: `owl_mind_dev_workflow.md` §7. Scope source: `STUDY-ASSISTANT-PLAN.md` §3.2, §11.2, §11.4.
> Depends on: ISSUE-002 (LLM gateway), merged.

## Project Fields

| Field | Value |
| --- | --- |
| Type | Task |
| Priority | P1 — routing cannot be built without it |
| Area | Core / Domain |
| Status | Ready |
| Milestone | Day 2 — Domain model |
| Owner | TBD |
| Reviewer | TBD |
| Branch | `feature/5-intent-recognition` |

---

## Summary

Implement `core/intent_recognizer.py`: classify a student utterance into one of 19
intents by fusing three independent signals (a model call, embedding similarity, and
pattern rules), and return a structured judgement — category, group, confidence,
per-source scores, urgency, and extracted entities — that the routing layer consumes.

Also registers the project's first startup contract, filling the hook that ISSUE-001
created empty.

---

## Background / Business Context

Routing is only as good as the judgement it routes on. A keyword matcher is brittle
against paraphrase; a model call alone is slow, costly, and variable. Fusing three
signals means each covers the others' failure modes: the model handles semantics and
context, embeddings handle common phrasings the model may over-think, and patterns give
a zero-latency high-precision path for utterances that are unambiguous.

What matters downstream is that the output is **structured, not a label**. Routing needs
the group to pick an agent, urgency to decide escalation, entities to fill tool
arguments, and per-source scores so a bad route can be explained after the fact rather
than guessed at.

This is also the highest-volume component in the system — it fires on **every** request,
where an agent fires only when selected. That makes it the first candidate for a cheaper
model later, and the one component whose quality can be A/B'd objectively against
labelled accuracy rather than a judge. ISSUE-002 left a per-call `model` override in the
gateway for exactly this.

---

## User Story

As the routing layer,
I want a structured judgement about what the student is asking for, with confidence and
provenance,
so that I can select an agent on evidence rather than keywords, and explain the choice
when it turns out to be wrong.

---

## Functional Requirements

### FR1 — Taxonomy

- [ ] `IntentCategory` (StrEnum) with the 19 intents from plan §3.2, and `IntentGroup`
      with the five groups.

| Group | Intents |
| --- | --- |
| `LEARN` | `concept_explain`, `concept_compare`, `material_search` |
| `PRACTICE` | `problem_help`, `homework_check`, `code_review`, `complexity_analysis` |
| `PLAN` | `study_plan`, `progress_query`, `deadline` |
| `ASSESS` | `quiz_request`, `answer_submission`, `mock_exam`, `explain_back` |
| `SUPPORT` | `motivation`, `human_tutor`, `greeting`, `feedback`, `other` |

- [ ] `_INTENT_GROUPS` maps every intent to exactly one group.

### FR2 — Templates

- [ ] `_TEMPLATES`: at least 3 example utterances per intent, in English.
- [ ] The same templates serve **both** the model's few-shot block and the embedding
      anchors. One source of truth; two consumers.
- [ ] **Templates are parameters, not test data.** A sentence used as a template must
      never appear in the eval corpus — otherwise the corpus measures memorisation.
      There is no eval corpus yet, so this issue ships the rule as a docstring and a
      placeholder test that the evaluation issue turns on.

### FR3 — Pattern signal

- [ ] `_pattern_recognize(message)` returns `{intent: score}` from regex rules — **all**
      rules that match, not just the best. Pure, synchronous, no I/O.
- [ ] Covers the unambiguous cases only — `"quiz me"`, `"talk to a TA"`, greetings.
      Patterns exist for precision, not coverage; a rule that fires on ambiguous input
      makes the fusion worse, not better.

### FR4 — Embedding signal

- [ ] Returns the **top-k nearest intents with scores** (k >= 3), not a single best
      match. Chroma's query already returns n results with distances; collapsing that
      to argmax throws away the evidence routing needs for fan-out.
- [ ] Template embeddings are computed once at startup and reused; embedding every
      template on every request is wasteful and slow.
- [ ] Uses the running Chroma service rather than adding a new dependency — a dedicated
      `intent_templates` collection, seeded at startup, queried per request.
- [ ] **Create the collection with cosine distance explicitly**
      (`metadata={"hnsw:space": "cosine"}`). Chroma defaults to squared L2, and the
      reference implementation computed relevance as `1.0 - distance`, which is only
      valid for cosine — producing scores on the wrong scale that could go negative
      (plan §11.4). A test must assert every similarity score falls in `[0, 1]`.
- [ ] If Chroma is unreachable, the embedding signal contributes nothing and the
      request still completes on the other two. A degraded classifier beats a 500.

### FR5 — Model signal

- [ ] One call through `LLMGateway.complete(component="intent", ...)`.
- [ ] Few-shot prompt built from `_TEMPLATES`; structured output so the reply parses
      without regex.
- [ ] The schema asks for a **ranked list of up to 3 intents with confidences**, not one
      label. This is the highest-weighted signal; forcing it to emit a single label
      discards exactly the information a composite request carries.
- [ ] The prompt prefix (instructions + few-shot block) is **stable across requests** —
      only the student's message varies, and it goes last. That ordering is what makes
      the prefix cacheable later; putting the message anywhere else would silently
      prevent it.

### FR6 — Fusion produces a distribution, not a label

- [ ] Model and embedding signals run concurrently (`asyncio.gather`); patterns run
      synchronously. The model call is on the critical path, so the embedding lookup
      must not be serialised behind it.
- [ ] Weighted vote: model `0.7`, embedding `0.2`, pattern `0.1`.
- [ ] **Sum weighted scores across every candidate each signal returned**, producing a
      score for each intent any signal proposed — not a vote between three argmaxes.

      This is the change that makes multi-agent fan-out possible. The reference
      implementation collapsed each signal to a single label *before* voting
      (`intent_recognizer.py` `_vote`), so its score map held at most three entries and
      a composite request could only be detected downstream, by keyword matching on the
      lowest-weighted signal. The strongest signal read the second request and then
      discarded it.
- [ ] Below a confidence threshold, the **primary** falls back to `other` rather than
      guessing. A wrong confident route is worse than an admitted one.
- [ ] Weights and threshold are module constants with a comment saying they are
      untuned starting points, not derived values.

### FR7 — Structured result

- [ ] A frozen `Intent` dataclass: `category`, `group`, `confidence`, `scores`,
      `source_scores`, `urgency`, `entities`.
- [ ] `category` is the argmax and stays **single-valued**. Routing needs one agent to
      own the response and the composer needs a spine to merge onto; an ambiguous
      primary helps nobody.
- [ ] `scores: dict[IntentCategory, float]` is the fused distribution. This is what the
      routing layer reads to select supporting agents (FR6.1), and it is the field that
      makes "one message, several agents" work on evidence rather than keywords.
- [ ] `source_scores` is populated on every path, including the fallback — it is the
      only way to explain a route after the fact.

### FR6.1 — What routing will do with this (contract, not implementation)

Routing belongs to the next issue, but this issue must produce a result that supports
it, so the contract is fixed here:

- **At most 3 agents per request** — one primary plus up to two supporting.
- Supporting agents are selected from `scores` by an **absolute floor only**. Do not
  port the reference implementation's relative gate (`score >= 0.55 * primary`): on a
  genuine two-domain request it left the second agent qualifying by a hair, so one
  fewer keyword hit silently dropped half of what the student asked. With a hard cap of
  3, the cap does the limiting and the relative gate only adds a fragile edge.
- Ties break deterministically (score descending, then intent name ascending) so the
  selection is testable.
- `TutorHandoffAgent` does **not** count toward the cap. Escalation short-circuits
  before scoring; it is not a fan-out participant.

Why 3 and not more: three agents at up to three tool rounds each, plus a composer, is
already ~10 model calls for one request. It also consumes 3 of the gateway's 8
concurrency slots, so two simultaneous fan-outs saturate the process. And merging four
independent answers into one coherent reply produces mush — the composer is the real
constraint, not the budget.

### FR8 — Entities

- [ ] Rule-based extraction of `topic`, `problem_id`, `language`, `complexity_class`,
      `due_date`.
- [ ] **Do not port an error-code pattern.** The reference implementation matched
      `\b([45]\d{2})\b`, which captures any three-digit number — prices, counts, line
      numbers — as an `error_code`. It sells structured entities as routing input and
      delivers noise (plan §11.4).
- [ ] An entity that does not match returns absent, never a guess.

### FR9 — Urgency

- [ ] Four levels, driven by the triggers in plan §11.2:
      - `CRITICAL` — explicit request for a human TA, or a deadline inside 24h
      - `HIGH` — deadline inside 72h
      - `MEDIUM` — homework or quiz request with a future due date
      - `LOW` — everything else
- [ ] **The third-failed-hint trigger is deferred.** Plan §11.2 also raises urgency on a
      third failed hint for the same `problem_id`, which requires reading working
      memory. `MemoryManager` is still a stub, so this issue ships the other triggers
      and leaves a named TODO for the memory issue. Shipping a partial rule silently
      would be worse than shipping it late.

### FR10 — The first startup contract

- [ ] Register a check in `core/contracts.py` asserting every `IntentCategory` member
      appears in both `_TEMPLATES` and `_INTENT_GROUPS`.
- [ ] Adding an intent without a template must **abort startup**, not degrade silently
      at request time. This is the hook ISSUE-001 built and left empty.
- [ ] `/health` already reports `registered_contracts()`; it should now list one.

### FR11 — Tests stay free

- [ ] The suite makes no network calls. The model signal is exercised through a fake
      gateway; the embedding signal through a fake Chroma client.
- [ ] Each of the three signals is tested in isolation, then the fusion is tested with
      each signal stubbed to a known value so the arithmetic is verified independently
      of any model.

---

## Non-Functional Requirements

- [ ] No production change before PR approval.
- [ ] No credentials or `.env` files committed.
- [ ] All model calls go through the gateway with `component="intent"` — the guard rail
      already enforces this.
- [ ] No sampling parameters (existing guard rail).
- [ ] ASCII-only sources (existing guard rail).
- [ ] The recogniser holds no agent knowledge. It classifies; it does not route.
- [ ] Existing behaviour not broken: `/health`, `/metrics`, and all guard rails pass.

---

## Acceptance Criteria

Given the message "explain how quicksort partitioning works",
when `recognize()` runs,
then `category` is `concept_explain`, `group` is `LEARN`, and `source_scores` names all
three signals.

Given the message "I want to talk to a TA",
when `recognize()` runs,
then `category` is `human_tutor` and `urgency` is `CRITICAL`.

Given the message "explain BFS and then quiz me on it",
when `recognize()` runs,
then `category` is `concept_explain`, and `scores` contains `quiz_request` above the
supporting floor — so routing can select two agents from the intent result alone,
without keyword matching.

Given a message that plausibly touches four intents,
when routing selects agents,
then at most 3 are chosen, and the selection is stable across repeated runs.

Given an utterance that matches nothing well,
when every signal scores below the threshold,
then `category` is `other`, and `source_scores` is still populated so the decision can be
inspected.

Given Chroma is unreachable,
when `recognize()` runs,
then the request completes using the model and pattern signals, the embedding score is
absent from `source_scores`, and no exception escapes.

Given the intent-template collection is queried,
when similarity scores are computed,
then every score lies within `[0, 1]` — no negative values from an L2/cosine mismatch.

Given a developer adds a member to `IntentCategory` without adding templates,
when the application starts,
then startup aborts with a `ContractViolation` naming the missing intent.

Given the message "my assignment is due tomorrow and I'm stuck on two-sum",
when entities are extracted,
then `due_date` and `problem_id` are present, and no `error_code` key exists anywhere in
the result.

Given the fusion weights and three stubbed signals with known scores,
when the vote is computed,
then the winning category and confidence match the hand-calculated value.

---

## Test Notes

- [ ] `pytest` green; no network calls in the default run.
- [ ] Each signal tested alone: patterns on unambiguous input, embeddings against a fake
      collection, the model against a fake gateway.
- [ ] Fusion arithmetic tested with all three signals stubbed — this must pass with no
      model involved at all.
- [ ] Threshold fallback tested with every signal weak.
- [ ] Chroma-down path tested by making the fake client raise.
- [ ] Similarity range asserted in `[0, 1]`.
- [ ] Startup contract verified by deliberate violation: add an intent with no template,
      watch startup abort, revert.
- [ ] Entity extraction tested against utterances containing three-digit numbers that
      are *not* error codes — the regression the old pattern would have caused.
- [ ] `/health` shows one registered contract; screenshot attached to the PR.
- [ ] One `pytest -m live` run to confirm the real prompt parses; attach the token cost
      from `/metrics`.

---

## Out of Scope

- **Routing.** `_domain_scores`, primary/supporting selection, and the agent pool belong
  to the routing issue. This issue produces the judgement; it does not act on it.
- **The five agents** and `/chat`.
- **The eval corpus.** Accuracy numbers come from the evaluation issue. Do not claim an
  accuracy figure in this PR — there is nothing to measure against yet.
- **Weight tuning.** The 0.7 / 0.2 / 0.1 split is inherited, not derived. Tuning it
  without a corpus is guessing, and tuning it *with* the corpus is hill-climbing that
  needs a held-out split (plan §11.2).
- **Prompt caching** on the few-shot prefix. FR5 makes it possible by ordering the
  prompt correctly; enabling it is a later, measurable change.

---

## Risks

- **Taxonomy ambiguity.** Nineteen intents with real overlap — `concept_explain` vs
  `material_search`, `problem_help` vs `homework_check`. Expect boundary disputes.
  Mitigation: write the templates first and notice which ones you struggle to assign;
  that is where the taxonomy is wrong, not the classifier.
- **Latency on the critical path.** Every request now waits for a model call before any
  agent starts. FR6's concurrency helps; the real fix is a cheaper model on this
  component, which the gateway already supports and `/metrics` will justify.
- **Contamination.** Templates are effectively training data. If they leak into the eval
  corpus the accuracy number becomes meaningless. FR2 ships the rule now because the
  mistake is easy to make later and invisible once made.
- **Data risk:** none. No user data beyond the utterance being classified.
- **Deployment risk:** the new startup contract can abort boot. That is the intent, but
  it means a taxonomy mistake fails the deploy rather than one request — worth knowing
  before it happens at an awkward moment.

---

## Dependencies

- Related issue: ISSUE-002 (LLM gateway) — must be merged; this is its first real caller.
- Related PR: the gateway PR on `feature/3-llm-gateway`.
- Required access: Anthropic API key for the single live test.
- Required services: Chroma running (`docker compose up -d chroma`) for the embedding
  signal; the suite itself needs neither.

---

## Definition of Done

Per SOP §12, with the two adapted items carried forward from ISSUE-001.

- [ ] Requirement implemented and checkboxes checked.
- [ ] Code committed to feature branch.
- [ ] Code pushed to GitHub.
- [ ] Pull Request opened against the target branch.
- [ ] PR links the Issue using `Closes #<number>`.
- [ ] Local/dev testing completed.
- [ ] Reviewer approved the PR.
- [ ] PR merged into the target branch.
- [ ] Issue auto-closed or manually closed with explanation.
- [ ] Project card moved to Done.
- [ ] SOP/README updated if the workflow changed.
- [ ] Changes are tested locally.
- [ ] *(adapted)* Redis and Chroma connectivity verified from inside the API container.
- [ ] *(adapted)* Logs reviewed for leaked API keys, `.env` contents, and unexpected errors.

Additional, because this issue adds a startup contract and a domain vocabulary:

- [ ] `/health` lists the taxonomy contract.
- [ ] `STUDY-ASSISTANT-PLAN.md` §3.2 matches the shipped taxonomy exactly — if they drift,
      the plan is wrong, not the code.
- [ ] The deferred urgency trigger (FR9) is recorded as a TODO naming the memory issue.

---

## Follow-ups (not in this issue)

- **Third-failed-hint urgency**, once `MemoryManager` can read working memory. It is the
  one urgency rule that is a function of conversation state rather than keywords, and
  the most defensible of the four.
- **A cheaper model for this component**, once `/metrics` shows what it costs. Highest
  volume, most mechanical, and objectively measurable — the best candidate in the system.
- **Prompt caching** on the few-shot prefix.
- **Weight tuning** against a held-out split, once the eval corpus exists.
