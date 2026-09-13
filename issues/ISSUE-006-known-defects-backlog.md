# [Task] Known Defects — the standing list of what is wrong and not yet fixed

> Template: `owl_mind_dev_workflow.md` §7. Scope source: review of ISSUE-001/002/003 as merged.
> Depends on: nothing. Each item below is independently landable.

## Project Fields

| Field | Value |
| --- | --- |
| Type | Task (defect register) |
| Priority | Mixed — see the tier table; F2 and F8 are P1, the rest P2/P3 |
| Area | Core / API / Housekeeping |
| Status | Open — collection only, nothing claimed |
| Milestone | F2 and F8 before ISSUE-005; the rest unscheduled |
| Owner | TBD |
| Reviewer | TBD |
| Branch | One per item, not one for the issue |

---

## Summary

A register of defects found reviewing ISSUE-001, ISSUE-002 and ISSUE-003 as merged, kept in
one place so they are not rediscovered one at a time. **Four findings from the same review
were resolved before ISSUE-005 and are recorded at the bottom** so nobody re-opens them.

This is deliberately not a single unit of work. Each item states its own evidence,
blast radius, and proposed fix, and should land as its own commit against its own branch.
Closing this issue means every item below is either fixed or consciously reclassified as
accepted behaviour — not that one PR resolved them all.

Item tags (`F2`, `F5`, …) match the ones used in the architecture write-up, so a comment
naming a tag refers to exactly one thing here.

---

## Background / Business Context

Three of these are invisible from inside the test suite. That is the property that makes
them worth a register rather than a TODO comment: **the suite is green, the endpoints
answer, and the defect only shows up against the real provider or under load.** The
default `pytest` run makes no network calls by design (ISSUE-002 FR8), which is correct
and which also means every fake-gateway test agrees the model signal works.

The tiering below is by *how much damage an item does while nobody is looking*, not by how
hard it is to fix. F2 is a two-line change that can silently halve the classifier's
evidence in production; the housekeeping items are visible the moment anyone looks at them.

| Tier | Items | Shared property |
| --- | --- | --- |
| A — silent in production | F2, F8, F10 | The request still succeeds; the damage is invisible |
| B — measurement is wrong or absent | F5, F11, F9 | A number exists and does not mean what it says |
| C — will bite the next issue | H1–H5 | Cheap now, confusing later |

---

## Tier A — silent failures

### F2 — The model signal can be truncated away without raising

**Priority P1. Scheduled into ISSUE-008 FR4** -- `/chat` is where it becomes user-visible.

`_llm_signal` requests `max_tokens=400` (`owl_mind/core/intent_recognizer.py:496`) and the
gateway sends no `thinking` parameter. On `claude-sonnet-5` — the configured default —
**omitting `thinking` runs adaptive thinking**, and `max_tokens` caps thinking *plus*
response text together. The failure chain is:

1. Thinking consumes the budget; the structured JSON is cut off, `stop_reason: "max_tokens"`.
2. `_parse_llm_response` fails `json.loads`, logs a warning, returns `{}`.
3. `_fuse` treats an empty map as "did not contribute" and renormalises over the
   remaining 0.5 of weight.
4. The request succeeds. The strongest signal is gone and nothing raises.

The broad `except Exception` in `_llm_signal` is correct as a degradation policy and is
also what makes this invisible: the only trace is a `llm signal returned unparseable
output` line. Every test uses `FakeAnthropic`, so the suite cannot reach this path.

**Proposed fix.** Send `thinking={"type": "disabled"}` for the `intent` component — a
19-way classification against a fixed taxonomy does not need reasoning tokens, and Sonnet 5
accepts `disabled` at any effort level. Raising `max_tokens` to ~2000 is the alternative
and costs more per request. Separately, the gateway never inspects `stop_reason`; a
truncated response is a distinct outcome from success and should be recorded
(`llm_calls_total{outcome="truncated"}` keeps cardinality bounded).

**Acceptance.** Given a fake response carrying `stop_reason: "max_tokens"` and truncated
JSON, when `recognize()` runs, then the truncation is counted and logged distinctly from a
parse failure on well-formed output.

---

### F8 — Nothing imposes a deadline

**Priority P1. Scheduled into ISSUE-008 FR5** -- `/chat` fans out to four calls.

There is no `asyncio.timeout` in `recognize()` and none in the gateway, so every call
inherits the SDK default of **ten minutes**. A hung call holds its request for that long.
The semaphore makes it worse: `llm_max_concurrency` defaults to 8
(`owl_mind/core/config.py:48`), it is per-process, and it is held across the whole
`messages.create` — so eight slow calls block every component in the process.

This is tolerable at one model call per request. ISSUE-005 makes it up to four (intent,
two agents, composer) fanned out under `gather`, at which point the worst case is a
multiple of a number nobody chose.

**Proposed fix.** A per-component deadline in the gateway, configurable, defaulting to
something an interactive study assistant can actually wait for. Do it while there is one
call site to change rather than five.

