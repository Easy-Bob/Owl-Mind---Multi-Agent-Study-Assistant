# [Issue #3] LLM gateway -- token accounting, backpressure, and metrics

## Summary

Implements `core/llm_gateway.py`: the single instrumented entry point for every
Anthropic call. Per-component token attribution, a concurrency ceiling, Prometheus
counters on a new `/metrics` endpoint, and a per-request rollup that survives parallel
dispatch.

Also removes `temperature` from `AgentProfile`. The current models reject it with a 400,
so the plan's per-agent values could never have run -- see "The defect" below.

## Linked Issue

Closes #3

<!-- Issue number is a guess: the scaffold PR took #2. Correct this line and the branch
     name if the issue lands on a different number. -->

## Type of Change

- [x] Feature
- [ ] Bug fix
- [ ] Chore
- [x] Documentation
- [ ] Refactor
- [x] Test

## What Changed

**The gateway** (`owl_mind/core/llm_gateway.py`)

- `complete(component=...)` requires a label from `KNOWN_COMPONENTS` and raises
  `UnknownComponent` **before any network call**. An unlabelled call is cost nobody can
  attribute, which is the exact failure this issue exists to prevent.
- Captures all four usage fields -- input, output, cache read, cache creation -- each
  read with `getattr(..., 0)`. A provider that omits the cache fields must not fail a
  request over a metric.
- `asyncio.Semaphore` from `settings.llm_max_concurrency`, held for the API call only.
- Prometheus `llm_tokens_total{component,model,direction}`,
  `llm_calls_total{component,model,outcome}`, `llm_latency_ms{component}`. The `outcome`
  label matters: a counter that only counts successes hides the failure rate, which is
  the number you want during an incident.
- `request_scope()` accumulates usage across one request via `contextvars`.
- `model` is overridable per call, so per-component tiering lands later as configuration
  rather than a refactor.

**Deliberately not included**, each for a stated reason in the module docstring: no retry
(the SDK already retries 408/409/429/5xx -- a second layer multiplies the wall-clock
timeout), no prompt construction (`cache_control` placement belongs to whoever builds the
prompt), no sampling parameters, no domain knowledge.

**The defect** (FR7)

`temperature`, `top_p`, and `top_k` are rejected with a 400 on Claude Sonnet 5 and
Opus 5. The scaffold declared `temperature: float = 0.3` on `AgentProfile` and the plan
assigned one per agent (Concept 0.4, Practice 0.1, Planner 0.0, Quiz 0.0). Every one of
those requests would have failed on first contact.

The guarantees behind those numbers are unaffected, because they never came from
sampling -- `schedule_review` is SM-2 arithmetic and `grade_answer` decomposes a holistic
judgement into per-rubric-point checks plus arithmetic in code. Replaced with `effort`,
set to **the same value for every role** (see Decisions below).

A new guard rail fails if `temperature`, `top_p`, or `top_k` is assigned anywhere under
`owl_mind/`.

**Supporting changes**

- `api/main.py` -- `/metrics` endpoint; gateway built once in the lifespan and closed on
  shutdown.
- `tests/fakes.py` -- `FakeAnthropic`, which also tracks peak in-flight calls (that is how
  the semaphore ceiling is verified).
- `pyproject.toml` -- `live` marker registered and deselected by default.
- `README.md`, `STUDY-ASSISTANT-PLAN.md` -- documented; temperature column removed.

## Testing

- [x] `pytest` -- 36 passed, 1 deselected. **Zero network calls** (FR8).
- [x] `ruff check .` -- clean.
- [x] Concurrency ceiling verified: 10 concurrent calls at `llm_max_concurrency=2`, peak
      in-flight never exceeded 2, all 10 completed.
- [x] Rollup verified **under `asyncio.gather` specifically** -- see Decisions.
- [x] Token capture verified with all four fields, with cache fields absent, and with no
      `usage` object at all.
- [x] Error path verified: `RateLimitError` increments `outcome="error"`, logs the
      component, propagates unchanged, and does **not** count toward the rollup.
- [x] Guard rails verified by deliberate violation, then reverted.
- [x] `/metrics` scraped end to end -- output below.
- [ ] `pytest -m live` -- **NOT RUN.** The local `.env` holds the placeholder key written
      during the scaffold. Needs a real key; one call at `max_tokens=16`, a fraction of a
      cent.

## Screenshots / Evidence

Three fake calls, two of them through `asyncio.gather`, then a real `/metrics` scrape:

```text
rollup: {'llm_calls': 3, 'tokens_in': 4200, 'tokens_out': 780,
         'tokens_by_component': {'agent:concept': 2684, 'agent:quiz': 2684, 'composer': 2684}}
```

