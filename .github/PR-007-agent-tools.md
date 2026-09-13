# [Issue #<n>] Agent tools -- the deterministic layer the roster rests on

<!-- File ISSUE-007 and correct the number above. Established pattern: scaffold
     issue #1 / PR #2, gateway #3 / PR #4, intent #5 / PR #5, orchestration #6. -->

## Summary

Seven in-process tools, registered into the loop ISSUE-005 already built. The loop does not
change. What changes is that a claim made across three merged issues -- that Owl Mind's
guarantees come from deterministic tools rather than from prompt sentences -- stops being
an assertion backed by an empty registry.

`218 passed, 1 skipped` (was 144), ruff clean, all three startup contracts now passing
against a populated registry. No network calls in the default suite.

## Linked Issue

Closes #<n>

## Four commits, FR1 deliberately first

| Commit | |
| --- | --- |
| `22de7a4` | the issue |
| `2fe4046` | **FR1** -- the scope rename, alone, against a passing suite |
| `6baa03b` | the seven tools |
| `be48625` | README tool table |

## FR1 exists because ISSUE-005 got the names wrong

ISSUE-005 declared `tool_scope` early *specifically* so this issue would populate a declared
scope rather than invent one. Four of the eight entries did not match plan section 3.3, and
nothing caught it because `_check_tool_scopes_resolve` returns early while the registry is
empty -- the first tool registered wakes it.

| Was | Now | |
| --- | --- | --- |
| `search_materials` | *removed* | an **MCP** tool: it owns the Chroma client and belongs in its own process |
| `check_step` | `analyze_complexity` | invented |
| `get_progress` | `get_due_topics`, then *removed* | invented, and the plan's version reads Redis state that does not exist yet |
| `generate_quiz` | `generate_quiz_spec` | the tool returns a spec, not the questions -- the distinction is what makes two quizzes on a topic comparable |

`get_due_topics` and `materials_search` arrive with their backing stores rather than ahead
of them. A declared scope naming a tool that cannot work is the same lie as an undeclared
one. Recorded as **H6** in the defect register.

## What is now structural rather than prose

**`build_hint` is the academic-integrity boundary, made mechanical.** The guarantee is not
that output is scanned for an answer -- that only catches phrasings a test author imagined.
It is that **every hint the tool can return is drawn from a table an author wrote**. The
model picks a rung; it does not supply the words, so there is no path from model output to
the returned string. A test asserts that over every reachable hint, across every level and
every topic family. With `build_hint` the only problem-facing tool in PracticeAgent's
scope, "never emit a complete solution" stops depending on the model's cooperation.

**`schedule_review` takes the clock as an argument.** A hidden `datetime.now()` makes "the
same progress always yields the same date" a test that passes on the day it is written and
drifts afterwards.

**`analyze_complexity` never executes the code.** It reads the shape of the source, and the
caveat travels in the result rather than relying on the prompt to add it -- a confident
wrong complexity is worse than no tool.

**`grade_answer` says out loud what it cannot promise.** Plan section 3.1.1 is explicit that
grading is *stabilised*, not deterministic. The arithmetic is exact, the rubric is pinned at
question-generation time and fingerprinted so a regenerated one is detectable, and the
judgement is decomposed into binary checks. But those checks are model judgements and can
differ between runs, and the tool reports that residual in its own payload so a caller
cannot mistake it for arithmetic. This is the honest claim, and the one that survives being
asked about it.

## Three deviations from the issue as written, each deliberate

**FR7's "tool names unique across agents" is not implementable.**
`inspect_request_context` is deliberately shared by all four model-calling roles, and
`register()` already rejects a duplicate name outright, so uniqueness within the registry is
structural rather than checkable. I implemented the invariant that was actually missing --
the other direction: **a registered tool in no scope is never offered to any model**, so it
is dead weight that still costs a reader time. That contract now fails the boot. Reasoning
is in the docstring, not just here.

**Seven tools, not eight.** `create_handoff_summary` is a plain function per FR6.
TutorHandoffAgent has no tool loop to offer one to, and registering it would invite a
`tool_scope` on the one role whose entire value is that it works when the gateway does not.

**I fixed a tool description rather than loosening the test that caught it.** The "every
description says *when* to call it" test failed on `build_hint`, whose description opened
with a noun phrase. The description was the weaker artefact, so it was rewritten. That test
is a knowing proxy and its docstring says so.

## Worth a reviewer's attention

**Six of the seven handlers never dereference `request`.** Only `shared.py` uses it, which
is what those two are for. This means plan section 3.3's stated split rule -- in-process
because they are "pure functions over local `Request` state" -- does not describe what was
built: they are pure functions over *their arguments*. The in-process decision is still
right, on the simpler ground that a process boundary costs serialisation and buys nothing
for arithmetic. But the justification in the plan is thinner than it reads, and it is worth
knowing before repeating it.

A side effect: those six lift cleanly into a standalone package if publishing is ever
wanted. They are already unit-tested without a request.

## Testing

- `pytest` -- 218 passed, 1 skipped, no network calls.
- `ruff check .` -- clean. Both existing guard rails unchanged.
- Determinism asserted by **calling the functions directly**, not through the registry:
  `sm2(**args) == sm2(**args)`, `grade(RUBRIC, checks)` twice, `chain_for` twice. If the
  registration wrapper vanished, every property this issue exists to establish would still
  hold and still be tested.
- `build_hint` output checked against the authored set across 7 topics x 3 levels.
- SM-2 table-driven against the published ladder, plus lapse reset and the ease floor.
- `analyze_complexity` given code that would abort the process if executed.
- Whitelist asserted per role, positively and negatively.
- Both new contract failures verified: a scope naming a missing tool, and an orphan tool.

## Not done

One checkbox remains: recording the FR4 simplification decision in the PR. It was not
taken -- full SM-2 shipped, not the fixed-interval Leitner fallback plan section 11 allows.

`materials_search` and `get_due_topics` remain absent by design, named in the README as
absences rather than quietly missing.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
