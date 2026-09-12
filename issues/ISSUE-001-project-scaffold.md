# [Task] Project Scaffold — Repository Structure, Runtime Skeleton, and Workflow Plumbing

> Template: `owl_mind_dev_workflow.md` §7. Scope source: `STUDY-ASSISTANT-PLAN.md` §2, §3.5, §4 (Day 1 AM), §5, §11.3.

## Project Fields

| Field | Value |
| --- | --- |
| Type | Task |
| Priority | P1 — every other issue is blocked until this merges |
| Area | DevOps / Platform |
| Status | Ready |
| Milestone | Day 1 — Foundations |
| Owner | TBD |
| Reviewer | TBD |
| Branch | `feature/1-project-scaffold` |

---

## Summary

Create the initial repository structure, runnable FastAPI skeleton, container stack, test
harness, and GitHub workflow plumbing for Owl Mind. No agent logic, no LLM calls, and no
intent recognition — this issue delivers the empty rooms that later issues furnish, plus
the few guard rails that are expensive to retrofit once code exists.

---

## Background / Business Context

Owl Mind is a multi-agent computer-science study assistant: five specialised agents
(Concept, Practice, Planner, Quiz, TutorHandoff) behind a routing layer, with real Model
Context Protocol tool access and end-to-end token accounting. The architecture is
specified in `STUDY-ASSISTANT-PLAN.md`; this repository is a greenfield build of that
plan, not a fork.

Three constraints from the plan are cheap now and costly later, so they belong in the
scaffold rather than in a feature issue:

1. **Package naming.** The reference implementation put its tooling in a top-level
   `mcp/` package, which shadows the official `mcp` SDK on `sys.path`. Any later attempt
   to `import mcp` fails in a confusing way. The scaffold must never create that
   directory name (plan §4, Day 1 AM).
2. **One LLM entry point.** The reference implementation grew nine scattered
   `client.messages.create` call sites, which made token accounting a retrofit across
   five modules. Owl Mind starts with `core/llm_gateway.py` as the only permitted call
   site, enforced by a test (plan §3.5).
3. **Contracts fail at boot.** Taxonomy/template sync and agent tool-scope agreement are
   invariants that should crash at startup, not silently at runtime. The scaffold adds
   the hook and calls it; later issues add the checks (plan §11.3 highlight 十一).

The repository currently has no commits, so this issue also establishes the `main` /
`develop` branch structure the SOP assumes (`owl_mind_dev_workflow.md` §13).

---

## User Story

As an Owl Mind developer,
I want a runnable, tested, containerised project skeleton with the module boundaries and
guard rails already in place,
so that every subsequent issue adds domain logic into a known location instead of
relitigating layout, config, and tooling in each PR.

---

## Functional Requirements

### FR1 — Branch structure

- [ ] Initial commit on `main` (this file, `STUDY-ASSISTANT-PLAN.md`, `owl_mind_dev_workflow.md`, `README.md`, `.gitignore`).
- [ ] `develop` branched from `main` and pushed.
- [ ] Work for this issue happens on `feature/1-project-scaffold`, branched from `develop`, with the PR targeting `develop`.
- [ ] Branch protection (or a written team rule if the plan is on a free tier): no direct pushes to `main`.

### FR2 — Directory layout

- [ ] The tree below exists, every Python package has an `__init__.py`, and every module listed as a stub imports cleanly.

```text
owl_mind/
├── api/
│   └── main.py              # FastAPI app + lifespan + /health          (real)
├── core/
│   ├── config.py            # pydantic-settings, single source of env   (real)
│   ├── contracts.py         # verify_startup_contracts()                (hook only)
│   └── llm_gateway.py       # LLMGateway — sole Anthropic call site     (stub)
├── agents/
│   ├── base.py              # BaseAgent, AgentProfile, AgentType        (stub)
│   ├── orchestrator.py      # AgentOrchestrator                         (stub)
│   └── tools/__init__.py    # AgentToolSpec + registry                  (stub)
├── memory/
│   └── conversation_memory.py                                           (stub)
├── toolkit/                 # ← MUST NOT be named mcp/ (see Background 1)
│   ├── tool_manager.py      # client-side policy: cache/breaker/timeout (stub)
│   └── materials.py         # Chroma-backed materials store             (stub)
├── mcp_servers/
│   └── __init__.py          # MCP server process lands here (Issue: MCP) (empty)
├── evaluation/
│   └── evaluator.py                                                     (stub)
├── monitor/
│   └── performance_monitor.py                                           (stub)
├── skills/
│   └── README.md            # skill file format                         (docs)
└── data/
    └── .gitkeep             # seed materials land here
tests/
├── conftest.py
├── test_health.py
└── test_guardrails.py
```

- [ ] "Stub" means: module docstring citing the plan section it implements, the class or
      function signature, type hints, and `raise NotImplementedError` — **not** an empty
      file. A stub must be importable and must state which issue fills it in.

