# [Task] Intent Recognition — Cascade vs Parallel Fusion: accuracy and token A/B

> Template: `owl_mind_dev_workflow.md` §7. Scope source: `STUDY-ASSISTANT-PLAN.md` §3.2, §11.2.
> Depends on: ISSUE-003 (intent recognition, merged), and the evaluation issue
> (`EndToEndEvaluator` must be real before this can produce a number).

## Project Fields

| Field | Value |
| --- | --- |
| Type | Task |
| Priority | P2 — cost optimisation, not a correctness gap |
| Area | Core / Domain, Evaluation |
| Status | Blocked on the evaluation issue |
| Milestone | After Day 4 — Evaluation |
| Owner | TBD |
| Reviewer | TBD |
| Branch | TBD |

---

## Summary

Add a **cascade** ordering to `IntentRecognizer` — pattern, then embedding, then the
model call only if the two free signals have not already settled the utterance — behind
a config switch, so it runs alongside the existing parallel fusion rather than replacing
it. Then measure both orderings on the same labelled corpus and publish one table:
**accuracy, composite-intent recall, p50/p95 latency, and tokens per 1000 requests**,
per ordering.

The deliverable of this issue is **the number**, not the optimisation. Which ordering
ships is decided by the table, not in advance.

---

## Background / Business Context

Today the three signals are not a sequence. `_pattern_signal` is synchronous and free;
`_llm_signal` and `_embedding_signal` run concurrently under `asyncio.gather`, and
`_fuse` sums all three. Every request pays for a model call, including "hi" and "quiz me
on hash tables", which the pattern rules already answer at 0.95 confidence.

The proposal is to invert that into an early-exit cascade: run the free signals first,
and skip the model call when they agree strongly enough. Patterns and the local
embedding model cost no API tokens, so every skipped call is a direct saving on the
highest-volume component in the system.

Two things make this more than a reordering, and both are why this is an issue rather
than a patch:

**The current weights cannot express an early exit.** `WEIGHTS` is
`{"llm": 0.7, "embedding": 0.2, "pattern": 0.1}` and `PRIMARY_THRESHOLD` is `0.35`. A
perfect pattern hit scores `0.1 * 0.95 = 0.095`. Pattern and embedding both saturated
reach `0.3`. Both are below the primary threshold, so a cascade that stopped there would
classify every early-exit utterance as `OTHER`. The fusion has to renormalise over the
signals that actually ran before a cascade is even representable (FR1). This same bug is
live today in a smaller form: when `index is None` the embedding signal returns `{}` and
0.2 of the weight mass silently vanishes, depressing every score by up to 20%.

**Early exit trades away the composite-intent guarantee.** ISSUE-003's acceptance
criteria include: given "explain BFS and then quiz me on it", `category` is
`concept_explain` and `scores` carries `quiz_request` above the supporting floor. The
pattern signal sees only `quiz me` there. An unguarded pattern-first exit returns
`quiz_request` as primary with no supporting intent — precisely the failure mode the
distribution-based fusion was built to remove. The exit gate (FR3) is therefore the
load-bearing part of the design, not the plumbing.

---

## User Story

As the person paying for this system,
I want the cheapest ordering that does not cost accuracy,
so that the decision is made from a measured accuracy-per-token table rather than from
an assumption about which signal is expensive.

---

## Functional Requirements

### FR1 — Fusion renormalises over the signals that ran

`_fuse` divides the weighted sum by the total weight of the signals that returned a
non-empty score map, so a score is always on `[0, 1]` regardless of how many signals
contributed. `PRIMARY_THRESHOLD` and `SUPPORTING_FLOOR` then mean the same thing in both
orderings and in the degraded path.

This is a behaviour change to the existing parallel mode as well: it removes the silent
20% depression when the embedding index is absent. Existing tests that assert absolute
score values will need updating, and the change must be reviewable on its own — land it
as the first commit, with the parallel-mode numbers re-baselined, before the cascade
exists.

### FR2 — Both orderings live at once

`Settings` gains `intent_fusion_mode: Literal["parallel", "cascade"] = "parallel"`.
`IntentRecognizer` reads it once at construction. The default stays `parallel`, so
merging this issue changes no production behaviour; the cascade is opt-in until the
table says otherwise.

### FR3 — The cascade exit gate is conservative and explicit

In `cascade` mode:

1. Run the pattern signal.
2. Run the embedding signal.
3. Exit early **only if all** of the following hold; otherwise fall through to the model
   call and fuse all three as today:
   - the renormalised top score is at or above `CASCADE_EXIT_THRESHOLD` (start at 0.85,
     tuned by the same held-out split as `WEIGHTS`);
   - the runner-up is below `SUPPORTING_FLOOR` — no second intent is in play;
   - exactly one pattern rule matched, if any matched at all;
   - the message contains no coordinating marker (`and then`, `also`, `after that`,
     `;`) and is at most one sentence.

   The last two conditions exist to protect the composite case. They are deliberately
   blunt: the cost of a wrong early exit is a wrong route, the cost of a missed early
   exit is one model call.
