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

## Status

**Scaffold.** The shell runs and is tested; the domain logic is not written yet. Every
module marked *stub* raises `NotImplementedError` naming the issue that implements it.

## Quick start

```bash
cp .env.example .env        # then fill in ANTHROPIC_API_KEY
docker compose up -d --build
curl http://localhost:8080/health
```

`/health` always answers with HTTP 200. `status` is `ok` when Redis and Chroma are both
reachable and `degraded` otherwise, with a per-dependency reason -- a health endpoint that
500s when a dependency dies tells you nothing at the moment you most need it.

### Local (no Docker)

```bash
python -m venv .venv
./.venv/Scripts/python -m pip install -r requirements-dev.txt   # Windows
# source .venv/bin/activate && pip install -r requirements-dev.txt   # macOS/Linux

./.venv/Scripts/python -m pytest
./.venv/Scripts/python -m ruff check .
./.venv/Scripts/python -m uvicorn owl_mind.api.main:app --reload --port 8080
```

Redis and Chroma still need to be running for `status: "ok"`; `docker compose up -d redis
chroma` starts just those two.

## Layout

```text
owl_mind/
├── api/main.py              FastAPI app, lifespan, /health
├── core/
│   ├── config.py            the only module that reads the environment
│   ├── contracts.py         startup invariants; failures abort the boot
│   └── llm_gateway.py       the only module permitted to call Anthropic   [stub]
├── agents/
│   ├── base.py              AgentType, AgentProfile, BaseAgent            [stub]
│   ├── orchestrator.py      routing decision, parallel dispatch           [stub]
│   └── tools/               in-process tools over request state           [stub]
├── memory/                  working / episodic / profile layers           [stub]
├── toolkit/                 MCP client policy + materials store           [stub]
├── mcp_servers/             MCP server processes                          [empty]
├── evaluation/              intent metrics, judged dialogue, baselines    [stub]
├── monitor/                 online stats, anomalies, routing penalty      [stub]
├── skills/                  behavioural policy injected per request
└── data/                    seed materials (bind-mounted, not baked in)
tests/                       health, config, and guard rails
```

`toolkit/`, not `mcp/`: a top-level `mcp` package shadows the official SDK on `sys.path`,
and a test fails if anyone recreates it.

## Guard rails

`tests/test_guardrails.py` enforces four rules that are cheap now and expensive to
retrofit. Each one encodes a mistake the reference implementation actually paid for.

| Rule | Why |
| --- | --- |
| `messages.create` only in `core/llm_gateway.py` | one door means token accounting is never a nine-call-site retrofit |
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
