# [Task] LLM Gateway — Token Accounting, Backpressure, and Metrics

> Template: `owl_mind_dev_workflow.md` §7. Scope source: `STUDY-ASSISTANT-PLAN.md` §3.5, §11.3 (highlight 九).
> Depends on: ISSUE-001 (scaffold), merged.

## Project Fields

| Field | Value |
| --- | --- |
| Type | Task |
| Priority | P1 — every model-calling issue is blocked until this merges |
| Area | Core / Platform |
| Status | Ready |
| Milestone | Day 1 — Foundations |
| Owner | TBD |
| Reviewer | TBD |
| Branch | `feature/3-llm-gateway` |

---

## Summary

Implement `core/llm_gateway.py`: the single permitted entry point for Anthropic API
calls. Captures per-component token usage, enforces a concurrency ceiling, exposes
Prometheus counters on a new `/metrics` endpoint, and rolls usage up per request via
`contextvars`.

Also corrects a model-compatibility defect in the scaffold's `AgentProfile` and in the
plan's agent roster — see FR7, which is the reason this issue is larger than "write one
class."

---

## Background / Business Context

The reference implementation grew nine `client.messages.create` call sites across five
modules. Adding token accounting afterwards meant finding all nine and hoping none was
missed; per-component attribution was impossible without touching every one. Owl Mind
starts with one door, already enforced by `tests/test_guardrails.py`.

This issue is first in the queue because everything else calls through it. Landing it now
means the intent recogniser, the five agents, the composer, and the judge are all
instrumented on the day they are written, rather than retrofitted.

The gateway also unblocks the cost question directly. Until per-component numbers exist,
any decision about model tiering, prompt caching, or offloading work to a local model is a
guess. `llm_tokens_total{component}` turns that into a measurement.

### The defect this issue has to fix

**`AgentProfile.temperature` cannot work on the configured model.** `temperature`,
`top_p`, and `top_k` are **rejected with a 400** on Claude Sonnet 5 and Claude Opus 5. The
scaffold declares `temperature: float = 0.3` on `AgentProfile`
(`owl_mind/agents/base.py:47`), and the plan's §3.1 roster assigns a temperature per agent
(Concept 0.4, Practice 0.1, Planner 0.0, Quiz 0.0). **Every one of those requests would
fail.**

The plan's stated intent behind those numbers — *"grading must be reproducible",
"scheduling is arithmetic, never LLM-guessed"* — is unaffected, because determinism in
this design never came from the sampling temperature. It comes from pulling the exact work
into deterministic tools: `grade_answer` compares against a rubric in code, and
`schedule_review` is SM-2 arithmetic. The model only phrases the result. Nothing about
that guarantee changes.

What replaces temperature as a per-role knob is `output_config.effort`
(`low` / `medium` / `high` / `xhigh` / `max`), which controls reasoning depth and token
spend. The mapping is not one-to-one — effort is a cost/quality dial, not a randomness
dial — so per-role values are a judgement call to be made here and revisited once the eval
corpus exists.

---

## User Story

As an Owl Mind developer,
I want every model call to pass through one instrumented gateway with per-component token
attribution and a concurrency ceiling,
so that cost is a number I can query rather than an estimate, and a burst of composite
requests cannot exhaust the API rate limit.

---

## Functional Requirements

### FR1 — The gateway

- [ ] `LLMGateway.complete(*, component: str, **kwargs) -> Message` is implemented and is
      the only place in the codebase that calls the Anthropic API.
- [ ] One `AsyncAnthropic` client is constructed per gateway instance and reused; the
      gateway is built once in the app lifespan and stored on `app.state`.
- [ ] `component` is required. An empty or unknown label raises `ValueError` before the
      call is made — an unlabelled call is an unattributable cost.
- [ ] Known labels are a module-level constant: `intent`, `agent:concept`,
      `agent:practice`, `agent:planner`, `agent:quiz`, `composer`, `rewrite`, `rerank`,
      `profile`, `compress`, `merge`, `judge`.
- [ ] `model` defaults to `settings.model` and may be overridden per call, so a later
      issue can route cheap components to a cheaper model without touching callers.

### FR2 — Token capture

- [ ] Populate `TokenUsage` from `response.usage` for every call: input, output,
      `cache_creation_input_tokens`, `cache_read_input_tokens`.
- [ ] **Read every field with `getattr(usage, name, 0)`.** Providers and SDK versions omit
      fields; a missing attribute must not take down a request over a metric.
- [ ] Capture `response._request_id` and include it in any error log — it is what support
      needs to trace a failure.

### FR3 — Backpressure

- [ ] An `asyncio.Semaphore` sized by `settings.llm_max_concurrency` wraps every call.
- [ ] The semaphore is held only for the API call itself, not for surrounding bookkeeping.
- [ ] A test proves the ceiling holds: with concurrency 2 and 10 concurrent calls against
      a fake client, peak observed in-flight calls never exceeds 2.

