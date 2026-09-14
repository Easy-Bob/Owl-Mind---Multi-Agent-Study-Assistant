# [Issue #8] `/chat` -- the first request a real model ever sees

## Summary

Wires `POST /chat`: a student message in, a routed and composed answer out, with the
per-request token rollup on the response. Constructs `IntentRecognizer` at startup --
nothing constructed one before -- and decides what happens to the embedding signal.

Also lands the two Tier A defects from ISSUE-006 that this endpoint turns from theoretical
into user-visible: **F2** (the model signal can be truncated away without raising) and
**F8** (nothing imposes a deadline).

Everything built in the four issues before this one has only ever been called by a fake.
This is where it becomes reachable.

## Linked Issue

Closes #8

## Type of Change

- [x] Feature
- [x] Bug fix
- [x] Documentation
- [x] Test
- [ ] Chore
- [ ] Refactor

## The endpoint

`POST /chat` takes `{message, session_id?, user_id?}` and returns the answer plus the
routing that produced it: `primary_agent`, `supporting_agents`, `dropped_agents`,
`escalated`, `routing_reason`, `tools_used`, and `usage`. Both models are pydantic, so the
schema is in the OpenAPI document rather than in a dict literal — verified:

```text
paths: ['/chat', '/health', '/metrics']
request schema:  #/components/schemas/ChatRequest
response schema: #/components/schemas/ChatResponse
```

The handler owns no domain logic. It recognises an intent, calls `orchestrator.run`, and
maps the result or the failure onto HTTP. Two edges are worth a reviewer's time.

**All three no-agent outcomes return HTTP 200.** The tutor handoff, the clarifying
question, and the off-topic decline. None of them is an error, and a 4xx would put "a
student asked about the weather" in the same bucket as a malformed request. `primary_agent`
is `null` on all three, which is how a client tells them from an answer.

**No failure body carries internals.** The error map deliberately loses information on the
way out — the class that raised, the prompt, and the provider's message stay in the logs.
Asserted, not assumed: the failure test greps the response body for `traceback`,
`anthropic`, the API key, and the exception text of each mapped exception.

| Raised | HTTP | Why |
| --- | --- | --- |
| `PrimaryAgentFailed` | 503 | the chosen specialist did not answer |
| `LLMTimeout` | 504 | the deadline below |
| `anthropic.APIStatusError` / `APIConnectionError` | 502 | upstream said no, or could not be reached |
| `UnknownComponent` | 500 | a bug in this repository, not a bad request |

A failed *primary* is deliberately not degraded to another role. A request routed to
PracticeAgent and quietly answered by ConceptAgent is a different, weaker product
substituted for the one that was asked for.

## F2 -- the intent signal could be truncated away silently

The premise was worth checking rather than assuming, so I checked it against the API
reference: on `claude-sonnet-5`, the configured model, **omitting `thinking` runs adaptive
thinking**, and `max_tokens` caps thinking and response text *together*. The failure chain
is entirely silent:

1. Thinking consumes the 400-token budget; the structured JSON is cut off.
2. `_parse_llm_response` fails `json.loads`, logs a warning, returns `{}`.
3. `_fuse` treats an empty map as "did not contribute" and renormalises over the rest.
4. The request succeeds. The 0.5-weighted signal is gone and nothing raises.

Two changes. The intent call now sends `thinking={"type": "disabled"}` — accepted on this
model, and classification against a fixed 19-intent taxonomy with a schema-constrained
answer is not a reasoning task, so there is nothing for thinking to improve. And the
gateway now counts `stop_reason == "max_tokens"` as `outcome="truncated"` rather than
folding it into `success`:

> A truncated response is a third outcome, not a success and not an error. The HTTP call
> succeeded; the content is incomplete. Counted as `success` it is invisible.

Label cardinality stays closed — `stop_reason` has a handful of values and only this one is
folded in.

**Note for whoever changes `model` next.** Disabled thinking is model-dependent: some models
reject it with a 400, and on others it is accepted only below a certain effort. A comment
now sits on the `model` setting saying so, because the failure would otherwise appear as an
unexplained 400 on every request.

## F8 -- the deadline, and why not the SDK's

`llm_timeout_seconds` (default 60) is enforced with `asyncio.timeout` wrapping **the
semaphore acquire as well as the call**. Two deliberate choices:

- **Queue time is inside the deadline.** The semaphore is per process, so a burst puts
  every caller behind it — the wait is the half that actually grows under load. A deadline
  that started once a slot was held would leave it unbounded.
- **This is not the SDK's `timeout` parameter.** The SDK *retries* timeouts, so its
  effective ceiling is `timeout x (max_retries + 1)` — it cannot state a bound.
  `asyncio.timeout` can.

**Worst-case wall clock for one composite request, stated as the issue asks:**

```text
intent (1 call)  ->  3 agents in parallel, each up to MAX_TOOL_ROUNDS = 3 sequential calls
                 ->  composer (1 call)

sequential depth = 1 + 3 + 1 = 5 calls
worst case       = 5 x 60s = 300s   (11 model calls in total)
```

**Five minutes is too long, and the deadline as built cannot fix it** — it bounds a call,
not a request. Flagged rather than patched: the right fix is a per-request budget, which
wants a measured p95 to size it, and the live test below is the first thing that could
produce one. Filed for the evaluation issue.

## A nesting bug caught before it shipped

`/chat` opens a `request_scope` around intent recognition *and* orchestration; the
orchestrator opens one around its own fan-out. The inner scope rebound the contextvar, so
the outer rollup would have held the intent call **alone** — every composite turn
under-reporting its own cost by three calls, with nothing failing and no test noticing.