**Acceptance.** Given a client that never returns, when a call runs, then it raises a
timeout within the configured deadline and `llm_calls_total{outcome="error"}` increments.

---

### F10 — Failed calls record no tokens

**Priority P2.**

`_record_error` (`owl_mind/core/llm_gateway.py:275`) increments the call counter and the
latency histogram but no token counters, because the exception carries no usage object.
ISSUE-005 FR6 drops a failed *supporting* agent and continues, so a flaky agent produces
spend that `/metrics` **structurally cannot see**.

The gateway's headline claim is that every token is attributable to a component. That claim
currently holds for calls that succeed. Given how much of the design rests on it, the gap
should be a stated limitation rather than something a reader discovers.

**Proposed fix.** Decide between recording a pre-flight estimate from
`messages.count_tokens` and documenting the gap explicitly in the module docstring and
`README`. Either is defensible; silence is not.

---

## Tier B — numbers that do not mean what they say

### F5 — Fused confidence launders weak evidence

**Priority P2.**

`_fuse` divides by the weight that actually voted. That is the right call and the
docstring defends it correctly — the alternative turned every model outage into `OTHER`.
The mirror-image failure is unrecorded: with the model and index both unavailable, a
single regex match reports **0.95**, indistinguishable downstream from three signals
agreeing at 0.95.

`source_scores` makes it *reconstructible*, but nothing carries how much of the panel
voted, so routing, degradation, and the composer cannot tell the two apart without
re-deriving it.

**Proposed fix.** Carry the divisor (the contributed weight) on `Intent`. It is now the
natural place — the object already carries `fallback_from` and `corroborated`, so
provenance is an established pattern there rather than a new one.

**Acceptance.** Given only the pattern signal contributes, when the Intent is inspected,
then the contributed weight reads 0.15 and a consumer can distinguish it from a
full-panel result at the same confidence.

---

### F11 — Composite routing rests entirely on the model

**Priority P2. Feeds ISSUE-004.**

At weight 0.15 the pattern signal **can never** promote a supporting agent. Its ceiling is
`0.15 / (0.5 + 0.15) = 0.2308` when the model also votes, and `0.15` when all three do,
against a `SUPPORTING_FLOOR` of `0.25`. Promotion would need its weight raised to ≈0.217.

So if the model returns one intent for *"explain BFS then quiz me"*, the
`\b(quiz|test)\s+me\b` rule written for exactly that phrase cannot rescue the second agent.
The `WEIGHTS` docstring anticipates the pattern rule "pulling the second intent closer to
first place" — it does, but never over the floor.

The uncomfortable part: **ISSUE-005's five composite tests will pass regardless**, because
they stub the model returning both intents. The suite cannot see this gap, which is the
same shape of failure plan §11.2 warns about.

Pinned by `test_the_pattern_signal_cannot_reach_the_supporting_floor_alone`, so the
arithmetic is a test rather than a comment.

**Proposed fix.** None here — this is input to ISSUE-004's tuning, which should measure
composite recall against real model behaviour before the weights are called settled.

---

### F9 — The prompt-caching comment promises something that cannot happen

**Priority P3.**

`_SYSTEM_PROMPT` is ordered correctly for a cacheable prefix — byte-stable instructions
first, the volatile message in the user turn — and the comment says that ordering "is what
makes the prefix cacheable later". It renders to 2851 characters, roughly **770 tokens**,
under Sonnet 5's **1024-token** minimum cacheable prefix. Below the minimum, caching
silently does nothing: no error, `cache_creation_input_tokens: 0`.

So the `cache_read` direction plumbed through `TokenUsage` reads zero forever on this
component, and whoever verifies caching later will reasonably conclude the gateway is
broken.

**Proposed fix.** Reword the comment to name the threshold. Worth noting alongside it that
Claude Opus 5 halves the minimum to 512 tokens — a model choice flips this without any code
change, which is the sort of thing that should be written down rather than rediscovered.

---

## Tier C — housekeeping that will bite the next issue

### H1 — `/health` reports a hardcoded empty agent list

`registered_agents()` in `owl_mind/api/main.py:87` returns `[]` and duplicates
`AgentOrchestrator.registered_agents()` (`owl_mind/agents/orchestrator.py:73`). ISSUE-005
FR7 adds a startup contract asserting every `AgentType` has a live instance — at which
point `/health` reporting `[]` while a contract just verified five agents is a
contradiction someone will hit during the demo. Wire it in the same PR.

### H2 — Startup contracts register by import side effect

`owl_mind/api/main.py:28` imports `intent_recognizer` purely so the taxonomy contract
registers, with a good comment explaining why. It is fragile as contracts multiply, and
ISSUE-005 FR7 adds two more from `agents/`. The deeper problem: **a contract that was never
imported is indistinguishable from a contract that passed.** `verify_startup_contracts()`
logs "N registered" and nothing asserts what N should be. An explicit `contracts.load_all()`,
or a test pinning the expected count, closes it.

### H6 — `tool_scope` names drifted from the plan's tool table

