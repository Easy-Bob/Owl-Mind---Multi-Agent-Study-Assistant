# [Issue #1] Project scaffold -- runtime skeleton, container stack, guard rails

## Summary

Creates the Owl Mind repository structure: a runnable FastAPI skeleton with `/health`,
a single configuration entry point, a startup-contract hook, containerised Redis and
Chroma, a test harness, and the GitHub workflow templates. Domain logic is deliberately
absent -- every module marked *stub* raises `NotImplementedError` naming the issue that
implements it.

Also included: four guard-rail tests that enforce constraints which are cheap to add now
and expensive to retrofit. Each one encodes a mistake the reference implementation
actually paid for.

## Linked Issue

Closes #1

## Type of Change

- [x] Feature
- [ ] Bug fix
- [ ] Chore
- [x] Documentation
- [ ] Refactor
- [x] Test

## What Changed

**Runtime**

- `owl_mind/api/main.py` -- FastAPI app, lifespan, structured logging, `/health`.
  Health always answers HTTP 200 and reports `ok` / `degraded` with a per-dependency
  reason; a health endpoint that 500s when Redis dies tells you nothing at the moment you
  most need it.
- `owl_mind/core/config.py` -- pydantic-settings, the only module permitted to read the
  environment. A missing `ANTHROPIC_API_KEY` aborts startup naming the setting rather than
  surfacing on the first model call.
- `owl_mind/core/contracts.py` -- startup invariant registry. Reports *all* violations at
  once, since fixing one config error only to be shown the next is a poor way to spend a
  morning. Checks arrive with the issues that create the things being checked.
- `owl_mind/core/llm_gateway.py` -- stub, but present from commit one so no second call
  site can appear.
- Stubs with real signatures and docstrings for `agents/`, `memory/`, `toolkit/`,
  `evaluation/`, `monitor/`. Each imports cleanly and cites its plan section.

**Guard rails** (`tests/test_guardrails.py`)

| Rule | Rationale |
| --- | --- |
| `messages.create` only in `core/llm_gateway.py` | the reference implementation grew nine call sites across five modules; token accounting then meant finding all nine |
| no top-level `mcp/` package | it shadows the official SDK on `sys.path`, and the import error is unreadable |
| `os.environ` only in `core/config.py` | a single config source is only trustworthy if nothing bypasses it |
| ASCII-only sources | delivery language is English; plan section 9 scheduled a CJK sweep for the last day, which on a greenfield repo is just a test |

**Infrastructure**

- `Dockerfile` -- python:3.12-slim, non-root (uid 10001), no secrets baked in.
- `docker-compose.yml` -- `api`, `redis`, `chroma`, named volumes, health-gated
  `depends_on`.
- `pyproject.toml` -- pytest (`pythonpath`, asyncio auto mode) and ruff config.
- `requirements.txt` / `requirements-dev.txt`.
- `.github/` -- PR template and three issue templates derived from the SOP.

## Testing

- [x] `pytest` -- 15 passed.
- [x] `ruff check .` -- clean.
- [x] Application verified under uvicorn: contracts run, `/health` returns 200.
- [x] Tested failure case: `/health` with both dependencies down returns `degraded`, not
      a 500 or a hang.
- [x] Tested failure case: missing `ANTHROPIC_API_KEY` exits 1 with a message naming the
      setting.
- [x] **Guard rails verified by deliberate violation** -- see evidence below.
- [x] `docker compose up -d --build` -- all three services healthy, `/health` reports
      `ok`, and data survives a `down`/`up` cycle. Two compose bugs found and fixed in
      the process; see below.

## Screenshots / Evidence

`GET /health` under uvicorn, with Redis and Chroma deliberately absent:

```json
{
  "status": "degraded",
  "app_env": "local",
  "version": "0.1.0",
  "agents": [],
  "contracts": [],
  "dependencies": {
    "redis":  {"reachable": false, "detail": "TimeoutError: Timeout connecting to server"},
    "chroma": {"reachable": false, "detail": "ConnectTimeout: "}
  }
}
```

Startup log:

```text
INFO  owl_mind.core.contracts: startup contracts verified (0 registered)
INFO  owl_mind: Owl Mind 0.1.0 starting (env=local, model=claude-sonnet-5)
INFO  Application startup complete.
```

Missing API key:

```text
ERROR: Owl Mind cannot start. Missing or invalid settings: ANTHROPIC_API_KEY.
       Copy .env.example to .env and fill it in.
exit code: 1
```

Guard rails, with a `messages.create` call, an `mcp/` directory, a CJK comment and an
`os.environ` read temporarily injected -- all four failed with file and line, then the
probes were reverted:

