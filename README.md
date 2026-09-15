# Owl Mind

A multi-agent computer-science study assistant: five specialised agents behind a routing
layer, with real Model Context Protocol tool access and end-to-end token accounting.

| Agent | Owns | Hard boundary |
| --- | --- | --- |
| ConceptAgent | explanations, analogies, worked examples | cites materials; never invents APIs |
| PracticeAgent | Socratic hints on problems | **never emits a complete solution** |
| PlannerAgent | study plans, spaced repetition | scheduling is arithmetic, never LLM-guessed |
| QuizAgent | quiz generation and grading | grading must be reproducible |
| TutorHandoffAgent | escalation to a human TA | **no LLM call** -- works during a model outage |

Every split is justified by a capability boundary, not a topic: each agent is forbidden
something another is allowed, and the whitelist enforces it before the model is called.

## What each agent may call

Tools are the layer the roster's guarantees actually rest on. `tool_scope` is applied when
the request payload is built, so a tool outside it is absent from the request rather than
discouraged in a prompt.

| Tool | Who may call it | What it guarantees |
| --- | --- | --- |
| `get_prerequisites` | Concept | Static graph. An unknown topic returns `known: false` rather than an invented chain |
| `build_hint` | Practice | Every hint comes from an authored table. **No level is the solution**, and there is no level 4 |
| `analyze_complexity` | Practice | Reads the shape of the source. **Never executes it**; the caveat travels in the result |
| `schedule_review` | Planner | SM-2 arithmetic with the clock as an argument. Same progress, same date, always |
| `generate_quiz_spec` | Quiz | The shape of a quiz, not its questions -- what makes two quizzes on a topic comparable |
| `grade_answer` | Quiz | Weights summed in code. See the caveat below |
| `inspect_request_context` | all four | Reports identifiers as present or absent, never by value |

`create_handoff_summary` is deliberately **not** a registered tool. TutorHandoffAgent makes
no model call, so it has no tool loop to offer one to -- and a `tool_scope` on that role
would be one edit away from a gateway call.

**Grading is stabilised, not deterministic.** The arithmetic is exact and the rubric is
pinned at question-generation time and fingerprinted, so a rubric regenerated at grading
time is detectable. But the per-rubric-point judgements are model judgements and can differ
between runs. `grade_answer` reports that residual in its own result rather than letting a
caller mistake it for arithmetic.

Two tools named in the plan are absent until they have something to read: `materials_search`
(MCP -- it owns the Chroma client) and `get_due_topics` (Redis progress state). A declared
scope naming a tool that cannot work is the same lie as an undeclared one.

## Status

**Routing works end to end.** Intent recognition and the five agents are implemented and
tested; memory, MCP tooling, evaluation and the monitor are still stubs, and every module
marked *stub* raises `NotImplementedError` naming the issue that implements it.

`POST /chat` is live. Three kinds of request are answered **without calling a model at
all**: an explicit ask for a human, an ambiguous message (answered with a clarifying
question built from the intent distribution), and an off-topic one (declined). All three
return HTTP 200 with `primary_agent: null` -- none of them is an error.

## Quick start

```bash
cp .env.example .env        # then fill in ANTHROPIC_API_KEY
docker compose up -d --build
curl http://localhost:8080/health

curl -X POST http://localhost:8080/chat \
  -H 'content-type: application/json' \
  -d '{"message": "explain BFS and then quiz me on it"}'
```

The reply carries the answer plus the routing that produced it -- `primary_agent`,
`supporting_agents`, `routing_reason`, and a `usage` rollup naming every component that
spent tokens on the turn:

```json
{
  "primary_agent": "quiz",
  "supporting_agents": ["concept"],
  "usage": {
    "llm_calls": 4,
    "tokens_by_component": {
      "intent": 150, "agent:concept": 280, "agent:quiz": 280, "composer": 190
    }
  }
}
```

> **`/chat` and `/metrics` are unauthenticated**, like everything else here. Fine on a
> laptop; auth and rate limiting are their own issue and belong in front of this before
> it is exposed anywhere public. `message` is capped at 4000 characters -- an unbounded
> text field on an open endpoint is a token bill with a URL.

> `session_id` is accepted and echoed but does nothing yet: memory is a later issue, so
> two turns sharing a session are not related to each other.

`GET /metrics` exposes Prometheus counters for model usage:
`llm_tokens_total{component,model,direction}`, `llm_calls_total{component,model,outcome}`,
and `llm_latency_ms{component}`. Every model call is attributed to a component, which is
what makes "where is the cost going" a query rather than a guess.

