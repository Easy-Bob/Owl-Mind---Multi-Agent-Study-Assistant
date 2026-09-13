# [Issue #6] Agent roster and orchestration -- routing, parallel dispatch, composition

## Summary

Five agents behind a routing layer, the orchestrator that selects them, the tool loop and
whitelist as mechanism, and response composition. This is where Owl Mind stops being
infrastructure and starts being a multi-agent system.

Every split in the roster is a capability boundary, not a topic: each role is forbidden
something another is allowed, and the forbidden thing is enforced **before the model is
called** rather than asked for in a prompt. `tool_scope` is applied when the request
payload is built, so a tool outside it is absent from the request rather than rejected on
return -- a prompt instruction can be argued with, an absent tool cannot.

`144 passed, 1 skipped`, ruff clean, both existing guard rails unchanged. No network calls
in the default suite.

## Linked Issue

Closes #6

## Two commits, deliberately split

**`77d6f5d` -- the four intent decisions.** Routing consumes `Intent`, and four ambiguities
in it would have been baked in silently by anything built on top. Reviewable on its own,
before the implementation that depends on it.

**`0fc6d25` -- the implementation.**

## FR6 decision (the issue asked for this to be confirmed or overruled)

The reference implementation degraded a failed specialist to `GeneralAgent`. Owl Mind has
no general agent, and inventing one would undo the roster's argument.

- **A failed supporting agent is dropped.** The reply returns with whatever answered;
  `dropped_agents` names what was lost and `routing_reason` gains `dropped=[...]`, so the
  omission stays visible rather than looking like the router chose fewer agents.
- **A failed primary raises `PrimaryAgentFailed`.** Substituting a specialist changes the
  boundaries the request was routed under: a `problem_help` request handed to ConceptAgent
  gets answered, *including the solution PracticeAgent is forbidden from giving*. A visible
  failure beats a silent academic-integrity breach.

`gather(..., return_exceptions=True)` keeps one flaky branch from cancelling its siblings,
and `AgentStats` counts every failure so the monitor issue has something to read.

## Worth a reviewer's attention

- **The human-tutor short-circuit keys on the category, not on urgency.** `compute_urgency`
  returns CRITICAL for any deadline within a day and `_extract_due_date` sets one on the
  bare word "today", so the original wording answered *"explain BFS to me today"* with a
  handoff stub -- at the moment a student needs the most help, not the least. FR2 is
  amended in-file with the reproduction, and a test pins that a CRITICAL-urgency ordinary
  intent must **not** escalate.
- **Three requests never reach an agent, and none costs a model call**: an explicit ask for
  a human, an ambiguous message (answered with a clarifying question built from the intent
  distribution), and an off-topic one (declined). Asserted by `client.calls == []`.
- **`tool_traces` are assigned on the path every successful request takes.** The reference
  implementation set them only when the loop ran out of rounds, so `/trace/tools` was empty
  after every request that worked and the monitor measured nothing (plan 11.4 #3).
- **`usage` added to `OrchestratorResult`.** FR3 requires a test that the rollup covers
  every agent plus the composer, and `request_scope` is internal to `run()` -- there was no
  way to observe it. The deferred item is surfacing tokens on the HTTP response, not on the
  internal result object.
- **`/health` now reports the live pool** (ISSUE-006 H1, which says to wire it here). The
  FR7 contract would otherwise verify five agents while `/health` reported none. This
  turned `test_health_reports_no_agents_before_the_agent_issue` red, correctly; it is
  rewritten to assert the roster.
- **`tool_scope` names tools that do not exist yet.** Deliberate per FR1 -- the tools issue
  populates a declared scope instead of inventing one, and the FR7 contract fails the boot
  the moment the two disagree.

## Known gap, carried forward

Composite routing in production rests **entirely on the model** returning both intents. At
weight 0.15 the pattern signal cannot reach the supporting floor (ceiling 0.231 against a
floor of 0.25), so the `quiz me` rule cannot rescue a second agent the model missed. The
five composite tests pass by stubbing the model returning both, so the suite cannot see
this gap. Pinned by `test_the_pattern_signal_cannot_reach_the_supporting_floor_alone` and
carried into #7 as input for weight tuning.

Composite routing is asserted on *agents* rather than intents throughout: two intents in
one group are still a single-agent request, and asserting on intents would hide that.

## Testing

- `pytest` -- 144 passed, 1 skipped, no network calls.
- `ruff check .` -- clean.
- Five composite routing cases, each asserting a distinct agent pair.
- Concurrency confirmed by an in-flight counter on the fake client (`peak_in_flight >= 2`).
- Whitelist verified by deliberate violation: one agent's request omits another's tool.
- Zero-gateway-call paths verified for TutorHandoff and both `OTHER` exits.
- Failure paths verified for a supporting agent, a primary agent, and a sibling that must
  not be cancelled.

## Not done

Thirteen checkboxes remain in the issue: PR/merge process items, plus verifying Redis and
Chroma connectivity from inside the container, which needs Docker running.