### FR3 — Configuration

- [ ] `core/config.py` exposes a single `Settings` object (pydantic-settings) read once at
      import; no module reads `os.environ` directly.
- [ ] Settings cover: `anthropic_api_key`, `model`, `redis_url`, `chroma_host`,
      `chroma_port`, `log_level`, `llm_max_concurrency`, `app_env`.
- [ ] `.env.example` is committed with every key present and no real values.
- [ ] `.env` is git-ignored and the app fails fast with a clear message if
      `ANTHROPIC_API_KEY` is missing.

### FR4 — Runtime skeleton

- [ ] `api/main.py` builds a FastAPI app with an `asynccontextmanager` lifespan.
- [ ] Lifespan calls `core.contracts.verify_startup_contracts()`; a failure aborts startup
      with a non-zero exit rather than serving a degraded app.
- [ ] `GET /health` returns `{"status", "app_env", "version", "agents": [...],
      "dependencies": {"redis": ..., "chroma": ...}}`. `agents` is an empty list for now
      and becomes the five-agent roster in a later issue.
- [ ] Dependency checks are real connectivity pings, and a failing dependency yields
      `status: "degraded"` with HTTP 200 — `/health` must stay answerable when Redis is
      down so the failure is diagnosable.
- [ ] Structured logging configured once at startup (level from settings).

### FR5 — Guard rails

- [ ] `core/llm_gateway.py` defines `LLMGateway.complete(component: str, **kwargs)` as a
      stub, documenting the semaphore / usage-capture / Prometheus responsibilities from
      plan §3.5.
- [ ] `tests/test_guardrails.py` fails if `messages.create` appears anywhere under
      `owl_mind/` except `core/llm_gateway.py`.
- [ ] `tests/test_guardrails.py` fails if a top-level `mcp/` directory exists, and
      asserts `import mcp` resolves to the installed SDK (skipped with a clear reason if
      the SDK is not yet installed).
- [ ] `tests/test_guardrails.py` fails if any non-ASCII character appears in `owl_mind/`
      source — the delivery language is English (plan §1 decision 1), and this is the CJK
      leak guard from plan §9 installed before there is anything to leak.

### FR6 — Dependencies

- [ ] `requirements.txt`: `fastapi`, `uvicorn[standard]`, `anthropic`, `pydantic`,
      `pydantic-settings`, `redis`, `chromadb`, `mcp`, `prometheus-client`, `httpx`.
- [ ] `requirements-dev.txt`: `pytest`, `pytest-asyncio`, `ruff`.
- [ ] All versions pinned to a minor range. Python 3.12.

### FR7 — Container stack

- [ ] `Dockerfile` for the API (Python 3.12-slim, non-root user, no secrets baked in).
- [ ] `docker-compose.yml` with three services: `api`, `redis`, `chroma`.
- [ ] **Chroma runs as its own container** (`chromadb/chroma`) rather than embedded in the
      API process, so the MCP materials server can share the same store later without a
      migration.
- [ ] Named volumes for Redis and Chroma persistence; `data/` bind-mounted for seed
      materials.
- [ ] `docker compose up -d --build` yields a healthy `/health` on the published port.

### FR8 — Workflow plumbing

- [ ] `.github/pull_request_template.md` per SOP §15/§23.
- [ ] `.github/ISSUE_TEMPLATE/` containing `task.md`, `story.md`, and `bug.md` derived
      from SOP §7.
- [ ] `README.md` covers: what Owl Mind is, the layout above, local run (venv and Docker),
      how to run tests, and a pointer to `STUDY-ASSISTANT-PLAN.md` and
      `owl_mind_dev_workflow.md`.
- [ ] `.gitignore` covers `.venv/`, `.env`, `__pycache__/`, `.pytest_cache/`, `data/chroma/`,
      `logs/`, `.DS_Store`, `.idea/`.

---

## Non-Functional Requirements

- [ ] No production change before PR approval.
- [ ] All logic version-controlled in `Owl-Mind---Multi-Agent-Study-Assistant`.
- [ ] No credentials, API keys, `.env` files, or vendor data committed.
- [ ] Every stub names the issue that will implement it, so the scaffold cannot be mistaken for working code.
- [ ] Code readable enough for SOP/onboarding use; module docstrings cite the plan section they implement.
- [ ] The scaffold contains **zero** Anthropic API calls — `docker compose up` must cost nothing.
- [ ] Startup time under 5 seconds with dependencies already running.

---

## Acceptance Criteria

Given a clean clone and a populated `.env`,
when the developer runs `docker compose up -d --build`,
then all three services report healthy and `GET /health` returns HTTP 200 with
`status: "ok"`, an empty `agents` list, and both dependencies reporting reachable.

Given the stack is running,
when Redis is stopped with `docker compose stop redis`,
then `GET /health` still returns HTTP 200 with `status: "degraded"` and
`dependencies.redis` reporting unreachable, rather than timing out or returning 500.

