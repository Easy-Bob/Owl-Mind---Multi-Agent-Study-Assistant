# [Task] Agent Tools — the deterministic layer the roster's guarantees rest on

> Template: `owl_mind_dev_workflow.md` §7. Scope source: `STUDY-ASSISTANT-PLAN.md` §3.1.1, §3.3.
> Depends on: ISSUE-005 (agent roster and orchestration) merged.

## Project Fields

| Field | Value |
| --- | --- |
| Type | Task |
| Priority | P1 — three merged issues assert determinism; none has delivered it |
| Area | Agents / Core |
| Status | Ready |
| Milestone | Day 3 — Tools |
| Owner | TBD |
| Reviewer | TBD |
| Branch | `feature/<n>-agent-tools` *(number it after the issue lands)* |

---

## Summary

Implement the eight in-process agent tools: deterministic `(request, args) -> dict`
functions registered in `agents/tools/REGISTRY`, which the tool loop built in ISSUE-005
already knows how to call, validate, trace, and whitelist.

Nothing in the loop changes. What changes is that it finally has something to run, and
that three claims made in three merged issues stop being assertions.

---

## Background / Business Context

**The determinism claim has been made three times and delivered zero times.** ISSUE-002 FR7
says reproducibility "comes from moving the exact work into tools -- a grade is computed by
comparing against a rubric in code, a review date by SM-2 arithmetic." `AgentProfile`
carries `risk_boundary` strings that say the same thing. Plan §3.1 promises scheduling is
"exact" and grading "stabilised". Every one of those statements is currently backed by a
prompt sentence and an empty registry.

This is the issue where the claim becomes code, and it is the reason the priority is P1
despite no user-visible feature: the roster's argument for existing is that each agent is
*structurally* prevented from crossing a boundary. Right now four of the five boundaries
are prose.

**The whitelist has nothing to whitelist.** ISSUE-005 built enforcement that applies when
the request payload is constructed, and tested it against fake tools. With a populated
registry, `PracticeAgent`'s academic-integrity boundary becomes a property of the payload:
the agent is offered `build_hint`, whose level 3 is still not a solution, and is not
offered anything that could produce one.

**The FR7 contract is dormant and becomes load-bearing here.**
`_check_tool_scopes_resolve` returns early while `REGISTRY` is empty. The first tool
registered wakes it, and it will immediately fail -- see FR1.

### FR1 exists because ISSUE-005 got the names wrong

ISSUE-005 FR1 declared `tool_scope` early so that "the next issue populates tools rather
than inventing scopes". The scopes it declared do not match the plan's tool table:

| Declared in `roster.py` | Plan §3.3 | Problem |
| --- | --- | --- |
| `search_materials` (Concept) | `materials_search` | **It is an MCP tool, not in-process.** Owns the Chroma client; belongs to the MCP issue |
| `check_step` (Practice) | `analyze_complexity` | Invented; no such tool in the plan |
| `get_progress` (Planner) | `get_due_topics` | Invented; the plan's version reads Redis |
| `generate_quiz` (Quiz) | `generate_quiz_spec` | Renamed, and the distinction matters: the tool returns a *spec* (topic, count, difficulty), not the questions |
| `get_prerequisites`, `build_hint`, `grade_answer`, `schedule_review` | same | correct |

So the scope declaration did the opposite of its job for half the entries. Correcting it is
FR1 and lands first, on its own commit, before any tool exists -- otherwise the tools get
built to the invented names and the drift becomes permanent.

---

## User Story

As a student,
I want the same answer to receive the same mark and the same progress to produce the same
revision date,
so that I can trust a self-test I am not being graded on by a human.

---

## Functional Requirements

### FR1 — Correct the drifted tool scopes first

- [ ] Rename to the plan's names: `check_step` -> `analyze_complexity`,
      `generate_quiz` -> `generate_quiz_spec`, `get_progress` -> `get_due_topics`.
- [ ] Remove `search_materials` from `ConceptAgent.tool_scope`. It is an MCP tool
      (`materials_search`); the MCP issue adds it back.
- [ ] Remove `get_due_topics` from `PlannerAgent.tool_scope` **for now** — it reads Redis
      progress state and memory is still a stub. The memory issue adds both the tool and
      the scope entry. A declared scope naming a tool that cannot work is the same lie as
      an undeclared one.
- [ ] Land as the first commit, with no tools registered yet, so the rename is reviewable
      against a passing suite rather than tangled with new behaviour.

Resulting scopes after FR1: Concept `(get_prerequisites,)`, Practice
`(build_hint, analyze_complexity)`, Planner `(schedule_review,)`, Quiz
`(generate_quiz_spec, grade_answer)`.

