# [Task] `/chat` — the first request a real model ever sees

> Template: `owl_mind_dev_workflow.md` §7. Scope source: `STUDY-ASSISTANT-PLAN.md` §3.5, §11.3.
> Depends on: ISSUE-007 (agent tools) merged.

## Project Fields

| Field | Value |
| --- | --- |
| Type | Task |
| Priority | P1 — nothing built so far is reachable by a user |
| Area | API / Core |
| Status | Ready |
| Milestone | Day 3 — Surface |
| Owner | TBD |
| Reviewer | TBD |
| Branch | `feature/<n>-chat-endpoint` *(number it after the issue lands)* |

---

## Summary

Wire `POST /chat`: a student message in, a routed and composed answer out, with the
per-request token rollup on the response. Construct the `IntentRecognizer` at startup --
nothing constructs one today — and decide what happens to the embedding signal.

Also land the two Tier A defects from ISSUE-006 that this endpoint turns from theoretical
into user-visible: **F2** and **F8**.

---

## Background / Business Context

**Everything built so far has only ever been called by a fake.** Four issues of
infrastructure, 218 tests, and not one of them has seen a real response object. The suite is
green because `FakeAnthropic` returns what the code expects; that is the correct default
(ISSUE-002 FR8) and it is also the reason a whole class of bug is invisible right now.

Three things are untested against reality, and all three are on the `/chat` path:

- **`stop_reason`.** The tool loop continues on `"tool_use"`. No fake has ever returned
  `"max_tokens"`, and ISSUE-006 F2 is exactly that case.
- **Content block shapes.** `_first_text` and the `tool_use` branch read attributes off
  whatever the SDK returns. The fakes are dataclasses that happen to match.
- **Latency.** Nothing has a deadline (F8). Four model calls fanned out under `gather`,
  each with the SDK's ten-minute default, is a number nobody chose.

So `/chat` is not "add an endpoint". It is the first time the system runs, and the issue
should be scoped as that.

**The embedding signal is currently dead and nobody has had to admit it.** Nothing
constructs `ChromaTemplateIndex`, so `IntentRecognizer(gateway, index=None)` returns `{}`
from `_embedding_signal` on every call. `_fuse` renormalises over the signals that voted, so
this is not *wrong* — but 0.35 of the designed panel has never run outside tests. FR3 forces
the decision rather than letting `/chat` ship with a two-signal classifier described as a
three-signal one.

---

## User Story

As a student,
I want to send a question and get an answer,
so that the five agents behind it are something I can use rather than something that exists.

---

## Functional Requirements

### FR1 — The endpoint

- [ ] `POST /chat` taking `{message, session_id?, user_id?}` and returning
      `{request_id, response, primary_agent, supporting_agents, escalated, latency_ms,
      routing_reason, usage}`.
- [ ] Pydantic models for both, so the schema is in the OpenAPI document rather than in a
      dict literal.
- [ ] `message` is bounded (suggest 4000 chars). An unbounded field on an unauthenticated
      endpoint is a token bill with a URL.
- [ ] **All three no-agent outcomes return HTTP 200**: the handoff, the clarifying question,
      and the decline. None is an error, and a 4xx would make a student's off-topic question
      look like a client bug.
- [ ] `primary_agent` is `null` on the two `OTHER` paths, as `OrchestratorResult` already
      models.

### FR2 — Wiring, at startup

- [ ] `IntentRecognizer` constructed in `lifespan` and held on `app.state`, like the gateway
      and orchestrator.
- [ ] `/chat` runs intent recognition, then `orchestrator.run(...)`, and returns the result.
      No domain logic in the route handler beyond that.
- [ ] `AgentRequest.history` stays empty and `session_id` is accepted, echoed, and otherwise
      unused. Memory is a later issue; **say so in the response model's docstring** so the
      field does not read as working.

### FR3 — Decide the embedding signal, out loud

Pick one and record it in the PR:

- [ ] **Wire it.** Construct `ChromaTemplateIndex` in `lifespan` and call `seed()` (already
      implemented). Then: seeding must not block a healthy boot when Chroma is down — catch,
      log, and run with two signals. A startup that dies because a *degraded* dependency is
      unreachable contradicts `/health`'s whole design.
- [ ] **Or do not.** Then `WEIGHTS` describes a signal that never votes, and the docstring
      and README must say the panel is two signals in production. Silence is the one option
      that is not acceptable.

### FR4 — F2: the model signal must not be truncated away

`ISSUE-006 F2`, promoted here because `/chat` is where it bites.

- [ ] Send `thinking={"type": "disabled"}` for `component="intent"`, or raise its
      `max_tokens`. Adaptive thinking is **on by default** on the configured model, and
      `max_tokens=400` caps thinking and output together.
- [ ] Record `stop_reason`. A truncated response is a distinct outcome from success and from
      a parse failure on well-formed output; `llm_calls_total{outcome="truncated"}` keeps
      cardinality bounded.
- [ ] A test drives the truncation path through a fake carrying
      `stop_reason: "max_tokens"` and asserts it is counted, not silently swallowed by
      `_parse_llm_response`.