Given a developer adds `client.messages.create(...)` to any module other than
`core/llm_gateway.py`,
when `pytest` runs,
then `test_guardrails.py` fails and names the offending file and line.

Given a developer creates a top-level `mcp/` package,
when `pytest` runs,
then `test_guardrails.py` fails with an explanation that the name shadows the MCP SDK.

Given `ANTHROPIC_API_KEY` is absent from the environment,
when the API starts,
then startup aborts with a message naming the missing setting, rather than failing later
on the first model call.

Given a reviewer opens the repository,
when they read `README.md`,
then they can run the stack and the test suite without asking a question.

---

## Test Notes

- [ ] `pytest` green on a clean checkout with dev requirements installed.
- [ ] `docker compose up -d --build` from scratch on Windows 11 with Docker Desktop.
- [ ] `/health` verified with all dependencies up.
- [ ] `/health` verified with Redis stopped, then with Chroma stopped.
- [ ] Missing-`ANTHROPIC_API_KEY` startup failure verified and the error message captured.
- [ ] Guard-rail tests verified to actually fail: temporarily add a `messages.create` call
      and an `mcp/` directory, confirm two red tests, then revert. **A guard rail nobody
      has watched fail is not a guard rail.**
- [ ] `python -c "import mcp; print(mcp.__file__)"` resolves to `site-packages`, not the repo.
- [ ] Every stub module imports without error: `python -c "import owl_mind.agents.orchestrator"` and equivalents.
- [ ] Terminal output of `docker compose ps` and the `/health` response attached to the PR.

---

## Risks

- **Data risk:** none — no user data, no model calls, no persistence beyond empty volumes.
- **Workflow risk:** the SOP assumes branching from `develop`, but the repository has no
  commits yet. Order matters: initial commit on `main` → create and push `develop` →
  branch `feature/1-project-scaffold` from `develop`. Getting this wrong on the first PR
  leaves an orphan history that is annoying to correct later.
- **Deployment risk:** Chroma as a separate container adds a service the reference
  implementation did not have. If the container proves troublesome on Windows, the
  fallback is embedded `PersistentClient` — but that fallback must then be revisited when
  the MCP materials server is built, since two processes cannot share an embedded store.
- **Rollback concern:** none meaningful; the scaffold is additive to an empty repository.
- **Scope risk:** the natural temptation is to "just sketch" an agent or the intent
  recogniser while the file is open. Everything in §Out of Scope below belongs to a later
  issue, and a scaffold PR that includes it cannot be reviewed as a scaffold.

---

## Out of Scope

Deferred to later issues, listed here so the reviewer can reject them if they appear:

- Intent taxonomy, templates, patterns, entity extraction (plan §3.2)
- The five agent implementations and routing (plan §3.1)
- Agent-level tools and the MCP materials server (plan §3.3, §3.4)
- `LLMGateway` internals — semaphore, usage capture, Prometheus counters (plan §3.5)
- Memory layers, skills content, evaluation corpus, monitor collection
- Authentication, response streaming, CI/CD pipelines

---

## Dependencies

- Related issue: none — this is the first issue; everything else depends on it.
- Related PR: —
- Required access: write access to `Easy-Bob/Owl-Mind---Multi-Agent-Study-Assistant`.
- Required environment: Python 3.12, Docker Desktop on Windows 11, an Anthropic API key
  for `.env` (unused by this issue, but `.env.example` must document it).
- Required config: none beyond `.env`.

---

## Definition of Done

Per SOP §12. The two SpecwinOrchard-specific lines (SQL Server authentication, PHI
leakage) have no counterpart in this project and are replaced with the equivalent Owl Mind
checks, marked below — raise it in review if the team prefers to keep the original wording
verbatim.

- [ ] Requirement implemented and checkboxes checked.
- [ ] Code committed to feature branch.
- [ ] Code pushed to GitHub.
- [ ] Pull Request opened against `develop`.
- [ ] PR links the Issue using `Closes #1`.
- [ ] Local/dev testing completed.
- [ ] Reviewer approved the PR.
- [ ] PR merged into the target branch.
- [ ] Issue auto-closed or manually closed with explanation.
- [ ] Project card moved to Done.
- [ ] SOP/README updated if the workflow changed.
- [ ] Changes are tested locally.
- [ ] *(adapted)* Redis and Chroma connectivity verified **from inside the API container**,
      not only from the host, to confirm Docker network configuration.
- [ ] *(adapted)* Logs reviewed before release for leaked API keys, `.env` contents, and
      unexpected errors.

Additional, because this issue changes documentation and project structure (SOP §12):

- [ ] `README.md` and this issue file are consistent with the merged tree.
- [ ] Local deployment instructions verified working; `/health` output attached.
- [ ] New modules, Docker/build changes, dependencies, and every `.env` variable documented.