### FR2 — The deterministic tools

Each is a pure function of `(request, args)`. No model call, no network, no clock reads
except through an injected value — a tool that calls `datetime.now()` internally cannot be
tested for the property this issue exists to establish.

- [ ] `get_prerequisites` — static concept graph (red-black tree -> BST -> rotations).
      Returns the prerequisite chain for a topic, or an empty chain with a reason.
- [ ] `build_hint` — progressive levels 1-3 over the request's problem context. **Level 3
      is still not the answer** (FR3).
- [ ] `analyze_complexity` — pattern-based Big-O guidance. **Does not execute code**; it
      recognises shapes (nested loop over the same collection, halving recursion) and names
      the class with its reasoning.
- [ ] `schedule_review` — SM-2 arithmetic (FR4).
- [ ] `generate_quiz_spec` — returns the *structure* (topic, count, difficulty mix), not
      the questions. The model writes the questions from the spec; the spec is what makes
      two quizzes on the same topic comparable.
- [ ] `grade_answer` — rubric comparison (FR5).
- [ ] `create_handoff_summary` — structured TA handoff. See FR6 for why this one is not
      registered as a model-facing tool.
- [ ] `inspect_request_context` — retained from the reference implementation; returns what
      the agent can see about the current request. Available to every role.

### FR3 — `build_hint` level 3 is still not the answer

- [ ] Three levels: orient ("what kind of problem is this"), narrow ("which technique"),
      concrete ("apply it to *this* input") — and level 3 stops before the result.
- [ ] A test asserts that for a problem with a known answer, no level's output contains it.
      This is the academic-integrity boundary made structural: with `build_hint` the only
      problem-facing tool in `PracticeAgent.tool_scope`, "never emit a complete solution"
      stops depending on the model's cooperation.
- [ ] The level is an argument the model chooses, so the escalation is visible in
      `tool_traces` and the monitor can later see a student being walked up the ladder.

### FR4 — `schedule_review` is SM-2, and takes its clock as an argument

- [ ] Standard SM-2: ease factor, interval, repetition count in; next interval and updated
      ease out. One correct answer per input.
- [ ] `now` is an argument, never read inside. The property under test is "the same
      progress always yields the same date", which is untestable against a hidden clock.
- [ ] A table-driven test over the published SM-2 examples, plus a test that the same input
      twice yields the identical dict.
- [ ] Plan §11 lists "SM-2 -> fixed-interval Leitner" as an acceptable simplification under
      time pressure. If that is taken, say so in the PR — it changes what the tool promises,
      not whether it is deterministic.

### FR5 — `grade_answer` is stabilised, and the limit is stated

Plan §3.1.1 is explicit that this is **not** fully deterministic, and the issue must not
claim otherwise:

- [ ] The rubric is written by the model at **question-generation** time and **pinned**.
      It is never regenerated at grading time — regenerating it lets two gradings of the
      same answer disagree, which destroys the only fairness property self-testing has.
- [ ] The one remaining judgement ("is rubric point N present in this answer?") is
      decomposed into **binary checks**, not a holistic score.
- [ ] Weights are summed **in code**. The model never returns a total.
- [ ] A test asserts that identical `(answer, rubric)` input produces an identical mark,
      given identical binary-check results.
- [ ] The README and the tool's docstring state the residual: the binary checks are model
      judgements and can differ between runs. "Stabilised, not deterministic" is the honest
      claim and the one that survives an interview question.

### FR6 — `create_handoff_summary` is a function, not a registered tool

- [ ] Implement it, and call it from `TutorHandoffAgent.handle` instead of registering it
      in `REGISTRY`.
- [ ] Rationale to record in the PR: that agent makes no model call, so it has no tool
      loop and nothing to offer a tool *to*. Registering it would put a tool in the registry
      that no payload can ever contain, and would tempt someone to give TutorHandoff a
      `tool_scope` — which is one edit away from giving it a gateway call.
- [ ] `TutorHandoffAgent.tool_scope` stays empty. A test asserts it.

### FR7 — Registration, and waking the dormant contract

- [ ] Registration is explicit (`register(AgentToolSpec(...))`), not a decorator scan, so
      the boot-time diff against `tool_scope` stays possible.
- [ ] Every tool declares a full `input_schema` with `required` and
      `additionalProperties: false` — the validator built in ISSUE-005 enforces exactly
      that subset, and a tool that omits it silently opts out of validation.
- [ ] `_check_tool_scopes_resolve` now runs for real. Verify it fails the boot when a scope
      names a missing tool, with a test that registers a partial set.
- [ ] Tool names are unique across agents (the third contract listed in
      `core/contracts.py`, still unregistered).

### FR8 — Traces carry enough for the monitor