```text
FAILED tests/test_guardrails.py::test_anthropic_is_called_only_through_the_gateway
  owl_mind/monitor/performance_monitor.py:55: return client.messages.create(model="x")
FAILED tests/test_guardrails.py::test_no_local_package_shadows_the_mcp_sdk
FAILED tests/test_guardrails.py::test_mcp_import_resolves_to_the_installed_sdk
FAILED tests/test_guardrails.py::test_sources_are_ascii_only
  owl_mind/evaluation/evaluator.py:50: # probe: non-ascii ...
FAILED tests/test_guardrails.py::test_environment_is_read_only_by_the_config_module
  owl_mind/memory/conversation_memory.py:59: _ = os.environ.get("X")
```

This exercise found a real bug in my own first version of the SDK check: it asserted the
module resolved *outside the repository*, which is wrong when `.venv` lives inside the
repo. It now asserts resolution from `site-packages`.

Clean state:

```text
15 passed, 2 warnings in 10.41s
All checks passed!            # ruff
```

## Deployment Notes

- New environment variables: **Yes** -- see `.env.example`. `ANTHROPIC_API_KEY` required;
  everything else has a working default.
- New services or volumes: **Yes** -- `redis` and `chroma` services, `redis-data` and
  `chroma-data` volumes.
- Requires image rebuild: **Yes** (first build).
- Deployment steps: `cp .env.example .env`, fill in the key, `docker compose up -d --build`.

Verified end to end. Two bugs surfaced only once the stack actually ran, both fixed in
this branch:

1. **The Chroma healthcheck could never pass.** It used `curl`, and the image ships no
   curl, wget, or python3 -- only bash. Every probe returned `curl: not found`, the service
   stayed `unhealthy`, and `api` (gated on `service_healthy`) never started, failing with
   `dependency chroma failed to start` after 119s. The service itself was fine the whole
   time. Replaced with a bash `/dev/tcp` probe, which needs nothing the image lacks.
2. **The Chroma volume was mounted at the wrong path.** This image persists to `/data`
   (verified: `/data/chroma.sqlite3`), but the named volume was mounted at
   `/chroma/chroma`, which stayed empty. Data was going to the container's writable layer,
   where `docker compose down` destroys it. Silent data loss, invisible until the first
   time anyone restarted the stack with materials ingested.

## Risk / Rollback

- **Risk:** low. No user data, no model calls, no persistence beyond empty volumes. The
  scaffold costs nothing to run -- there are zero Anthropic API calls in this PR.
- **Risk:** retired. Chroma is now pinned by digest
  (`sha256:1e0b73a1...`, server API version 1.0.0) rather than tracking `latest`, so the
  image CI builds is the image tested here.
- **Rollback:** additive to an empty repository; revert the merge commit.

## Decisions for the reviewer

Five judgement calls, flagged rather than buried:

1. **Adapted Definition of Done.** SOP section 12 says the DoD is not modified, but two of
   its items ("SQL Server authentication", "PHI leakage") are SpecwinOrchard-specific and
   have no counterpart here. They are replaced with the structural equivalents -- Redis and
   Chroma connectivity verified *from inside the API container*, and logs reviewed for
   leaked API keys -- and marked `*(adapted)*` in the issue. **Worth settling once, since
   every future issue inherits it.**
2. **Dependency pins are major-bounded, not exact.** Guessing minor versions I could not
   verify seemed worse than declaring the bound and capturing a lockfile from a real
   install. Resolved versions here: anthropic 0.125.0, chromadb 1.5.9, fastapi 0.141.1,
   mcp 1.30.0. Suggest committing `requirements.lock.txt` once Docker builds.
3. **`MODEL` defaults to `claude-sonnet-5`.** Five agents with composite fan-out makes
   per-call cost a design constraint; `.env.example` documents `claude-opus-5` for quality
   evaluation and asks that benchmarks record which model produced them.
4. **Added `tests/test_config.py`**, which is not in the issue's FR2 tree. The
   missing-key acceptance criterion was specified as a manual check and deserves a test.
5. **Added a fifth guard rail** (`os.environ` only in `core/config.py`), not in FR5.
   `core/config.py` is only a single source of truth if nothing bypasses it, and that is
   unenforceable once twenty modules exist.

## Follow-ups (not in this PR)

Two design notes surfaced while writing the stubs. Both belong in the routing issue, both
are cheap now and awkward later:

- **`routing_score` cold start inverts canary rollout.** A fresh instance computes
  `success_rate = 1.0` and `latency_score = 1.0`, scoring a perfect 1.0 -- higher than any
  warm instance. Since selection is `max()`, a newly registered canary takes *all* of that
  role's traffic. Needs a warm-up count, seeded stats, or weighted sampling.
- **Fan-out passes the same input to every agent.** That is correct for independent
  composites ("explain BFS then quiz me") but wrong when one agent needs another's output
  ("I failed the graphs quiz, what now" -- the planner never sees the grade). A
  `mode: parallel | sequential` field on `RoutingDecision` covers it without introducing
  an LLM planning step.
