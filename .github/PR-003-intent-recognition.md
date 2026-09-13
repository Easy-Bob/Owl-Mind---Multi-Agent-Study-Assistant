# [Issue #5] Intent recognition -- three-way fusion over a 19-intent taxonomy

## Summary

Implements `core/intent_recognizer.py`: a student utterance in, a structured judgement
out -- category, group, confidence, the full fused distribution, per-source scores,
urgency, and extracted entities.

Three independent signals (model `0.7`, embedding `0.2`, pattern `0.1`) are fused by
**summing across every candidate each signal returned**, not by voting between three
argmaxes. That one change is what makes "one message, several agents" decidable from the
intent result instead of by keyword matching downstream.

Also registers the project's first startup contract, filling the hook ISSUE-001 built and
left empty.

## Linked Issue

Closes #5

<!-- Issue number follows the established pattern: scaffold issue #1 / PR #2, gateway
     issue #3 / PR #4. Correct this line if the issue lands elsewhere. -->

## Type of Change

- [x] Feature
- [x] Bug fix
- [ ] Chore
- [x] Documentation
- [x] Refactor
- [x] Test

## What Changed

**The recognizer** (`owl_mind/core/intent_recognizer.py`, 659 lines)

- **19 intents in 5 groups.** `IntentCategory`, `IntentGroup`, `UrgencyLevel` as
  `StrEnum`, so they serialise into logs and metrics without conversion.
- **Fusion produces a distribution.** `_fuse` sums `weight * score` over every candidate
  each signal proposed. The reference implementation collapsed each signal to one label
  *before* voting, so its score map held at most three entries -- the strongest signal
  read the second request in a composite message and then threw it away.
- **The model and embedding signals run concurrently** (`asyncio.gather`); patterns run
  synchronously because they are a regex sweep. The model call is on the critical path,
  so the embedding lookup runs beside it rather than behind it.
- **Both remote signals degrade instead of failing.** A Chroma outage or a model error
  returns `{}` and logs a warning; the remaining signals carry the request and
  `source_scores` records the absence, so a degraded route is still explainable after the
  fact. A classifier that 500s takes the whole product down; one that drops to two
  signals does not.
- **Below `PRIMARY_THRESHOLD` (0.35) the primary falls back to `other`.** A confidently
  wrong route is worse than an admitted one.
- `Intent` is a frozen dataclass. `category` stays single-valued -- routing needs one
  agent to own the response and the composer needs a spine to merge onto.
  `supporting_candidates()` reads `scores` against an **absolute floor** (`0.25`), sorted
  score-descending then name-ascending.
- **Entities** (`topic`, `problem_id`, `language`, `complexity_class`, `due_date`) are
  rule-based; a rule that does not match returns absent, never a guess.
- **Urgency** covers the human-tutor and deadline triggers. The third-failed-hint trigger
  is **deliberately deferred** with a named TODO -- it needs working memory, and
  `MemoryManager` is still a stub. Shipping a partial rule silently would be worse than
  shipping it late.

**The first startup contract** (FR10)

`_check_taxonomy_is_complete` asserts every `IntentCategory` member appears in both
`_TEMPLATES` and `_INTENT_GROUPS`. Adding an intent without a template **aborts the
boot** rather than degrading at request time. `api/main.py` imports the module for the
registration side effect (`# noqa: F401`), and `/health` now lists one contract.

This earned its keep during the ASSESS expansion below: it caught three missing template
sets mid-edit, before any test ran.

**Three defects from the reference implementation, not ported**

1. **The error-code pattern.** `\b([45]\d{2})\b` matches any three-digit number -- prices,
   counts, line numbers -- and labels it `error_code`. It sells structured entities as
   routing input and delivers noise. There is a regression test asserting three-digit
   numbers produce no entity.
2. **`1.0 - distance` assuming cosine.** Chroma defaults to **squared L2**, where that
   expression is not a similarity and can go negative. `ChromaTemplateIndex.seed()`
   creates the collection with `metadata={"hnsw:space": "cosine"}` and `search()` clamps
   to `[0, 1]` anyway. A test asserts the range.