- [ ] The existing trace shape (`tool`, `arguments`, `ok`, `error`, `elapsed_ms`) is
      populated for every tool. No change to `BaseAgent` should be needed — if one is, say
      why in the PR rather than editing the loop quietly.
- [ ] A test asserts a real tool round produces a trace with `ok: True` and a non-zero
      elapsed time.

---

## Non-Functional Requirements

- [ ] The default `pytest` run stays free and offline.
- [ ] No new runtime dependency. The schema subset validated in ISSUE-005 is deliberate;
      pulling in `jsonschema` for eight hand-written schemas is not a trade worth making.
- [ ] Both existing guard rails pass unchanged (one door to the model, ASCII-only source).
- [ ] No tool reads `os.environ`; configuration comes through `Settings`.

---

## Acceptance Criteria

Given the corrected scopes from FR1 and an otherwise empty registry,
when the application boots,
then `_check_tool_scopes_resolve` fails naming every scope entry with no registered tool.

Given a problem whose answer is known to the test,
when `build_hint` is called at every level,
then no level's output contains the answer.

Given the same `(quality, ease, interval, repetitions, now)` twice,
when `schedule_review` runs,
then both calls return the identical dict.

Given the same answer and the same pinned rubric,
when `grade_answer` runs twice with the same binary-check results,
then the mark is identical, and the rubric was not regenerated in either run.

Given `PracticeAgent` handles a request,
when the outgoing payload is inspected,
then `tools` contains exactly `build_hint` and `analyze_complexity` — nothing that could
produce a solution.

Given `TutorHandoffAgent` handles a request,
when the result is inspected,
then it contains a structured handoff summary and the gateway recorded zero calls.

Given a tool is called with an argument its schema does not declare,
when the loop runs,
then the model receives a validation error as a tool result and the turn still completes.

---

## Out of Scope

- **MCP server tools** (`materials_search`, `materials_add`, `materials_stats`). They own
  the Chroma client and belong in their own process — plan §3.3's split rule. `ConceptAgent`
  gets `materials_search` back in that issue.
- **`get_due_topics`.** Reads Redis progress state; lands with the memory issue that
  creates the state for it to read.
- **`/chat`.** Still the follow-up after this; the tools are exercised through the
  orchestrator in tests.
- **Skills injection.** The `academic_integrity` skill states in prose what FR3 enforces
  structurally. It is a later issue, and FR3 is the reason it is not urgent.
- **The instance pool and `monitor_penalty`.** Unchanged from ISSUE-005.

---

## Risks

| Risk | Mitigation |
| --- | --- |
| `grade_answer` is described as deterministic in the PR and someone later finds it is not | FR5 requires the residual to be written into the docstring and README, not just known |
| The FR1 rename is bundled with new tools, so a reviewer cannot tell a rename from a behaviour change | FR1 is its own first commit against a passing suite |
| `build_hint` level 3 drifts toward the answer as it is tuned for helpfulness | The no-answer assertion is a test, not a review convention |
| A tool reads the clock or the environment internally and the determinism tests quietly stop meaning anything | FR2 requires `now` to be injected; a guard rail already forbids `os.environ` outside config |
| Eight tools plus schemas plus tests is a large diff | The four with real invariants (build_hint, schedule_review, grade_answer, analyze_complexity) carry the risk; the rest are small. Split the PR if review stalls |

---

## Dependencies

- ISSUE-005 merged: the tool loop, the whitelist, the validator, and the dormant contract
  all exist and are tested against fakes.
- No new services, no schema, no migration.

---

## Definition of Done

- [ ] FR1 merged as its own commit, scopes matching plan §3.3.
- [ ] Eight tools implemented, registered, and schema-validated.
- [ ] `build_hint` no-answer property covered by a test.
- [ ] `schedule_review` table-driven against published SM-2 examples.
- [ ] `grade_answer` reproducibility tested, and its residual non-determinism documented in
      the docstring and the README.
- [ ] `create_handoff_summary` called from `TutorHandoffAgent`, not registered; the empty
      `tool_scope` asserted.
- [ ] `_check_tool_scopes_resolve` verified failing on a partial registry.
- [ ] Tool-name uniqueness contract registered.
- [ ] `pytest` green, no network calls; `ruff` clean; both guard rails unchanged.
- [ ] README gains the tool table and what each agent may call.

---

## Follow-ups (not in this issue)

- The MCP materials server, which returns `materials_search` to `ConceptAgent`'s scope.
- `get_due_topics`, with the memory issue that gives it state to read.
- `/chat`, wiring intent -> routing -> agents -> composer with the token rollup on the
  response.
- The `academic_integrity` skill, stating in prose what FR3 enforces structurally.