```text
llm_tokens_total{component="agent:concept",direction="input",model="claude-sonnet-5"} 1400.0
llm_tokens_total{component="agent:concept",direction="output",model="claude-sonnet-5"} 260.0
llm_tokens_total{component="agent:concept",direction="cache_read",model="claude-sonnet-5"} 1024.0
llm_tokens_total{component="agent:quiz",direction="input",model="claude-sonnet-5"} 1400.0
llm_tokens_total{component="composer",direction="output",model="claude-sonnet-5"} 260.0
llm_calls_total{component="agent:concept",model="claude-sonnet-5",outcome="success"} 1.0
llm_calls_total{component="agent:quiz",model="claude-sonnet-5",outcome="success"} 1.0
llm_calls_total{component="composer",model="claude-sonnet-5",outcome="success"} 1.0
```

Guard rails, with `client.messages.create(model="x", temperature=0.3)` temporarily
injected into an unrelated module -- both fired, then the probe was reverted:

```text
FAILED tests/test_guardrails.py::test_anthropic_is_called_only_through_the_gateway
FAILED tests/test_guardrails.py::test_no_sampling_parameters
2 failed, 5 passed
```

Clean state:

```text
36 passed, 1 deselected, 2 warnings
All checks passed!            # ruff
```

## Deployment Notes

- New environment variables: **No.** `LLM_MAX_CONCURRENCY` already shipped in
  `.env.example`; this PR is the first code to read it.
- New services or volumes: No.
- Requires image rebuild: Yes (source changed).
- New endpoint: `GET /metrics`.

## Risk / Rollback

- **Risk:** low for behaviour -- nothing calls the gateway yet except tests, so no
  request path changes.
- **Risk:** `/metrics` is **unauthenticated**. Acceptable locally; it publishes usage
  volume, so it needs a decision before this is deployed anywhere reachable.
- **Risk:** metric cardinality. `component` and `model` are closed sets by construction.
  Adding a user id or session id as a label would turn a counter into an unbounded series
  and take the scrape down -- there is a comment at the metric definitions saying so.
- **Rollback:** revert the merge. `AgentProfile.temperature` would come back, but nothing
  consumes it yet.

## Decisions for the reviewer

**1. This branch contains a change that does not belong to the issue.** The second commit
adds plan section 3.1.1 (keeping both QuizAgent and GradingAgent, scoping grading to v2).
That is a roster decision, not part of the gateway. SOP section 18.1 explicitly asks
reviewers to catch this. It is documentation-only and zero-risk, but say the word and I
will split it onto its own branch.

**2. `effort` is the same value for every role, on purpose.** Effort is a cost/quality
dial; temperature was a randomness dial. There is no mapping between them, so
transcribing "Concept 0.4, Planner 0.0" into effort levels would invent numbers that look
considered and are not. The field exists so the knob is reachable; tuning belongs to the
evaluation issue, where a corpus can settle it.

**3. A claim in the plan was corrected.** The plan previously implied `grade_answer` makes
grading deterministic. For free-text answers it cannot -- deciding whether a rubric point
is present is model judgement. What the rubric actually buys is *decomposition*: one
holistic score becomes several binary checks plus arithmetic in code, which is far more
stable but not deterministic. The plan now says so. This matters because it is exactly
the reason GradingAgent needs an instructor-authored rubric rather than a model-authored
one.

**4. The gather test is the one not to skip.** If the contextvar stops propagating into
gathered tasks, token totals are simply low and **nothing raises**. Parallel agent
dispatch is the whole point of the routing layer, so a sequential-only test would pass
while the headline numbers were quietly wrong. There is a comment at the contextvar
explaining why the current shape works (tasks copy the binding, not the object).

**5. `pytest -m live` has not been run** -- see Testing. The reviewer or I should run it
once with a real key and attach the token counts before merge.

## Follow-ups (not in this PR)

- **Prompt caching**, once agents build real prompts. The role contract, injected skill,
  and tool definitions form a stable prefix on every request; cached reads cost roughly a
  tenth of input price and break even at two requests. Likely the cheapest cost lever
  available -- and the `cache_read` field already in the counters is what will prove it
  worked.
- **Per-component model tiering**, once `/metrics` has real data. `intent` fires on every
  request and is the obvious first candidate; it is also the only component whose quality
  can be A/B'd objectively, against labelled intent accuracy rather than a judge.
- **Per-role effort values**, once the eval corpus can justify them. `agent:planner`
  mostly narrates a schedule the tool already computed and is the likely candidate to run
  cheaper -- a hypothesis, not a conclusion.
