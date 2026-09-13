# [Task] Agent Roster and Orchestration — Routing, Parallel Dispatch, Composition

> Template: `owl_mind_dev_workflow.md` §7. Scope source: `STUDY-ASSISTANT-PLAN.md` §3.1, §11.1 (highlight 2), §11.2.
> Depends on: ISSUE-002 (LLM gateway) merged, ISSUE-003 (intent recognition) merged.

## Project Fields

| Field | Value |
| --- | --- |
| Type | Task |
| Priority | P1 — the headline deliverable; `/chat` does not exist until this lands |
| Area | Agents / Core |
| Status | Ready |
| Milestone | Day 2 — Domain model |
| Owner | TBD |
| Reviewer | TBD |
| Branch | `feature/6-agent-orchestration` *(filed as issue #6)* |

---

## Summary

Implement the five agents and the orchestrator that routes to them: group-to-agent
mapping, supporting-agent selection from the fused intent distribution, concurrent
dispatch, response composition, and degradation. Includes the tool-calling loop and
whitelist enforcement as mechanism; the tools themselves arrive in the next issue.

This is the issue where Owl Mind stops being infrastructure and starts being a
multi-agent system.

---

## Background / Business Context

`IntentRecognizer` already produces everything routing needs: a primary `category`, its
`group`, the full fused `scores` distribution, and `supporting_candidates()` — intents
above `SUPPORTING_FLOOR` sorted deterministically. Nothing consumes any of it yet.

The plan's §11.2 identifies the failure mode this issue must avoid: **if every request
routes to exactly one agent, the orchestrator degrades into a switch statement over five
prompts and the headline architectural claim evaporates — with no test failing.** That is
why `supporting_candidates()` was built to return evidence rather than keyword matches,
and why the acceptance criteria below assert on composite routing specifically.

The second thing that makes this more than dispatch is the whitelist. `AgentProfile`
carries `tool_scope`, and enforcement happens *before* the model is invoked — a tool
outside the tuple is absent from the request rather than discouraged in the prompt. That
is the mechanism behind `PracticeAgent`'s academic-integrity boundary, and it is the
answer to "isn't this just five prompts."

---

## User Story

As a student,
I want one question to reach whichever specialists it actually needs, with each one held
to what it is allowed to do,
so that asking "explain BFS and then quiz me" gets both an explanation and a quiz, while
asking for help on a problem never gets me the answer.

---

## Functional Requirements

### FR1 — The five agents

- [x] `ConceptAgent`, `PracticeAgent`, `PlannerAgent`, `QuizAgent`, `TutorHandoffAgent`,
      each with a populated `AgentProfile` (`role`, `tool_scope`, `effort`, `max_tokens`,
      `risk_boundary`, `handoff_conditions`).
- [x] `max_tokens` per plan §3.1: Concept 1200, Practice 1000, Planner 800, Quiz 1200.
- [x] `effort` stays the same value for every role (ISSUE-002 FR7) — this issue does not
      differentiate it.
- [x] `tool_scope` is declared now even though the registry is empty, so the next issue
      populates tools rather than inventing scopes.
- [x] **`TutorHandoffAgent` overrides `handle()` and makes no model call.** It builds a
      structured handoff from the request and returns. A test asserts the gateway was
      never invoked — this is the property that keeps the escape hatch working during a
      model outage.

### FR2 — Routing

- [x] `_route_decision(intent) -> RoutingDecision` maps `IntentGroup` to a primary agent:
      `LEARN`→Concept, `PRACTICE`→Practice, `PLAN`→Planner, `ASSESS`→Quiz,
      `SUPPORT`→Concept *unless* the intent is the human-tutor one, which goes to
      TutorHandoff.
- [x] Supporting agents come from `intent.supporting_candidates()` — and only from there —
      mapped through the same group table, deduplicated against the primary, and capped so
      the total never exceeds `MAX_AGENTS` (3). That method now applies two gates of its
      own (corroboration, and the fallback check); routing must not reimplement or bypass
      them.
- [x] **`IntentCategory.HUMAN_TUTOR` short-circuits to TutorHandoff** before scoring, with
      no supporting agents. **Not `UrgencyLevel.CRITICAL`** — see the amendment note below.
- [x] `RoutingDecision.reason` carries the primary, the supporting list, and the score
      vector — a route that cannot be explained after the fact cannot be debugged.
- [x] Routing is a pure function of `Intent`: no model call, no I/O. It must be testable
      by constructing an `Intent` directly.

> **Amendment (pre-implementation).** This requirement originally keyed the short-circuit
> on `UrgencyLevel.CRITICAL`. `compute_urgency` returns CRITICAL for any due date within a
> day, and `_extract_due_date` sets one on the bare words "today", "tonight", or
> "tomorrow" — so "explain BFS to me today", "I need to understand DP tonight", and "my
> assignment is due tomorrow, explain BFS" all escalated to a human TA and received no
> answer, verified against the built recognizer. A deadline is when a student needs the
> most help, not the least.
>
> Urgency fuses two unrelated ideas: *this person asked for a human* and *this is
> time-pressured*. Only the first is a routing decision. `compute_urgency` is unchanged
> and CRITICAL keeps its meaning — it is a priority signal PlannerAgent reads when
> scheduling and the composer may surface ("this is due tomorrow, so start here"). It just
> no longer decides which agent answers.

### FR2a — What `OTHER` does

`Intent` now distinguishes the two meanings of `OTHER` via `fallback_from` / `is_fallback`,
because they want opposite answers. Neither costs a model call.

- [x] **`is_fallback` is true** — "evidence below bar". Something was asked; the panel
      disagreed about what. The orchestrator returns a deterministic clarifying question
      built from `fallback_from` and the top entries of `intent.scores` ("Did you want me
      to explain BFS, or quiz you on it?"). No agent is dispatched.
- [x] **`is_fallback` is false** — "no relevant intent". The model classified the message
      as off-topic, or no signal returned anything. The orchestrator returns a scoped
      decline naming what Owl Mind does cover. No agent is dispatched.
- [x] Both paths return `OrchestratorResult(primary_agent=None, ...)` with a
      `routing_reason` naming which case fired, and **make zero gateway calls** — asserted
      by test, as with TutorHandoff.
- [x] Neither path routes to ConceptAgent. The original FR2 group table sent `OTHER`
      through `SUPPORT` → Concept, which would have had ConceptAgent earnestly explaining
      the weather, with "cites materials" as the only thing standing in the way.
- [x] `_route_decision` is never called for an `OTHER` intent — `run()` returns before it.
      This is what keeps `RoutingDecision.primary_agent` non-optional while
      `OrchestratorResult.primary_agent` is `AgentType | None`.

### FR3 — Dispatch

- [x] Single-agent requests call one agent.
- [x] Multi-agent requests dispatch concurrently via `asyncio.gather`.
- [x] **The whole dispatch runs inside `gateway.request_scope()`**, so token usage rolls
      up across concurrent agents. ISSUE-002 built and tested exactly this; a test here
      asserts the rollup is non-zero and names every agent that ran.
- [x] One instance per agent type. The pool-of-instances design stays deferred — see
      Out of Scope.

### FR4 — The tool loop and the whitelist

- [x] `BaseAgent` runs a bounded tool-calling loop (max 3 rounds, as in the reference).
- [x] Only tools in `profile.tool_scope` are sent in the request. A tool outside the
      scope is not merely rejected on return — **it never appears in the request**.
- [x] Tool inputs are validated against `input_schema` before the handler runs; a
      hallucinated argument yields a validation error as a tool result, not a stack trace.
- [x] `tool_traces` is assigned **on the success path**, not only on loop exhaustion. This
      is the reference implementation's `_last_tool_traces` bug (plan §11.4 #3), which
      made `/trace/tools` empty after every successful request and left the monitor
      measuring nothing. A test asserts traces are present after a successful tool call.
- [x] The registry is empty in this issue. Tests register a fake tool to exercise the loop
      and prove the whitelist — inventing domain tools early is the next issue's job.

### FR5 — Composition

- [x] `ResponseComposer` merges multiple `AgentResponse`s into one reply, through the
      gateway under `component="composer"`.
- [x] A single-agent result does **not** go through the composer — paying for a model call
      to reformat one answer is waste.
- [x] The composed reply preserves each agent's contribution rather than averaging them
      into mush; the student asked two things and should get two answers.

### FR6 — Degradation

- [x] **Decide and document the fallback.** The reference implementation degraded a failed
      specialist to `GeneralAgent`; Owl Mind has no general agent, so this cannot be
      ported and must be designed. Proposed: a failed *supporting* agent is dropped and
      the request succeeds with what remains (noting the omission); a failed *primary*
      surfaces as an error rather than being silently answered by a role that was not
      chosen. Confirm or overrule in review.
- [x] A failure in one concurrent branch must not cancel the others —
      `gather(..., return_exceptions=True)` or equivalent.
- [x] Every failure increments the per-agent stats the monitor will later read.

### FR7 — Startup contracts

- [x] Register in `core/contracts.py`: every `AgentType` has exactly one registered agent,
      and every name in a profile's `tool_scope` exists in the tool registry. The second
      check is trivially true while the registry is empty and becomes load-bearing next
      issue — registering it now means the tools issue cannot drift.

---

## Non-Functional Requirements

- [ ] No production change before PR approval.
- [x] No credentials or `.env` contents committed.
- [x] The default `pytest` run stays free and offline — all agent tests use a fake gateway.
- [x] Every model call carries its `agent:*` component label, already in
      `KNOWN_COMPONENTS`.
- [x] No module outside `core/llm_gateway.py` calls the API; no sampling parameters. Both
      existing guard rails must still pass.
- [x] Routing stays a pure function — resist the temptation to let it fetch anything.

---

## Acceptance Criteria

Given an `Intent` whose group is `LEARN` and whose distribution has no other intent above
the supporting floor,
when routing runs,
then the decision names ConceptAgent as primary with an empty supporting list.

Given an `Intent` whose distribution has a second intent above `SUPPORTING_FLOOR` in a
different group,
when routing runs,
then the decision names both agents, primary first, and `reason` contains the score vector.

Given a composite request routed to two agents,
when the orchestrator runs it,
then both agents are invoked concurrently, the composer produces one reply containing both
contributions, and the request-scope rollup reports usage for both plus the composer.

Given an `Intent` whose category is `HUMAN_TUTOR`,
when the orchestrator runs it,
then TutorHandoffAgent handles it, the supporting list is empty, and **the gateway records
zero calls**.

Given an `Intent` whose urgency is `CRITICAL` because a deadline is a day away, and whose
category is an ordinary study intent,
when the orchestrator runs it,
then it is routed to that intent's agent as normal and **is not escalated to TutorHandoff**.
The regression this pins: "explain BFS to me today" answered by a handoff stub.

Given an `Intent` where `is_fallback` is true,
when the orchestrator runs it,
then the reply is a clarifying question naming `fallback_from`, `primary_agent` is `None`,
and **the gateway records zero calls**.

Given an `Intent` whose category is `OTHER` and `is_fallback` is false,
when the orchestrator runs it,
then the reply is a scoped decline, `primary_agent` is `None`, and **the gateway records
zero calls**. ConceptAgent is not invoked.

Given an `Intent` whose distribution has a second intent above `SUPPORTING_FLOOR` that the
model never returned and no pattern rule matched,
when routing runs,
then the supporting list is empty and exactly one agent is dispatched — index similarity
alone does not buy a second agent call plus a composer call.

Given an agent whose `tool_scope` excludes `some_tool`,
when it calls the model,
then `some_tool` is absent from the request payload entirely.

Given a supporting agent that raises,
when the orchestrator runs a multi-agent request,
then the primary's answer is still returned and the response records that a supporting
agent was dropped.

Given an agent completes a request that used a tool,
when the result is inspected,
then `tool_traces` is non-empty.

Given the application starts with an agent type that has no registered instance,
when the lifespan runs,
then startup aborts naming the missing type.

---

## Test Notes

- [x] `pytest` green; no network calls.
- [x] Routing tested by constructing `Intent` objects directly — no model needed.
- [x] **At least five composite routing cases**, per plan §11.2, asserting a non-empty
      supporting list: "explain BFS then quiz me", "I'm stuck on two-sum and what should I
      review this week", "why is my solution O(n^2), show me the concept I'm missing",
      "I failed the graphs quiz, what now", plus one more.
- [x] Concurrency confirmed — instrument the fake gateway with an in-flight counter and
      assert branches overlap rather than running in sequence.
- [x] Whitelist verified by deliberate violation: give a fake tool to one agent, confirm a
      second agent's request omits it.
- [x] TutorHandoff verified to make zero gateway calls.
- [x] **Both `OTHER` paths verified to make zero gateway calls**, and verified distinct
      from each other — a test that only checks "returns something" passes when the two
      cases are collapsed.
- [x] **A deadline case that must *not* escalate**: build an `Intent` with an ordinary
      category and `urgency=CRITICAL`, assert the route is the category's agent. Without
      this, re-keying the short-circuit on urgency would go unnoticed.
- [x] Composite tests must set `corroborated` on the constructed `Intent`. A test that
      populates `scores` and forgets `corroborated` gets an empty supporting list and will
      look like a routing bug; this is the most likely way to waste an hour in this issue.
- [x] Failure path verified for both a supporting and a primary agent.
- [x] `tool_traces` verified present after a successful tool round.

---

## Out of Scope

- **Tools.** The eight agent-level tools are the next issue. This one ships the loop and
  the whitelist with an empty registry.
- **`/chat`.** The HTTP surface, memory wiring, and the token fields on the response come
  after this. The orchestrator is exercised directly in tests.
- **Memory.** Agents receive a request object; nothing populates conversation history yet.
- **The instance pool and `routing_score`.** One instance per type here. When the pool
  lands it needs a warm-up rule: a fresh instance computes `success_rate = 1.0` and
  `latency_score = 1.0` for a perfect score, so under `max()` selection a new canary would
  capture 100% of a role's traffic — backwards for the use case the pool exists to serve.
- **Monitor feedback into routing.** `monitor_penalty` stays zero.
- **Skills injection.** Role prompts are static in this issue.

---

## Risks

- **Composite routing is the thing most likely to be quietly lost.** If the group mapping
  collapses several intents onto one agent, `supporting_candidates()` can return two
  intents that both map to the primary, and the request silently becomes single-agent. The
  five composite tests are the mitigation; check they assert on *agents*, not intents.
- **Correctness risk:** `gather` without `return_exceptions` cancels sibling branches on
  the first failure, turning one flaky agent into a failed request. FR6 covers it.
- **Scope risk:** this issue is already large. Anything from Out of Scope that appears in
  the diff should be pushed back rather than waved through.
- **Cost risk:** a composite request is now three model calls (two agents plus composer).
  With `/metrics` already live, watch `llm_calls_total` per request once `/chat` exists.
  The corroboration gate added to `supporting_candidates()` is what keeps that cost tied to
  genuine composites: without it, an intent the model never returned crossed the supporting
  floor at a cosine similarity of 0.607, which is ordinary between two short CS-study
  sentences, and every such request silently cost three calls instead of one.
- **Composite detection rests entirely on the model.** The pattern signal cannot promote a
  supporting agent at the current weights — at 0.15 its ceiling is 0.231 with the model
  voting, under the 0.25 floor. So if the model returns one intent for "explain BFS then
  quiz me", the `\b(quiz|test)\s+me\b` rule cannot rescue the second agent. The five
  composite tests will pass (they stub the model returning both) while production
  composites depend on a single signal. ISSUE-004 should measure this before the weights
  are called settled.
- **Deployment risk:** none. No new services, no schema, no migration.

---

## Dependencies

- Related issues: ISSUE-002 (merged, PR #4), ISSUE-003 (in review).
- Required access: none beyond the repository.
- Required config: none new.

---

## Definition of Done

- [x] Requirement implemented and checkboxes checked.
- [ ] Code committed to feature branch.
- [ ] Code pushed to GitHub.
- [ ] Pull Request opened against the target branch.
- [ ] PR links the Issue using `Closes #<number>`.
- [x] Local/dev testing completed.
- [ ] Reviewer approved the PR.
- [ ] PR merged into the target branch.
- [ ] Issue auto-closed or manually closed with explanation.
- [ ] Project card moved to Done.
- [ ] SOP/README updated if the workflow changed.
- [x] Changes are tested locally.
- [ ] *(adapted)* Redis and Chroma connectivity verified from inside the API container.
- [ ] *(adapted)* Logs reviewed for leaked API keys, `.env` contents, and unexpected errors.

Additional:

- [x] `README.md` gains the agent roster and what each is forbidden to do.
- [ ] The FR6 degradation decision is recorded in the PR, not just in code.
- [x] Both existing guard rails pass unchanged.

---

## FR6 decision, as implemented

The requirement asked for this to be decided and recorded rather than ported. The
reference implementation degraded a failed specialist to `GeneralAgent`; Owl Mind has no
general agent, and inventing one would undo the roster's entire argument.

**A failed supporting agent is dropped.** The reply is returned with whatever answered,
`dropped_agents` names what was lost, and `routing_reason` gains `dropped=[...]` so the
omission is visible rather than looking like the router simply chose fewer agents.

**A failed primary raises `PrimaryAgentFailed`.** It is not answered by a role that was not
chosen. Substituting a specialist changes the boundaries the request was routed under: a
`problem_help` request handed to ConceptAgent gets answered, including the solution
PracticeAgent is forbidden from giving. A visible failure is better than a silent
academic-integrity breach.

`gather(..., return_exceptions=True)` keeps one flaky branch from cancelling its siblings,
and `AgentStats` counts every failure so the monitor issue has something to read.

---

## Follow-ups (not in this issue)

- The eight agent tools, which make `tool_scope` and the whitelist meaningful.
- `/chat`, wiring intent → routing → agents → composer, with the token rollup surfaced on
  the response.
- The instance pool plus the warm-up rule described in Out of Scope.
- Skills injection into role prompts, and the `academic_integrity` skill that states in
  prose what the whitelist already enforces structurally.