### FR5 — F8: a deadline

`ISSUE-006 F8`, same reasoning.

- [ ] A configurable per-call deadline in the gateway. The SDK default is ten minutes and
      the semaphore is held across the whole call, so eight slow calls block the process.
- [ ] `/chat` fans out to as many as four calls. State the worst-case wall clock in the PR
      and make it a number somebody chose.
- [ ] A timeout increments `llm_calls_total{outcome="error"}` and surfaces as a 504.

### FR6 — Error mapping

- [ ] `PrimaryAgentFailed` -> **503**, with a message that says the assistant could not
      answer, not which class raised.
- [ ] Upstream provider errors -> **502**; timeouts -> **504**; `UnknownComponent` -> **500**
      (it is a bug, not a user error).
- [ ] **No response body ever contains a stack trace, a prompt, or an API key.** A test
      asserts the failure body against those.

### FR7 — The rollup on the response

- [ ] `OrchestratorResult.usage` surfaced as the `usage` field: `llm_calls`, `tokens_in`,
      `tokens_out`, `tokens_by_component`.
- [ ] A test asserts a composite request reports both agents and the composer, so the claim
      that cost is attributable is visible at the API surface and not only in `/metrics`.

### FR8 — The live test

- [ ] **One** test under the existing `live` marker that sends a real message through the
      real endpoint against the real model, and asserts a non-empty answer and a non-zero
      rollup.
- [ ] It stays deselected by default, so `pytest` remains free and offline.
- [ ] This is the first test in the project that could fail for a reason the fakes cannot
      produce. That is the point of it.

---

## Non-Functional Requirements

- [ ] Default `pytest` run stays free and offline.
- [ ] Both guard rails pass unchanged.
- [ ] `/chat` is unauthenticated, like `/metrics`. **Note it in the README** next to the
      existing warning rather than implying otherwise.
- [ ] No new runtime dependency.

---

## Acceptance Criteria

Given a single-intent message,
when `POST /chat` runs,
then one agent answers, the composer is not called, and `usage.llm_calls` is 2 (intent plus
the agent).

Given a composite message,
when `POST /chat` runs,
then two agents and the composer appear in `usage.tokens_by_component`, and both
contributions appear in the reply.

Given an off-topic message,
when `POST /chat` runs,
then the reply declines, `primary_agent` is `null`, HTTP is 200, and `usage.llm_calls` is 1
— the intent call only.

Given a message asking for a human,
when `POST /chat` runs,
then `escalated` is true and `usage.llm_calls` is 1.

Given the model returns `stop_reason: "max_tokens"` on the intent call,
when `/chat` runs,
then the truncation is counted distinctly and the request still answers on the surviving
signals.

Given the primary agent raises,
when `/chat` runs,
then the response is 503 and its body contains no stack trace, prompt, or key.

Given Chroma is unreachable at startup,
when the app boots,
then it serves, `/health` reports `degraded`, and intent recognition runs on the signals
that remain.

---

## Out of Scope

- **Memory.** `session_id` is accepted and unused. Multi-turn is the next issue and is the
  reason this one keeps the field rather than omitting it.
- **Streaming.** A student watching a composed multi-agent answer arrive is a real feature
  and a different one; composition inherently waits for every agent.
- **Auth and rate limiting.** Worth their own issue, and worth having before this is
  deployed anywhere public.
- **The MCP materials server**, and the rest of ISSUE-006.

---

## Risks

| Risk | Mitigation |
| --- | --- |
| The live test is flaky and gets deleted rather than fixed | Keep it to exactly one, assert only shape and non-emptiness, never wording |
| Real content blocks do not match what the fakes taught the code to expect | That is the discovery this issue exists to force; budget time for it rather than treating it as a surprise |
| Chroma seeding at boot slows or blocks startup | FR3 requires the failure path to be a degraded boot, not a dead one |
| `/chat` ships unauthenticated and gets pointed at from somewhere public | Called out in NFRs and the README; a real fix is its own issue |
| The deadline chosen for F8 is too tight and truncates legitimate multi-agent turns | Pick it from the measured p95 of the live test, not from taste |

---

## Definition of Done

- [ ] `POST /chat` implemented, typed, and in the OpenAPI document.
- [ ] `IntentRecognizer` constructed at startup; FR3 decision recorded in the PR.
- [ ] F2 and F8 closed in ISSUE-006, or explicitly reclassified there.
- [ ] Error mapping tested, including the no-leak assertion.
- [ ] One live test, deselected by default, green when run.
- [ ] `pytest` green offline; `ruff` clean; guard rails unchanged.
- [ ] README gains a `/chat` example and the unauthenticated warning.
- [ ] The worst-case wall clock for a composite request stated in the PR.

---

## Follow-ups (not in this issue)

- Memory: working, episodic, and profile layers, which give `session_id` meaning and
  `get_due_topics` something to read.
- The MCP materials server, returning `materials_search` to ConceptAgent's scope.
- Auth and rate limiting before any public deployment.
- Streaming responses.
