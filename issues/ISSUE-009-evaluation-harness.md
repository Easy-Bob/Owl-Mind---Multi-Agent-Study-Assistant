# [Task] Evaluation — the first thing that can say whether any of this is good

> Template: `owl_mind_dev_workflow.md` §7. Scope source: `STUDY-ASSISTANT-PLAN.md` §4 (Day 4), §11.2.
> Depends on: ISSUE-008 (`/chat`) merged.

## Project Fields

| Field | Value |
| --- | --- |
| Type | Task |
| Priority | P1 — four deferred decisions are blocked on it |
| Area | Evaluation |
| Status | Ready |
| Milestone | Day 4 — Measurement |
| Owner | TBD |
| Reviewer | TBD |
| Branch | `feature/evaluation-harness` |

---

## Summary

A labelled corpus and a harness that reports per-class intent accuracy, routing correctness,
and judged answer quality — with a pinned baseline so "regression" means *worse than the
release* rather than *worse than last Tuesday*.

This is the issue that turns four written-down guesses into measurements.

---

## Background / Business Context

**The request path is complete and the feedback loops are not.** Routing, dispatch,
permission boundaries and cost attribution all work end to end; nothing in the repository
can say whether the routing is any *good*. Every tuning decision so far has been deferred to
this issue, by name and in code comments:

| Deferred | Where it is written down | What it needs |
| --- | --- | --- |
| `WEIGHTS = 0.5/0.35/0.15` | `intent_recognizer.py` docstring | per-class accuracy at several weightings |
| **F13** — a regex outranks the model on the primary | ISSUE-006, pinned by a test | whether corroboration should gate the primary |
| `llm_timeout_seconds = 60` | `config.py` — "a first guess" | a measured p95 |
| Cascade ordering | ISSUE-004 | accuracy/token A/B, which needs this corpus |

Three of the four are *already* shipped behaviour. They are running in front of a user on
numbers nobody measured.

The evaluator stub records three requirements from the reference implementation, and they
stand as written — the baseline was overwritten every run, judge failures were averaged in
as scores, and nothing asserted composite routing. All three are FRs below.

---

## User Story

As the person who has to defend this system in an interview,
I want a number for how often it routes correctly and a way to see what it got wrong,
so that "the fusion weights are tuned" is a claim with evidence rather than a hope.

---

## Functional Requirements

### FR1 — The corpus

- [ ] Labelled utterances in a version-controlled file (JSONL), each carrying `message`,
      `expected_intent`, and — where routing is the point — `expected_primary_agent` and
      `expected_supporting_agents`.
- [ ] **Held-out split, fixed by a seed and committed.** Tuning against the same cases used
      to report the score is how a number becomes decoration.
- [ ] Target ~10 cases per intent (≈190). State plainly in the README that per-class
      metrics at n=10 have wide confidence intervals and are directional, not decisive.
      Inflating to 400 by paraphrase would make the intervals look narrower without adding
      information.
- [ ] At least 25 **out-of-scope** cases labelled `other`. OOS is the failure mode that
      matters most in production — real students say things the taxonomy does not cover —
      and folding it into the average hides it.
- [ ] At least 15 **composite** cases with two intents in different groups.

### FR2 — Contamination is a test, not a convention

- [ ] **No corpus case may appear in `_TEMPLATES`.** The templates *are* the embedding
      index, so an overlapping case means the embedding signal has memorised the test set
      and the 0.35 leg scores itself. Asserted by a test comparing normalised strings.
- [ ] The same test must fail on near-duplicates, not just exact matches — embed both sets
      and fail above a similarity threshold. An exact-match check is trivially defeated by
      changing one word, which is exactly what a well-meaning person does when the test goes
      red.
- [ ] If tier-0-style caching ever lands, corpus cases must be excluded from it for the same
      reason. Note it in the harness docstring now.

### FR3 — Intent metrics that can be acted on

- [ ] **Per-class precision, recall and F1** — not overall accuracy. A single number at 19
      classes hides which pairs are confused, and the confused pairs are the actionable
      output.
- [ ] A confusion matrix written to the report. The `answer_submission` / `homework_check`
      boundary and the `explain_back` / `answer_submission` overlap were both flagged as
      likely confusions when those intents were added; this is where that prediction gets
      checked.
- [ ] OOS reported separately: what fraction of out-of-scope messages correctly reach
      `other`, and what fraction of in-scope messages are wrongly declined.
- [ ] **Per-signal breakdown.** For each case, what did llm, embedding and pattern each say,
      and did fusion improve on the best single signal? If it did not, the three-signal
      design is costing money for nothing and that should be visible.

### FR4 — Routing metrics

- [ ] Primary-agent accuracy, and supporting-agent set accuracy separately. They fail
      differently: a wrong primary is a wrong answer, a missing supporter is a thin one.
- [ ] **At least five composite cases asserting `supporting_agents` is non-empty**, per the
      evaluator stub and plan §11.2. Without that assertion a five-prompt switch statement
      passes the whole suite.
- [ ] Report how often a message engages 1, 2, and 3 agents. If the answer is "almost always
      1", the multi-agent architecture is not earning its complexity and the honest move is
      to say so.

### FR5 — Settle F13 and the weights with data

- [ ] Sweep `WEIGHTS` over a small grid and report per-class F1 for each. Pick the winner
      from the held-out split, once.
- [ ] **Answer F13 specifically:** should corroboration gate the *primary* the way it
      already gates supporting agents? Report primary accuracy with and without that gate.