### FR4 — Metrics

- [ ] Prometheus counters, registered once at import:
      `llm_tokens_total{component,model,direction}`,
      `llm_calls_total{component,model,outcome}`,
      `llm_latency_ms{component}` (histogram).
- [ ] `direction` is one of `input`, `output`, `cache_read`, `cache_creation`.
- [ ] `outcome` is `success` or `error` — a counter that only counts successes hides the
      failure rate, which is the number you want during an incident.
- [ ] `GET /metrics` returns `generate_latest()` with the correct content type.
- [ ] Latency is measured with `time.monotonic()`, not `time.time()`.

### FR5 — Per-request rollup

- [ ] A `contextvars.ContextVar` accumulates usage for the duration of one request.
- [ ] `gateway.request_scope()` is an async context manager that resets the var on entry
      and yields a rollup object readable on exit.
- [ ] The rollup exposes `llm_calls`, `tokens_in`, `tokens_out`, and
      `tokens_by_component`.
- [ ] It works correctly under `asyncio.gather` — contextvars propagate into tasks
      created inside the scope, which is what makes this usable for parallel agent
      dispatch. **A test must prove this**, because the failure is silent undercounting.

### FR6 — Error handling

- [ ] Catch the SDK's typed exceptions most-specific-first; never string-match error
      messages.
- [ ] **Do not hand-roll retry.** The SDK already retries 408/409/429/5xx with exponential
      backoff (`max_retries` default 2). A second retry layer multiplies the wall-clock
      timeout.
- [ ] On failure, increment `llm_calls_total{outcome="error"}`, log the component, the
      exception type, and the request id, then re-raise. The gateway observes; it does not
      decide.

### FR7 — Remove temperature

- [ ] Delete the `temperature` field from `AgentProfile` (`owl_mind/agents/base.py:47`).
- [ ] Remove the temperature explanation from the `AgentProfile` and module docstrings in
      `agents/base.py`; state the role contract in terms of tool scope and risk boundary,
      which is where the guarantees actually live.
- [ ] The gateway never sends `temperature`, `top_p`, or `top_k`.
- [ ] **Add a guard-rail test** that fails if any of those three appears anywhere under
      `owl_mind/`, in the same shape as the existing guard rails, so the mistake cannot
      come back silently.
- [ ] Add `effort: Literal["low","medium","high","xhigh","max"] = "medium"` to
      `AgentProfile`, with **the same value for every role**, and the gateway passes it as
      `output_config={"effort": ...}`.
- [ ] Do **not** assign per-role effort values in this issue. Effort is a cost/quality
      dial, not a randomness dial, and there is no data yet to justify one value over
      another. Transcribing the old temperature numbers into effort levels would be
      cargo-culting a mapping that does not exist. The field exists so the knob is
      reachable; tuning it belongs to the evaluation issue, where a corpus can settle it.
- [ ] Update `STUDY-ASSISTANT-PLAN.md` §3.1: drop the temperature column and note that
      determinism comes from the deterministic tools (`grade_answer`, `schedule_review`),
      not from a sampling parameter.

### FR8 — Tests must not cost money

- [ ] A `FakeAnthropic` test double returns canned responses with realistic `usage`
      objects. All unit tests use it.
- [ ] `pytest` makes **zero** network calls by default. A test that would call the real
      API fails loudly rather than quietly billing.
- [ ] Exactly one opt-in live test, marked `@pytest.mark.live` and deselected by default,
      makes a single real call with `max_tokens=16` to prove the wiring. Document the
      command to run it.

---

## Non-Functional Requirements

- [ ] No production change before PR approval.
- [ ] No credentials, API keys, or `.env` files committed.
- [ ] The default `pytest` run stays free and offline (FR8).
- [ ] Metric cardinality stays bounded — `component` and `model` are closed sets; never
      label a metric with a user id, session id, or free text.
- [ ] The gateway adds no domain knowledge. It does not know what an agent is, and it does
      not construct prompts.
- [ ] Existing behaviour not broken: `/health` and all ISSUE-001 guard rails still pass.

---

## Acceptance Criteria

Given a caller invokes `complete(component="agent:concept", ...)`,
when the call succeeds,
then `llm_tokens_total{component="agent:concept",direction="output"}` increases by the
response's output token count, and `llm_calls_total{outcome="success"}` increases by one.

Given a caller omits `component` or passes a label not in the known set,
when `complete()` is invoked,
then it raises `ValueError` before any network call is made.

Given `llm_max_concurrency` is 2 and ten calls are issued concurrently,
when they run against the fake client,
then the peak number of simultaneously in-flight calls never exceeds 2 and all ten
complete.