ISSUE-005 declared `tool_scope` early so the tools issue would "populate a declared scope
rather than invent one". Four of the eight entries do not match plan §3.3:
`check_step` and `get_progress` are invented, `generate_quiz` should be
`generate_quiz_spec` (a spec, not the questions), and `search_materials` is an **MCP**
tool that does not belong in an in-process scope at all. Recorded here because the FR7
contract is dormant while the registry is empty, so nothing currently catches it.

**Fixed by ISSUE-007 FR1**, as its first commit. Listed anyway: if that issue is
descoped, the drift outlives it.

### H3 — `MAX_AGENTS` is a routing constant living in the classifier

`owl_mind/core/intent_recognizer.py:261`. `supporting_candidates()` does not apply it;
ISSUE-005 applies it elsewhere. Routing policy leaking into the classifier module, and a
cap that conceptually lives in two files.

### H4 — `MemoryContext.user_profile` defaults to `None` behind a `type: ignore`

`owl_mind/memory/conversation_memory.py:31`. A dataclass field typed `dict[str, Any]` with
a `None` default and the type error suppressed. Trivial now; a `NoneType has no attribute`
once the memory issue populates it.

### H5 — ISSUE-004 cites weights that no longer exist

Its Background quotes `WEIGHTS` as `{"llm": 0.7, "embedding": 0.2, "pattern": 0.1}` — the
values are now `0.5 / 0.35 / 0.15` — and its **FR1 (renormalise over the signals that ran)
already shipped** in ISSUE-003's `_fuse`. Left alone, someone implements FR1 a second time
and re-baselines against a change that is already in. Amend ISSUE-004 to mark FR1 done and
correct the numbers.

---

## Out of Scope

- **Anything already resolved.** See the register below; do not re-open those.
- **Weight and threshold tuning.** `WEIGHTS`, `PRIMARY_THRESHOLD`, `SUPPORTING_FLOOR` belong
  to ISSUE-004 against a held-out split. F11 is input to that, not a fix here.
- **A cheaper or different model for the intent component.** Interacts with F2 and F9 and
  changes both their arithmetic; measure it on its own or neither effect is attributable.
- **Wiring a real Chroma index.** The embedding signal returns `{}` in production today
  because nothing constructs `ChromaTemplateIndex` outside tests. That is a missing feature
  with its own issue, not a defect in the code that exists.

---

## Risks

| Risk | Mitigation |
| --- | --- |
| The register becomes a graveyard nobody reads | Tier A is small and blocks a named issue; if an item is not worth scheduling it should be closed as accepted, not left open |
| F2 is "fixed" by raising `max_tokens` without measuring | The acceptance criterion asks for a distinct counter, so the fix is observable rather than assumed |
| Items are fixed in one PR and reviewed as a batch | One branch per item is in Project Fields for this reason — a batched diff hides which change caused which behavioural shift |
| F11 is read as a bug and someone raises the pattern weight to make it promote | It is deliberately marked "no fix here" — the weights are a measured decision in ISSUE-004, and hand-tuning one to clear a threshold is how a tuned system becomes untunable |

---

## Definition of Done

- [ ] F2 fixed and covered by a test that exercises the truncation path.
- [ ] F8 fixed; a per-component deadline exists and is configurable.
- [ ] F10 resolved either way, with the decision written down where a reader will find it.
- [ ] F5 resolved or consciously accepted.
- [ ] F9 comment corrected.
- [ ] F11 carried into ISSUE-004 as a named input.
- [ ] H1–H5 fixed or closed as accepted.
- [ ] Every item above is checked or has a one-line reason it was reclassified.

---

## Resolved before ISSUE-005 — do not re-open

Recorded so the fixes are not undone by someone reading an older draft of the plan.

| Was | Resolution |
| --- | --- |
| **F1** — `CRITICAL` urgency short-circuited to the human handoff, so *"explain BFS today"*, *"…tonight"*, and *"due tomorrow"* all escalated instead of being answered | ISSUE-005 FR2 amended to key on `IntentCategory.HUMAN_TUTOR`. `compute_urgency` unchanged — urgency stays a scheduling signal |
| **F3** — entity extraction matched substrings: "al**go**rithm" → Go, "f**rust**rated" → Rust, "**java**script" → java, "de**queue**" → queue | `_compile_terms()` builds `\b`-anchored patterns sorted longest-first, with optional plural suffix; `"binary search tree"` added, since `\b` alone still matched inside it |
| **F4** — an intent the model never returned crossed the supporting floor at cosine similarity 0.607, buying an agent call plus a composer call | `Intent.corroborated`; promotion now requires the model or a pattern rule to have returned the intent |
| **F6** — the low-confidence path emitted `category=OTHER` with a *different* intent's confidence and all-zero `source_scores` | `fallback_from` records what was displaced; `confidence` describes `category`; `source_scores` describes the displaced candidate |
| **F7** — `OTHER` routed through `SUPPORT` to ConceptAgent | ISSUE-005 FR2a: ambiguous asks a clarifying question, off-topic declines, neither calls a model |