A fourth counter, `llm_unattributed_calls_total{component,model,reason}`, counts calls
whose token usage the provider never returned -- a failed call has no usage object, so
its tokens cannot be known. It bounds the gap rather than closing it: token totals are
complete to within that count, and `/chat` surfaces `unattributed_calls` on any request
where it is non-zero.

`outcome` has three values, not two: `success`, `error`, and `truncated`. A response cut
off at `max_tokens` is neither -- the call succeeded and the content is incomplete -- and
counting it as a success is how it stays invisible. `llm_latency_ms` includes time spent
waiting for a semaphore slot, so it measures what the caller actually waited, though it
does not yet separate queue time from model time.

`/health` always answers with HTTP 200. `status` is `ok` when Redis and Chroma are both
reachable and `degraded` otherwise, with a per-dependency reason -- a health endpoint that
500s when a dependency dies tells you nothing at the moment you most need it.

### Local (no Docker)

```bash
python -m venv .venv
./.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows
# source .venv/bin/activate && pip install -r requirements-dev.txt   # macOS/Linux

./.venv/Scripts/python -m pytest            # free and offline: no network calls
./.venv/Scripts/python -m pytest -m live    # ONE real API call; costs a fraction of a cent
./.venv/Scripts/python -m ruff check .
./.venv/Scripts/python -m uvicorn owl_mind.api.main:app --reload --port 8080
```

Redis and Chroma still need to be running for `status: "ok"`; `docker compose up -d redis
chroma` starts just those two.

## Layout

```text
owl_mind/
├── api/main.py              FastAPI app, lifespan, /health, /metrics
├── api/chat.py              POST /chat -- routing, composition, error mapping
├── core/
│   ├── config.py            the only module that reads the environment
│   ├── contracts.py         startup invariants; failures abort the boot
│   ├── intent_recognizer.py three-way signal fusion over 16 intents
│   └── llm_gateway.py       the only module permitted to call Anthropic
├── agents/
│   ├── base.py              BaseAgent: tool loop, whitelist, statistics
│   ├── roster.py            the five profiles and their implementations
│   ├── orchestrator.py      routing decision, parallel dispatch, degradation
│   ├── composer.py          merges several agent answers into one reply
│   └── tools/               deterministic (request, args) -> dict tools
├── memory/                  working / episodic / profile layers           [stub]
├── toolkit/                 MCP client policy + materials store           [stub]
├── mcp_servers/             MCP server processes                          [empty]
├── evaluation/              intent metrics, judged dialogue, baselines    [stub]
├── monitor/                 online stats, anomalies, routing penalty      [stub]
├── skills/                  behavioural policy injected per request
└── data/                    seed materials (bind-mounted, not baked in)
tests/                       health, config, intent, agents, and guard rails
```

`toolkit/`, not `mcp/`: a top-level `mcp` package shadows the official SDK on `sys.path`,
and a test fails if anyone recreates it.

## Guard rails

`tests/test_guardrails.py` enforces five rules that are cheap now and expensive to
retrofit. Each one encodes a mistake the reference implementation actually paid for.

| Rule | Why |
| --- | --- |
| `messages.create` only in `core/llm_gateway.py` | one door means token accounting is never a nine-call-site retrofit |
| no `temperature` / `top_p` / `top_k` | rejected with a 400 by the current models; reproducibility comes from deterministic tools |
| no top-level `mcp/` | it shadows the SDK, and the resulting import error is unreadable |
| `os.environ` only in `core/config.py` | settings have one source, so a new key is trustworthy everywhere |
| source is ASCII-only | delivery language is English; the leak never happens rather than being swept for later |

They are verified by deliberate violation, not assumed: a guard rail nobody has watched
fail is not a guard rail.

## Configuration

Every key lives in [.env.example](.env.example). A missing `ANTHROPIC_API_KEY` aborts
startup with a message naming the setting, rather than surfacing on the first model call.

## Documents

| File | Purpose |
| --- | --- |
| [STUDY-ASSISTANT-PLAN.md](STUDY-ASSISTANT-PLAN.md) | architecture, agent roster, MCP topology, day plan |
| [owl_mind_dev_workflow.md](owl_mind_dev_workflow.md) | issue / branch / PR / review SOP |
| [issues/](issues/) | issue specifications, one file per issue |

## Contributing

Branch from `develop`, never commit to `main`, and link every PR to its issue with
`Closes #<number>`. See [owl_mind_dev_workflow.md](owl_mind_dev_workflow.md).