Given several components are invoked inside one `request_scope()`, some via
`asyncio.gather`,
when the scope exits,
then `tokens_by_component` contains an entry for every component that ran, and
`tokens_in` equals the sum of their input tokens.

Given the Anthropic API returns a rate-limit error,
when `complete()` is called,
then `llm_calls_total{outcome="error"}` increments, the log line names the component and
request id, and the exception propagates to the caller unchanged.

Given a response whose `usage` object lacks the cache fields,
when tokens are captured,
then those fields record zero and the request completes normally.

Given the stack is running,
when `GET /metrics` is scraped,
then it returns HTTP 200 in Prometheus text format including the three `llm_*` metric
families.

Given a developer adds `temperature=0.3` to any module under `owl_mind/`,
when `pytest` runs,
then the guard-rail test fails naming the file and line.

---

## Test Notes

- [ ] `pytest` green; confirm with `-p no:randomly` that no test hits the network.
- [ ] Token capture verified against a fake `usage` with all four fields, and again with
      the cache fields absent.
- [ ] Concurrency ceiling verified by instrumenting the fake client with an in-flight
      counter.
- [ ] `contextvars` rollup verified under `asyncio.gather` specifically — a sequential-only
      test would pass while parallel dispatch silently undercounts.
- [ ] Error path verified by making the fake client raise `RateLimitError`; assert the
      metric, the log, and that the exception propagates.
- [ ] `/metrics` scraped after a few fake calls; output attached to the PR.
- [ ] Guard rail verified by deliberate violation: add a `temperature=` line, watch the
      test fail, revert.
- [ ] **One** live test run against the real API (`pytest -m live`); attach the token
      counts and the resulting `/metrics` excerpt. Expected cost: a fraction of a cent.

---

## Out of Scope

- Intent recognition, agents, routing, `/chat` — nothing calls the gateway yet in this
  issue except tests.
- **Prompt caching.** `cache_control` placement belongs to whoever builds the prompt, not
  to the gateway. This issue only *captures* the cache token fields so the win is
  measurable when a later issue adds it.
- Per-component model tiering (routing cheap components to Haiku, or to a local model).
  FR1 leaves the `model` override in place so that lands as configuration later; the
  decision itself should wait for real per-component numbers.
- Streaming responses, retry policy beyond the SDK default, request-level budgets.

---

## Risks

- **Correctness risk — contextvars under concurrency.** The rollup is the one piece that
  fails silently: if the var doesn't propagate into gathered tasks, totals are simply low
  and nothing errors. FR5's test is the mitigation and should not be skipped.
- **Data risk:** none. No user data; token counts and latencies only. Metric labels are
  closed sets by NFR.
- **Cost risk:** the suite is free by construction (FR8). The single live test costs a
  fraction of a cent.
- **Workflow risk:** FR7 changes `AgentProfile`, which no code depends on yet. Doing it
  now is nearly free; doing it after five agents exist is a five-file change plus a
  prompt-behaviour re-baseline.
- **Deployment risk:** `/metrics` is unauthenticated. Acceptable locally, but it exposes
  usage volume — note it for whenever this is deployed anywhere real.

---

## Dependencies

- Related issue: ISSUE-001 (scaffold) — merged.
- Related PR: #2.
- Required access: an Anthropic API key with a small budget, for the opt-in live test only.
- Required config: `ANTHROPIC_API_KEY`, `MODEL`, `LLM_MAX_CONCURRENCY` — all already in
  `.env.example`.

---

## Definition of Done

Per SOP §12, with the two adapted items from ISSUE-001 carried forward.

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

Additional, because this issue changes an API contract and adds an endpoint:

- [ ] `README.md` documents `/metrics` and the `pytest -m live` command.
- [ ] `STUDY-ASSISTANT-PLAN.md` §3.1 updated: temperature column removed (FR7).
- [ ] The one-door rule is still enforced — `tests/test_guardrails.py` passes unchanged.

---

## Follow-ups (not in this issue)

- **Prompt caching** once agents build real prompts: the role contract, injected skill, and
  tool definitions form a stable prefix on every request. Cached reads cost roughly a tenth
  of input price and break even at two requests, so this is likely the cheapest cost lever
  available — and FR2's cache fields are what will prove it worked.
- **Per-component model tiering** once `/metrics` has a week of real data. `intent` fires on
  every request and is the obvious first candidate; it is also the one component whose
  quality can be A/B'd objectively, against labelled intent accuracy rather than a judge.
- **Per-role effort values**, once the eval corpus exists to justify them. FR7 ships one
  default for every agent deliberately; `agent:planner` mostly narrates a schedule the tool
  already computed and is the obvious candidate to run cheaper, but that is a hypothesis
  until measured.