4. On early exit, `source_scores["llm"]` is `0.0` and a new field records that the model
   never ran, so a route can still be explained after the fact.

### FR4 — Per-mode cost accounting

The gateway already counts tokens per `component`. Extend the intent path so a run can
be attributed to its fusion mode:

- count of requests that exited early vs. reached the model, per mode;
- input, output, and cache-read tokens per 1000 requests, per mode;
- p50 and p95 wall-clock latency, per mode, split by early-exit and full path.

Label cardinality stays bounded — `mode` is a closed set of two. The existing rule holds:
no user id, session id, or free text as a label.

### FR5 — The comparison report

`EndToEndEvaluator` gains a mode that runs the full labelled corpus twice, once per
ordering, against the same fixtures and the same pinned baseline, and emits one table:

| Mode | Accuracy | Composite recall | Early-exit rate | p50 / p95 ms | Tokens / 1k req | $ / 1k req |
| --- | --- | --- | --- | --- | --- | --- |
| parallel | | | n/a | | | |
| cascade | | | | | | |

"Composite recall" is the fraction of multi-intent cases where the supporting intent
still lands above `SUPPORTING_FLOOR` — the metric the early-exit gate puts at risk, and
the one a single accuracy number would hide.

The report is committed as an artefact, not just printed, so the next person to ask this
question reads the answer instead of re-running it.

### FR6 — Token counts are measured, not estimated

Token figures come from `usage` on real responses or from `messages.count_tokens`, never
from a characters-divided-by-four estimate. The current system prompt is roughly 2.9 KB
of text; whether it clears the model's minimum cacheable prefix is a measurement, not a
guess, and FR5's dollar column is wrong if it is guessed.

---

## Acceptance Criteria

Given the corpus is run in `parallel` mode after FR1 lands,
when the results are compared to the pre-FR1 baseline,
then accuracy has not regressed, and the score changes are explained by renormalisation
alone.

Given the message "hi",
when `recognize()` runs in `cascade` mode,
then no model call is made, `category` is `greeting`, and the metrics record an early
exit.

Given the message "explain BFS and then quiz me on it",
when `recognize()` runs in `cascade` mode,
then the model call **is** made, `category` is `concept_explain`, and `scores` still
carries `quiz_request` above the supporting floor — the ISSUE-003 criterion holds
unchanged in both modes.

Given the whole labelled corpus,
when both modes are run,
then composite recall in `cascade` mode is within one percentage point of `parallel`
mode, or the issue reports the gate as too permissive and does not recommend the switch.

Given the embedding index is unavailable,
when `recognize()` runs in `cascade` mode,
then no early exit occurs, the request falls through to the model, and the result is
identical to `parallel` mode with a missing embedding signal.

Given the comparison run completes,
when the report is read,
then every cell of the FR5 table is populated from measured data, and the recommendation
names the ordering to ship and the evidence for it.

---

## Out of Scope

- **Changing the default mode.** This issue produces the table. Flipping the default is a
  follow-up that cites it.
- **Prompt caching on the system prompt** (ISSUE-003 follow-up). It is likely the larger
  lever, because it applies to the requests the cascade cannot skip rather than only to
  the ones it can — but it is an independent change and must be measured separately, or
  neither change's effect is attributable.
- **A cheaper model for the intent component** (ISSUE-003 follow-up). Same reasoning:
  a model swap cuts cost on 100% of calls, an early exit on the early-exit rate only.
  Measure one at a time.
- **Weight tuning.** `CASCADE_EXIT_THRESHOLD` is tuned here because the cascade cannot be
  evaluated without it; `WEIGHTS` and `PRIMARY_THRESHOLD` stay with the evaluation issue.

---

## Risks

| Risk | Mitigation |
| --- | --- |
| The early-exit gate silently drops second intents, and a single accuracy number hides it | Composite recall is a first-class column in FR5, with its own acceptance criterion |
| The measured saving is too small to justify carrying two code paths | That is a valid outcome — report it and delete the cascade. The table is the deliverable |
| FR1 renormalisation changes scores across the board and masks the comparison | FR1 lands first as its own commit with parallel mode re-baselined, so the cascade is compared against a stable baseline |
| Cascade adds embedding latency to the model path, which parallel mode hides | FR4 splits latency by path so the regression is visible rather than averaged away |

---

## Dependencies

- The evaluation issue: `EndToEndEvaluator.run` is a stub today, and with no corpus there
  is no accuracy column.
- A real Chroma client wired into application startup. `ChromaTemplateIndex` is currently
  constructed only in tests, so the embedding signal returns `{}` in production and the
  cascade would have one working free signal instead of two.

---

## Definition of Done

- [ ] FR1 merged as a separate reviewable commit, parallel-mode baseline updated.
- [ ] `intent_fusion_mode` config switch, default `parallel`, both paths tested.
- [ ] Early-exit gate implemented with each FR3 condition covered by a test.
- [ ] Per-mode metrics exposed at `/metrics`, cardinality bounded.
- [ ] Comparison report generated from measured token counts and committed.
- [ ] Report names a recommended ordering and the evidence behind it.