3. **The relative supporting gate** (`score >= 0.55 * primary`). On a real two-domain
   message it left the second agent qualifying by a hair, so one fewer keyword hit
   silently dropped half of what the student asked for. With a hard cap of 3 agents, the
   cap does the limiting and the relative gate only adds a fragile edge.

**Three more ASSESS intents** (16 -> 19)

ASSESS held only `quiz_request`, which made it a poor attractor -- assessment phrasings
had nowhere natural to land and drifted into PRACTICE and SUPPORT.

- `answer_submission` -- **the real gap.** The quiz loop is two turns and only the
  generate turn had an intent; a student replying "my answer is a queue" fell to `other`
  or `homework_check`. Its boundary against `homework_check` is *who posed the question*:
  an external artifact versus an answer to a rubric the agent already pinned.
- `mock_exam` -- longer, mixed-topic, timed. A different tool shape, not a bigger `count`.
- `explain_back` -- the student explains a concept to be checked.

All three route to QuizAgent, so the roster is unchanged.

**A tiebreak bug I introduced and then found** (see Decisions #2)

## Testing

- [x] `pytest` -- **73 passed, 1 skipped, 1 deselected.** Zero network calls (FR11); the
      gateway and the template index are both faked.
- [x] `ruff check .` -- clean.
- [x] Fan-out verified end to end on a composite message -- output below.
- [x] Tie determinism verified by running the same scores in both orders, and the test
      was confirmed to **fail against the previous implementation** before the fix landed.
- [x] Startup contract verified by deliberate violation -- output below.
- [x] Entity extraction verified against the error-code regression case.
- [x] Degraded paths verified: index raising, model raising, both empty.
- [ ] `pytest -m live` -- **still not run.** Unchanged from the gateway PR; `.env` holds
      the placeholder key.
- [ ] **No accuracy measurement.** See Risk.

## Screenshots / Evidence

One message, two agents, decided from the intent result:

```text
primary   : concept_explain 0.63
group     : learn
scores    : {'concept_explain': 0.63, 'quiz_request': 0.545}
supporting: ['quiz_request']
sources   : {'llm': 0.7, 'embedding': 0.7, 'pattern': 0.0}
urgency   : low
entities  : {'topic': 'bfs'}
```

`quiz_request` at `0.545` clears the `0.25` floor on its own evidence. Under the
reference implementation's shape this message produces a single label and the "quiz me"
half is lost.

The startup contract, with one template set deleted at runtime:

```text
ContractViolation: 1 startup contract(s) violated:
  - every IntentCategory has templates and a group: no templates: ['mock_exam']
```

Entities -- note the first line:

```text
'my code throws a 404 on line 500'       -> {}
'I scored 450 on problem 1234 in python' -> {'problem_id': '1234', 'language': 'python'}
'explain O(n log n) for quicksort'       -> {'topic': 'quicksort', 'complexity_class': 'O(nlogn)'}
```

The tiebreak test against the pre-fix argmax, then against the fix:

```text
FAILED tests/test_intent_recognizer.py::test_primary_tie_breaks_by_name_not_dict_order
--- restored ---
.                                                                        [100%]
```

Clean state:

```text
73 passed, 1 skipped, 1 deselected, 2 warnings
All checks passed!            # ruff
```

## Deployment Notes

- New environment variables: **No.**
- New services or volumes: No. Chroma already ships in the compose file; this is the
  first code to use it.
- Requires image rebuild: Yes (source changed).
- **The template collection must be seeded.** `ChromaTemplateIndex.seed()` is not called
  automatically -- nothing constructs the recognizer in the request path yet, so there is
  no live consumer to seed for. When routing lands, seeding belongs in the lifespan.
  Until then the embedding signal is inert in a real deployment and the fusion runs on
  two signals.
- New endpoints: none. `/health` now lists one contract.

## Risk / Rollback

- **Risk: the weights and thresholds are untuned.** `0.7 / 0.2 / 0.1`, `0.35`, `0.25`
  are starting points chosen to be reasonable, not values derived from data. There is a
  comment at the constants saying exactly that. **This is the largest open risk in the
  PR** -- the supporting floor in particular decides how often a request fans out to
  three agents, which is directly a cost multiplier.
- **Risk: no accuracy number exists.** Every test here asserts *mechanism* -- that fusion
  sums, that ties are stable, that a degraded signal does not crash. None asserts the
  classifier is correct, because there is no labelled corpus yet. Mechanism tests catch
  regressions; they do not tell you the taxonomy works. The eval issue is where that
  gets answered.
- **Risk: 19 classes is a lot for 3 templates each.** More classes makes the group a
  better attractor *and* makes the boundaries harder. `explain_back` versus
  `answer_submission` is the pair I expect to confuse -- same shape, differing only in
  whether a rubric was pinned in advance. If the corpus shows they mix, merge them.
- **Risk: low for behaviour.** Nothing calls the recognizer in the request path yet.
- **Rollback:** revert the merge. The only cross-cutting change is the contract import in
  `api/main.py`.

## Decisions for the reviewer

**1. Fusion sums a distribution; the primary is still one label.** These sound like they
conflict. They do not: `category` is single-valued because one agent has to own the
response, while `scores` keeps everything so routing can pick supporting agents on
evidence. The reference implementation had neither -- it had one label and a three-entry
map, and detected composite requests downstream by keyword matching on the *lowest*
weighted signal.

**2. I introduced a tiebreak bug and caught it while writing this PR.** The argmax read
`key=(score, -ord(name[0]))`, comparing only the first character. Four intents start with
`c`, three with `m` -- on a tie between `concept_explain` and `concept_compare` the winner
fell out of dict insertion order, which flips with the order the signals happened to
return. FR6.1 requires the tie order to be fixed, and `supporting_candidates()` already
did it correctly, so primary and supporting selection could disagree about the same tie.
Now `min(scores, key=lambda i: (-scores[i], i.value))` -- the same order in both places.
The test was confirmed to fail against the old code first.

**3. The embedding signal is inert until routing lands.** `seed()` exists and is tested
against a fake, but nothing calls it, so in a real deployment fusion currently runs on
two signals at `0.7 + 0.1`. This is honest rather than hidden: seeding needs an owner in
the lifespan and that owner arrives with the routing issue. Flagging it because "the
embedding signal is implemented" and "the embedding signal is running" are different
claims and I do not want the first read as the second.

**4. Weights are constants, not configuration.** Making them environment variables would
invite tuning them in production against a metric nobody is computing. They move when the
eval corpus says they should move, in a commit with a number attached.

**5. The urgency rule is deliberately incomplete.** Three of four triggers ship; the
third-failed-hint trigger needs `MemoryManager`. The alternative -- approximating it from
the current message -- would produce a rule that looks implemented and fires on the wrong
thing.

**6. Four intents have no owning agent.** `motivation`, `greeting`, `feedback`, and
`other` are in the SUPPORT group with nothing to route to. They are real utterance
classes and the classifier should name them rather than force them into a learning
intent, but routing will need an answer -- most likely a thin default responder. Raising
it here so it is a decision and not a surprise.

## Follow-ups (not in this PR)

- **The eval corpus.** The single highest-value next step for this module. Per-class
  precision and recall would settle the weights, the two thresholds, whether
  `explain_back` and `answer_submission` are distinct, and whether 19 classes is too many
  -- none of which is answerable by argument.
- **Routing** (the next issue) consumes `scores` and `supporting_candidates()` under the
  3-agent cap, and owns lifespan seeding of the template collection.
- **Prompt caching on the intent component.** `intent` fires on every single request and
  its system prompt carries all 19 intent descriptions -- a large, perfectly stable
  prefix. This is the cheapest cost win available, and `llm_tokens_total`'s `cache_read`
  direction is already in place to prove it landed.
- **A cheaper model for `intent`.** Also the only component whose quality can be A/B'd
  objectively -- against labelled accuracy rather than a judge -- which makes it the right
  place to start tiering.