- [ ] Whatever wins, replace the "untuned starting points" comment with the measured basis
      and the date. A tuned constant with no provenance is a guess with better PR.

### FR6 — Judged answer quality

- [ ] LLM-as-judge over dialogue cases, through the gateway under `component="judge"`
      (already in `KNOWN_COMPONENTS`).
- [ ] **Judge failures are excluded from the mean and reported as a count.** Averaging a
      failure in as a zero quietly depresses every result and looks like a quality
      regression.
- [ ] **Name the self-preference problem in the report.** The judge is the same model family
      as the agents being judged, so it is not an independent referee. Mitigate what is
      cheap to mitigate — score against a rubric with binary checks rather than a 1-10
      vibe — and state the limitation rather than implying the number is objective.
- [ ] Judge only the dialogue subset. Judging every case is the expensive way to learn
      nothing new.

### FR7 — The pinned baseline

- [ ] Results write to a timestamped run file; the **baseline is a separate, committed file
      that a run never overwrites**. Promoting a run to baseline is an explicit command.
- [ ] `--compare` reports deltas against the baseline and exits non-zero if a per-class F1
      drops by more than a stated margin.
- [ ] The margin is a config value with the reasoning written next to it, not a magic number.

### FR8 — Cost and latency, measured

- [ ] The run reports tokens and wall clock per case, by component, from the rollup that
      already exists.
- [ ] **Report the p95 that `llm_timeout_seconds` should have been set from**, and update
      the default if the measurement disagrees with 60s.
- [ ] Report mean model calls per request. The worst case is 11; the mean is the number that
      predicts the bill.

---

## Non-Functional Requirements

- [ ] The default `pytest` run stays free and offline. **The harness is not a test** — it is
      a script that costs money, run deliberately.
- [ ] One command to run it, one file of output, both documented in the README.
- [ ] No corpus content in the repository that a student would recognise from a real course
      — write the cases, do not scrape them.
- [ ] Both guard rails pass unchanged.

---

## Acceptance Criteria

Given the corpus and a committed baseline,
when the harness runs,
then it writes per-class precision/recall/F1, a confusion matrix, routing accuracy, judged
quality with the failure count reported separately, and a token/latency summary.

Given a corpus case that also appears in `_TEMPLATES`,
when the contamination test runs,
then it fails naming the case — and it also fails when the case has been reworded slightly.

Given a change that makes one intent's F1 drop past the margin,
when the harness runs with `--compare`,
then it exits non-zero naming the class and both numbers.

Given the weight sweep,
when it completes,
then the chosen weights are justified in the docstring by a measurement and a date, and F13
is either fixed or closed with evidence that it does not matter.

Given a judge call that fails,
when the run completes,
then the mean excludes it and the report states how many failed.

---

## Out of Scope

- **Memory and multi-turn.** Every case is single-turn. Multi-turn evaluation needs
  conversation state that does not exist yet, and is its own issue.
- **Automated tuning.** A grid sweep reported to a human, not a loop that edits constants.
- **CI integration.** It costs money per run; wiring it to every push is a decision to make
  deliberately, after the first few runs show what it costs.
- **The cascade A/B** (ISSUE-004). This issue builds the corpus that unblocks it; the
  benchmark itself stays deferred.

---

## Risks

| Risk | Mitigation |
| --- | --- |
| **The corpus is written by the same model that classifies it.** Generated cases test self-consistency, not correctness — the classifier will ace phrasings its own family produced. | Write the OOS and composite cases by hand; if generated cases are used for the bulk, have a human edit every one and say so in the README. This limitation must be stated, not buried. |
| n=10 per class makes per-class metrics noisy | Stated in the report itself, not just the issue. Report counts alongside rates so a "0.80 F1" that is 4/5 is visibly 4/5 |
| Tuning on the test split | Split committed and seeded; the sweep reads train only |
| The judge flatters its own family | Rubric with binary checks rather than a holistic score; limitation named in the report |
| The harness becomes a test and gets run on every push | It is a script, not a test. Called out in the NFRs |
| **The result is unflattering** | That is the point. A harness that cannot return a bad number is decoration — if fan-out turns out to fire on 5% of traffic, the plan's headline claim needs revisiting and it is better to know |

---

## Definition of Done

- [ ] Requirement implemented and checkboxes checked.
- [ ] Code committed to feature branch and pushed.
- [ ] PR opened, linked with `Closes #<number>`, reviewed, merged.
- [ ] Local testing completed; `pytest` green offline; `ruff` clean.
- [ ] *(adapted)* Redis and Chroma connectivity verified from inside the API container.
- [ ] *(adapted)* Logs reviewed for leaked API keys and `.env` contents.

Additional:

- [ ] **The first real run is committed as the baseline**, with its cost in the PR.
- [ ] F13 closed in ISSUE-006 — fixed, or closed with evidence it does not matter.
- [ ] `WEIGHTS` and `llm_timeout_seconds` carry measured provenance.
- [ ] README documents the command, the output, and the n=10 caveat.

---

## Follow-ups (not in this issue)

- ISSUE-004, the cascade accuracy/token A/B, which this corpus unblocks.
- Memory, and multi-turn evaluation on top of it.
- Prometheus and Grafana in compose — nothing currently scrapes `/metrics`, so every counter
  resets on restart and no one has ever seen a value.
- Auth and rate limiting, before this is exposed anywhere public.