`request_scope` now reuses an active rollup instead of rebinding. One rollup per request,
no matter how many layers ask for one. A test asserts the inner scope yields the same
object and that both calls land in it.

## FR3 -- the embedding signal, decided out loud

**Wired it.** The alternative was shipping a recogniser whose `WEIGHTS` name a signal that
never votes — 0.35 of the designed panel silently absent on every production request.

Seeding does not take the boot down with it. A startup that dies because a *degraded*
dependency is unreachable contradicts `/health`'s entire design: the endpoint exists to
report Chroma as unreachable, which it cannot do from a process that refused to start.
Recognition is correct without the index — `_fuse` divides by the weight that contributed,
so the thresholds still mean what they say on two signals. It is less robust to paraphrase,
which is a degradation, not an outage. `INTENT_INDEX_ENABLED` turns it off deliberately.

## Found while testing: a regex outranking the model

This is the finding I most want a reviewer to look at. It is **recorded, not fixed.**

A composite test failed in a way I did not predict. On *"explain BFS then quiz me"* the
model ranks `concept_explain` 0.88 over `quiz_request` 0.74 — and the reply came back with
**quiz as primary**:

```text
pattern signal: {'quiz_request': 0.95}       # nothing matches the explanation half

quiz_request    (0.5*0.74 + 0.15*0.95) / 0.65 = 0.788
concept_explain (0.5*0.88 + 0.00     ) / 0.65 = 0.677
```

With the embedding signal absent — the production shape whenever Chroma is down — fusion
renormalises over llm+pattern, and **a regex at weight 0.15 reversed a 0.14 model gap and
took the lead answer.**

This is the risk the `WEIGHTS` docstring predicted ("the composite case now separates by a
much narrower margin") arriving in a stronger form than predicted: not narrowed, reversed.
Fan-out is unaffected — both agents still run, so the multi-agent claim holds — but which
specialist *leads* a composite reply is being decided by a keyword, on exactly the class of
request the fan-out exists to serve.

**Why it is not fixed here.** The question is whether pattern deserves 0.15, or whether
corroboration should gate the *primary* the way it already gates supporting agents. That is
what the labelled corpus answers; picking a number now would be guessing with extra steps.
Filed as **ISSUE-006 F13** and pinned by
`test_a_pattern_rule_can_outrank_the_model_on_the_primary`, so the behaviour is stated
rather than stumbled into. The fan-out test was loosened to assert the agent *set*, so a
future weight change fails in the place that explains why.

## Testing

- [x] `pytest` -- **238 passed, 1 skipped, 2 deselected**.
- [x] `ruff check .` -- clean.
- [x] Both guard rails unchanged and passing.
- [x] `/chat` present in the OpenAPI document with both schemas (output above).
- [x] Tested failure case: every mapped exception returns its status code with no
      traceback, prompt, or key in the body.
- [x] Tested failure case: a 4001-character message is rejected at 422 **before any model
      call** (`assert fake.calls == []`).
- [x] Tested degradation: boot with Chroma unreachable serves, reports `degraded`, and
      answers on two signals.
- [ ] **`pytest -m live` has never been run.** See below.

The chat tests run the *real* stack — recogniser, fusion, routing, dispatch, composition —
against a fake Anthropic client. Only the network is faked, so a break in routing or
attribution fails there rather than passing on a mock taught the expected answer. The fake
dispatches on `output_config`, which is the only thing distinguishing the classifier call
from an agent call without the fake needing to know what a component is.

Evidence for the attribution claim, which is the one this PR most needs to hold:

```python
assert set(components) == {"intent", "agent:concept", "agent:quiz", "composer"}
assert body["usage"]["llm_calls"] == 4
```

## Deployment Notes

- New environment variables: **Yes** — `LLM_TIMEOUT_SECONDS` (default 60),
  `INTENT_INDEX_ENABLED` (default true). Both have working defaults; `.env.example` updated.
- New services or volumes: **No.**
- Requires image rebuild: **Yes** (new module).
- Migration: none.

## Risk / Rollback

- **Risk: this is the first code path that spends real money.** A composite request is up to
  11 model calls. `/metrics` was built for exactly this and is live; `llm_calls_total` per
  component is the number to watch on day one.
- **Risk: `/chat` is unauthenticated**, like `/metrics`. Called out in the README next to the
  existing warning. `message` is capped at 4000 characters so the open endpoint is not an
  unbounded token bill, but that is a mitigation, not a fix. Auth and rate limiting are
  their own issue and belong in front of this before it is exposed anywhere public.
- **Risk: the 300s worst case above.** Real, unmitigated, and stated rather than buried.
- **Rollback:** revert the merge commit. No persistence, no schema, no migration.

## Not done

- **The live test has never been run.** It is written, marked `live`, and deselected by
  default, but `.env` holds a placeholder key. It is the first test in this repository that
  could fail for a reason the fakes cannot produce — content-block shapes, real
  `stop_reason` values, actual latency — so it should be run before merge, not after.
- **Memory.** `session_id` is accepted, echoed, and inert; a test asserts it does not reach
  the model, so the field's inertness is a stated property rather than something a reader
  has to infer.
- **Streaming, auth, rate limiting**, and the per-request budget the 300s figure argues for.

## Decisions for the reviewer

1. **All three no-agent paths are 200.** If you would rather the decline were a 422, say so
   — it is a one-line change and a real product judgement, not a technical one.
2. **A failed primary is a 503, not a degraded answer from another role.** The reference
   implementation degraded to a general agent; this roster has none, and substituting a
   different specialist silently seemed worse than failing honestly.
3. **F13 is filed, not fixed.** If you would rather block on it, the eval corpus is the
   prerequisite and this PR does not depend on the answer.
