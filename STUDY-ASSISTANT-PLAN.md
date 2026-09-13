# EchoMind → Study Assistant: 4-Day Refactor Plan

**Goal:** convert the customer-service multi-agent runtime into a **computer-science
study assistant** with (1) five specialised agents, (2) real Model Context Protocol
usage, and (3) end-to-end token accounting.

**Delivery language:** English — code, prompts, skills, docs, and agent output.
**Domain:** computer science coursework.
**Timebox:** 4 working days. Claude Code writes the code.

---

## 1. Decisions

### Confirmed by the user

| # | Decision |
|---|---|
| 1 | All delivery in **English** (prompts, skills, docs, responses) |
| 2 | Course domain is **computer science** |

### Assumed — say so if any is wrong

| # | Assumption | Cost to reverse |
|---|---|---|
| 3 | **English-only end to end** — students type English too | moderate: bilingual keyword lists + swap to `bge-m3` |
| 4 | Progress data lives in **Redis** (already deployed), not a new SQLite/Postgres | low |
| 5 | Customer-service agents are **replaced**, not kept alongside (git preserves them) | low |
| 6 | Spaced repetition = **SM-2**, implemented as a deterministic tool | low |
| 7 | **No sandboxed code execution** — hint-giving does not require it | n/a, it is a future add |

### Two consequences of going English

**`all-MiniLM-L6-v2` is now the correct embedding model.** Earlier analysis
recommended swapping to a Chinese-capable model because the corpus was Chinese and
Chroma's built-in model is English-trained. English delivery removes the mismatch.
**No embedding swap is needed** — this drops off the work list.

**The chunk-truncation problem largely dissolves.** `all-MiniLM-L6-v2` truncates at
~256 word-piece tokens; `_chunk_text` chunks at 500 *characters*. In Chinese that is
~500 tokens (half the chunk unembedded). In English 500 chars ≈ 125 tokens — well
inside the limit. **Verify on code-heavy chunks**, which tokenize denser.

---

## 2. Environment prerequisites (before Day 1 work)

| Check | Status | Action |
|---|---|---|
| Python | ✅ 3.12.0 on PATH | — |
| `.venv` | ❌ built on macOS (`/opt/homebrew/...`) | delete and rebuild |
| Git branch | ⚠️ on `main`, uncommitted changes | commit/stash, then branch |
| `mcp` package | ❌ not installed | add to `requirements.txt` |

```bash
git checkout -b feat/study-assistant
rm -rf .venv && python -m venv .venv
./.venv/Scripts/python -m pip install -r requirements.txt
```

---

## 3. Target design

### 3.1 Agent roster

Each agent has a genuinely distinct risk profile — that is what justifies
multi-agent over one prompt.

| Agent | max_tokens | Owns | Hard boundary |
|---|---:|---|---|
| **ConceptAgent** | 1200 | explanations, analogies, worked examples | cite materials; never invent APIs or signatures |
| **PracticeAgent** | 1000 | Socratic hints on problems | **never emit a complete solution** — academic integrity |
| **PlannerAgent** | 800 | study plans, spaced-repetition scheduling | scheduling is arithmetic, never LLM-guessed |
| **QuizAgent** | 1200 | quiz generation, free-text grading | grading must be reproducible |
| **TutorHandoffAgent** | — | escalate to a human TA | **no LLM call** — deterministic node |

**The temperature column is gone (ISSUE-002 FR7).** `temperature`, `top_p`, and
`top_k` are rejected with a 400 on Claude Sonnet 5 and Opus 5, so the original
per-agent values could never have run. The guarantees they were meant to provide
are unaffected, because they never came from sampling. Scheduling is exact because
`schedule_review` is SM-2 arithmetic — pure code, one correct answer per input.
Grading is *stabilised* rather than made deterministic: the rubric is pinned, the
weights are summed in code, and the one remaining judgement ("is rubric point N
present in this answer?") is decomposed into binary checks, which are far more
consistent than asking for a holistic score. A sampling parameter would not have
helped with either.

`AgentProfile` instead carries `effort` (`low`–`max`), deliberately set to the
same value for every role until the eval corpus can justify differentiating them
— it is a cost/quality dial, not a randomness dial, and transcribing the old
temperatures into effort levels would invent a mapping that does not exist.

### 3.1.1 QuizAgent's rubric is model-authored — and that is a deliberate limit

`grade_answer` compares a student's free-text answer against a rubric. That rubric is
**written by the model at question-generation time and pinned**, never regenerated at
grading time — regenerating it would let two gradings of the same answer disagree, which
destroys the one fairness property self-testing needs.

The honest limitation: a model-authored rubric can be **wrong** — a missing point, or one
that is not actually required. For self-study that is acceptable. The student gets one bad
correction, notices, and moves on; the stakes absorb it.

That tolerance is exactly why Owl Mind is **student-facing only**. Grading real assignments
would require an instructor-authored rubric, a human approval step, an audit trail, and a
second class of user with its own permissions — a different product, not a flag on this one.

### 3.2 Intent taxonomy

19 intents in 5 groups. Replaces the current customer-service set entirely.

| Group | Intents |
|---|---|
| `LEARN` | `concept_explain`, `concept_compare`, `material_search` |
| `PRACTICE` | `problem_help`, `homework_check`, `code_review`, `complexity_analysis` |
| `PLAN` | `study_plan`, `progress_query`, `deadline` |
| `ASSESS` | `quiz_request`, `answer_submission`, `mock_exam`, `explain_back` |
| `SUPPORT` | `motivation`, `human_tutor`, `greeting`, `feedback`, `other` |

Group → primary agent: `LEARN`→Concept, `PRACTICE`→Practice, `PLAN`→Planner,
`ASSESS`→Quiz, `human_tutor`/CRITICAL→TutorHandoff.

The three-way fusion (LLM 0.7 / embedding 0.2 / pattern 0.1) and the
specific-over-generic refinement are kept as-is — only the vocabulary changes.

**One change to the fusion's output, and it is load-bearing.** Each signal returns
its top candidates with scores, and the vote sums across all of them, producing a
distribution over intents rather than a winner. The reference implementation
collapsed each signal to a single label *before* voting, so its score map held at
most three entries; a composite request could then only be detected downstream, by
keyword hits on the lowest-weighted signal. The LLM — weighted 0.7 — read the second
half of the request and threw it away.

**Fan-out rule: at most 3 agents per request** (one primary, up to two supporting),
selected from the intent distribution by an absolute floor. The relative gate from
the reference implementation (`score >= 0.55 * primary`) is dropped: on a real
two-domain message it left the second agent qualifying by hundredths, so one fewer
keyword hit silently dropped half of what the student asked. With a hard cap the cap
does the limiting.

Three is the ceiling because three agents at up to three tool rounds each, plus the
composer, is already about ten model calls for one request — and it consumes three of
the gateway's eight concurrency slots, so two simultaneous fan-outs saturate the
process. Merging four independent answers also produces mush; the composer is the
binding constraint, not the budget. `TutorHandoffAgent` is exempt — escalation
short-circuits before scoring and is never a fan-out participant.

**Add a boot-time assertion** that every `IntentCategory` member appears in
`_TEMPLATES` and `_INTENT_GROUPS`, so taxonomy drift fails at startup rather than
silently at runtime.

### 3.3 Tool inventory

Split by the rule: **MCP is for tools with external dependencies or reuse value;
in-process is for pure functions over local `Request` state.**

**Agent-level (in-process, `agents/tools.py`)** — all deterministic `(req, args) -> dict`:

| Tool | Agent | Notes |
|---|---|---|
| `get_prerequisites` | Concept | static concept graph (red-black tree → BST → rotations) |
| `build_hint` | Practice | progressive levels 1–3; **level 3 is still not the answer** |
| `analyze_complexity` | Practice | pattern-based Big-O guidance; does not execute code |
| `schedule_review` | Planner | SM-2 arithmetic |
| `get_due_topics` | Planner | reads Redis progress state |
| `generate_quiz_spec` | Quiz | topic/count/difficulty structure, not the questions |
| `grade_answer` | Quiz | rubric-based comparison |
| `create_handoff_summary` | TutorHandoff | structured TA handoff |
| `inspect_request_context` | all | existing, retained |

**MCP server tools (`mcp_servers/materials_server.py`)**:

| Tool | Why MCP |
|---|---|
| `materials_search` | owns the Chroma client; benefits from process isolation |
| `materials_add` | ingestion; forces fixing the `handler.__self__` layering violation |
| `materials_stats` | corpus size |

**Explicitly NOT moved to MCP:** every tool that reads `Request` (`build_hint`,
`get_prerequisites`, `create_handoff_summary`, …). Moving them means serialising the
whole request context per call for zero benefit.

### 3.4 MCP topology

```
┌──────────────────────────────┐
│ EchoMind API  (MCP CLIENT)   │   initialize / tools/list / tools/call
│  ToolManager = client policy │  ──────────────────────────────────►
│  cache · breaker · timeout   │                    ┌────────────────────┐
│  fallback · schema validate  │  ◄──────────────── │ materials_server   │
└──────────────────────────────┘      results       │ (MCP SERVER)       │
         ▲                                          │  owns ChromaDB     │
         │ tool_use block                           └────────────────────┘
   ┌───────────┐
   │    LLM    │  ← never speaks MCP; the client translates
   └───────────┘
```

Transport: **stdio** for Day 3 (client spawns the subprocess). Migrate to
streamable HTTP on Day 4 if the compose-service version is wanted.

`ToolManager` survives intact and becomes **client-side policy** — this is the
correct MCP layering, and the reliability primitives become genuinely load-bearing
because there is now a real process boundary that can fail.

**Free win:** Chroma leaves the API process, so its calls stop consuming the
`asyncio.to_thread` executor pool.

### 3.5 Token accounting

New `core/llm_gateway.py` replaces 9 bare `messages.create` call sites.

```
LLMGateway.complete(component=..., **kwargs)
  ├─ asyncio.Semaphore              → backpressure (currently absent entirely)
  ├─ resp.usage capture             → input / output / cache_read / cache_creation
  │    getattr(..., 0) throughout   → compatible providers may omit fields
  ├─ Prometheus counters            → llm_tokens_total{component,model,direction}
  │                                   llm_calls_total{component,model}
  │                                   llm_latency_ms{component}
  └─ contextvars per-request rollup → surfaced on ChatResponse
```

`component` labels: `intent`, `agent:concept`, `agent:practice`, `agent:planner`,
`agent:quiz`, `composer`, `rewrite`, `rerank`, `profile`, `compress`, `merge`, `judge`.

New `ChatResponse` fields: `llm_calls`, `tokens_in`, `tokens_out`,
`tokens_by_component`.

`/metrics` already calls `generate_latest()` over the default registry, so new
counters are exposed with no monitor changes.

**Prerequisite:** fix the `_last_tool_traces` bug first (see §4 Day 1) — otherwise
per-tool timing never reaches `/trace/tools` and attribution is impossible.

---

## 4. Day-by-day

### Day 1 — Foundations, observability, MCP de-risk

**AM**
- [ ] Rebuild venv; branch `feat/study-assistant`
- [ ] Rename `mcp/` → `toolkit/`, `MCPToolManager` → `ToolManager`
      *(precondition: local `mcp/` shadows the real SDK on `sys.path`)*
- [ ] Fix `_last_tool_traces` — missing assignment on the success path of `_call_llm`
- [ ] Delete dead code: `_build_knowledge_context`, `_should_use_knowledge`,
      `_route`, `_collaboration_targets`, `_INTENT_ROUTING`

**PM**
- [ ] Build `core/llm_gateway.py`; convert all 9 call sites
- [ ] Add token fields to `ChatResponse`
- [ ] English translation pass on hardcoded strings (§5)
- [ ] **2-hour MCP spike**: hello-world server + `ClientSession` over stdio, one
      round trip. Throwaway code — the point is to surface SDK/`AsyncExitStack`
      surprises on Day 1, not Day 3.

**End state:** existing system runs in English; `/chat` returns token counts;
`/metrics` exposes them. **Token stats: DONE.**

---

### Day 2 — Domain model

**AM**
- [ ] Rewrite `IntentCategory`, `_TEMPLATES`, `_INTENT_GROUPS`, `_pattern_recognize`
      to the §3.2 taxonomy (English keywords and few-shot examples)
- [ ] Add the boot-time taxonomy-sync assertion
- [ ] Rewrite `_extract_entities` for CS: `topic`, `problem_id`, `language`,
      `complexity_class`, `due_date` — and **drop the loose `\b([45]\d{2})\b`
      error-code pattern**, which currently matches any 3-digit number

**PM**
- [ ] Five agent classes + `AgentProfile`s per §3.1
- [ ] Rewrite `_domain_scores` for the five agents *(not `_INTENT_ROUTING` — dead)*
- [ ] Rewrite `_needs_escalation` keywords for the tutor-handoff domain

**End state:** `POST /chat` routes CS study questions to the correct agent, with
`routing_reason` exposing the score vector. **Core deliverable: DONE.**

---

### Day 3 — Tools + real MCP

**AM**
- [ ] Implement the 8 agent-level tools (§3.3), each with a unit test
- [ ] Rewrite `skills/` as three English CS skills:
      `academic_integrity` (global), `socratic_method` (practice),
      `study_planning` (planner)

**PM**
- [ ] `mcp_servers/materials_server.py` via `FastMCP`, owning the Chroma client
- [ ] Client session in `lifespan` using `AsyncExitStack`; `initialize` +
      `tools/list`; register discovered tools with policy from a config map
- [ ] Rewrite `/knowledge/*` → `/materials/*` calling through the session
      *(the `handler.__self__` hack necessarily breaks — this is the fix)*
- [ ] Seed CS materials: complexity analysis, data structures, concurrency,
      HTTP/networking, SQL indexing

**End state:** a JSON-RPC message crosses a process boundary on every material
lookup. **MCP: REAL.**

---

### Day 4 — Measure, harden, document

**AM**
- [ ] Eval corpus: ~40 `IntentTestCase`s, ~15 dialog cases incl. multi-turn tutoring
- [ ] **Pin the eval baseline** — stop `_save_baseline` overwriting every run, so
      "regression" means "worse than the pinned release", not "worse than last run"
- [ ] Deterministic assertions:
      - `PracticeAgent` output never contains a complete solution
      - `set(profile.tool_scope) == set(agent.get_tools())` for every agent
      - every `IntentCategory` is covered by an eval case

**PM**
- [ ] `docker-compose` service for the MCP server (+ streamable HTTP if wanted)
- [ ] Rewrite `README.md`; update `wiki/`; update `interview-prep/` to the new
      architecture
- [ ] Full verification run (§8)

**End state:** shippable, measured, documented.

---

## 5. File change inventory

| File | Change |
|---|---|
| `mcp/` → `toolkit/` | rename; `MCPToolManager` → `ToolManager` |
| `core/llm_gateway.py` | **new** — token stats, semaphore, retry policy |
| `core/intent_recognizer.py` | taxonomy, templates, patterns, entities, prompt → English |
| `agents/agent_orchestrator.py` | 5 agent classes, `_domain_scores`, role-contract labels, composer prompt, error strings, dead-code deletion, `_last_tool_traces` fix |
| `agents/tools.py` | 8 new CS tools; delete billing/technical tools |
| `memory/conversation_memory.py` | 3 prompts + `to_prompt_text` labels → English; **fix the stale docstring** claiming Anthropic generates embeddings |
| `toolkit/tool_manager.py` | rewrite/rerank prompts → English; MCP-backed handler |
| `toolkit/knowledge_base.py` | → materials; CS seed docs; **fix `1.0 - dist`**, which assumes cosine while Chroma defaults to squared L2 |
| `evaluation/evaluator.py` | `JUDGE_PROMPT` → English; CS eval corpus; baseline pinning; exclude `judge_failed` results from averages |
| `api/main.py` | MCP session lifespan, `/materials/*`, token fields, English strings |
| `mcp_servers/materials_server.py` | **new** — MCP server |
| `skills/` | replace 3 CS-support skills with 3 study skills |
| `requirements.txt` | `+mcp`; **new** `requirements-dev.txt` (`pytest`, `pytest-asyncio`) |
| `docker-compose.yml` | MCP server service |

---

## 6. Cutline

Drop in this order. Everything above the line still ships as a coherent system.

1. `materials_add` / `materials_stats` over MCP — keep local ingestion
2. QuizAgent — four agents still satisfies the requirement
3. SM-2 → simple fixed-interval Leitner
4. Streamable HTTP transport — stay on stdio

**Never cut:** token stats, the `_last_tool_traces` fix, the eval corpus. Those are
what make everything else verifiable.

---

## 7. Out of scope

Authentication, response streaming, load testing, human-calibrated LLM-judge,
multi-worker state externalisation, a second MCP server for progress data,
sandboxed code execution, and anything requiring a second class of user
(TA/instructor tooling, role-based routing — see 3.1.1). Each is real work; none fits alongside the above in four
days.

**Sandboxed code execution is the obvious next MCP server** and the natural Day-5+
extension — it is the one CS-study capability that genuinely needs process
isolation, which is exactly what MCP is for.

---

## 8. Definition of done

```
docker compose up -d --build
GET  /health                    → ok, 5 agents listed
POST /chat  "explain quicksort partitioning"    → ConceptAgent,   tokens reported
POST /chat  "I'm stuck on two-sum"              → PracticeAgent,  hint not solution
POST /chat  "what should I review today"        → PlannerAgent
POST /chat  "quiz me on graph traversal"        → QuizAgent
POST /chat  "I want to talk to a TA"            → TutorHandoff,   no LLM call
GET  /trace/tools               → tool_calls NON-EMPTY (regression guard)
GET  /metrics                   → llm_tokens_total present, non-zero
POST /eval/run                  → intent accuracy + judge scores + pinned baseline
pytest                          → green
```

Plus: `ps` shows the MCP server as a **separate process**, and the materials tool
round-trips over JSON-RPC. That is the one-line test for whether MCP is real.

---

## 9. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| MCP SDK API differs from expectation | medium | Day-1 spike surfaces it 2 days early |
| `AsyncExitStack` + `lifespan` plumbing is fiddly | medium | spike covers exactly this |
| Translation pass leaks stale Chinese strings | medium | grep sweep for CJK range in Day 4 verification |
| Eval corpus takes longer than half a day | medium | cutline item 2 frees Day 3 AM |
| Chroma re-embed needed after seed-material change | low | corpus is small; re-embed is seconds |
| Scope creep into code execution | **high** | explicitly out of scope; see §7 |

---

## 10. Open questions

1. **English-only input?** (assumption 3) — if students may type Chinese, bilingual
   keyword lists are needed and the embedding simplification reverses.
2. **Course specifics** — is this tied to a particular syllabus/textbook, or generic
   CS? Affects seed materials and the prerequisite graph.
3. **Progress persistence** — Redis 24h TTL is wrong for spaced repetition, which
   needs weeks. Use a separate Redis DB with no TTL, or a small SQLite file?

Question 3 is the one that most affects PlannerAgent; the others have workable
defaults.

---

## 11. Single scenario — Assumption 5 reinstated

**Decision: the customer-service domain is removed.** EchoMind becomes a CS study
assistant, full stop — one taxonomy, one roster, one eval corpus. No `ScenarioPack`, no
env-var scenario switch, no second compose service. Git preserves the commerce code on
`main`.

"Do not remove EchoMind's highlights" is a constraint on **mechanisms, not domains**.
All eight highlights in `EchoMind 技术亮点.md` are domain-free machinery; what is being
deleted is the vocabulary they happen to operate on today. §11.1 is the checklist that
keeps each one alive, because two of them die quietly if nobody watches for it.

### 11.1 Highlight preservation checklist

| # | Highlight | Carries over | Changes in the study domain |
|---|---|---|---|
| 一 | 三路融合意图识别 | fusion weights, weighted vote, `OTHER` fallback, structured output (`intent_group` / `source_scores` / `urgency` / `entities`) | vocabulary only. Entities `order_id`/`amount`/`error_code` → `topic`/`problem_id`/`language`/`due_date`/`complexity_class` |
| 二 | 路由驱动的多 Agent 编排 | `AgentProfile`, pool-as-list, `primary` + `supporting` parallel execution, `routing_reason`, monitor feedback into scoring | 4 roles → 5. **At risk — see §11.2** |
| 三 | 共享工具 + 角色白名单 | `AgentToolSpec`, whitelist check, JSON-Schema validation, shared RAG tool | sharpened: `PracticeAgent`'s "never a complete solution" is a harder boundary to enforce and a far better demo than "never promise a refund" |
| 四 | 意图驱动 RAG | rewrite, parallel recall, dedup, fallback, cache, breaker | upgraded by the real MCP boundary (§11.3 十) and the diagram fix (§11.4 #2) |
| 五 | 三层记忆 | Redis working / `episodic` / `user_profile` split, compression, merge-update | **promoted from decorative to load-bearing.** `user_profile` now stores mastery and weak topics, which `PlannerAgent` reads directly. In the commerce domain the profile influenced little; here it drives scheduling |
| 六 | 动态 Skills 注入 | hot reload, injection by agent type + keyword | 3 commerce skills → `academic_integrity` (global), `socratic_method` (practice), `study_planning` (planner) |
| 七 | Monitor 在线观测与路由降权 | unchanged — entirely domain-free | needs the `_last_tool_traces` fix to measure anything at all (§11.4 #3) |
| 八 | 端到端评测闭环 | Accuracy, Macro-F1, per-class P/R/F1, LLM-as-Judge, regression detection | + pinned baseline, + deterministic contract assertions (§11.3 十一) |

### 11.2 The two highlights that can die silently

Neither shows up as a test failure. Both have to be designed for on Day 2.

**主辅协作并行 (highlight 二).** Customer service had natural composite requests —
"登录报错 \+ 重复扣款" splits cleanly across `TechnicalAgent` and `BillingAgent`. If every
study question routes to exactly one agent, the orchestrator degrades into a switch
statement over five prompts and the headline highlight is gone. Composite study requests
exist and must be in the taxonomy and the eval corpus:

| Utterance | Primary | Supporting |
|---|---|---|
| "explain BFS and then quiz me on it" | Concept | Quiz |
| "I'm stuck on two-sum — and what should I review this week?" | Practice | Planner |
| "why is my solution O(n²)? show me the concept I'm missing" | Practice | Concept |
| "I failed the graphs quiz, what now" | Quiz | Planner |

Require **≥5 multi-agent cases** among the ~15 dialog eval cases, and assert that
`supporting_agents` is non-empty for each.

**四级紧急度 (highlight 一).** Nothing in a study domain is naturally `CRITICAL`, so the
level collapses to decoration unless it is given real triggers. Define them explicitly:

- `CRITICAL` — explicit TA request, or a deadline inside 24h
- `HIGH` — deadline inside 72h, or the **third** failed hint on the same `problem_id`
  (read from working memory — this is also what feeds `TutorHandoffAgent`)
- `MEDIUM` — homework or quiz request with a future due date
- `LOW` — everything else

The third-failed-hint rule is the one worth building: it makes urgency a function of
conversation state rather than keyword matching, which is strictly more than the
commerce version did.

### 11.3 What the refactor contributes back to the core

Domain-agnostic; **new highlights appended to the existing eight**, not replacements.

| # | Highlight | File | Why it is a highlight |
|---|---|---|---|
| 九 | 全链路 Token 计量与背压 | `core/llm_gateway.py` | 9 scattered `messages.create` sites become one gateway: per-component token attribution, semaphore backpressure (**currently absent entirely**), Prometheus counters on the existing `/metrics`. Turns "多 Agent 是不是很贵" from a shrug into a number |
| 十 | 真正的 MCP 进程边界 | `mcp_servers/materials_server.py` + `toolkit/` | Today `mcp/` is a *local package name*, so the reliability primitives in 亮点四 (cache / breaker / timeout / fallback) guard an in-process call that cannot realistically fail. A real JSON-RPC boundary makes them load-bearing, and `ToolManager` becomes textbook client-side policy. This **upgrades 亮点四; it does not replace it** |
| 十一 | 契约即断言 | `evaluation/`, boot checks | Invariants currently live in prose: taxonomy↔template sync, `tool_scope == get_tools()`, and the role boundary itself (`PracticeAgent` never emits a complete solution). Make each one a startup assertion or a deterministic eval case, so a violation fails at boot or in CI instead of in a demo |

The zero-LLM escape hatch (`EscalationAgent` → `TutorHandoffAgent`) is **existing** design
reused, and worth naming as such when presenting: the handoff path stays functional
during a total model outage. That is a property of EchoMind, not of this refactor.

### 11.4 Doc corrections — the highlights must describe live code

Found while verifying this plan against the source. Each is a doc describing a path that
no longer executes. Fix on Day 4; keep every highlight.

1. **`EchoMind 场景扩展指南.md` Step 5 tells readers to register in `_INTENT_ROUTING`.**
   That table and its only reader `_route()` are dead — live routing is
   `_route_decision()` → `_domain_scores()`. Anyone following the guide today registers a
   route that never fires. Fix the guide *and* delete the dead table (§4 Day 1).
2. **`EchoMind 技术亮点.md` 总体架构 and 模块协作关系 both place `_build_knowledge_context()`
   in the `/chat` chain.** It is dead, along with `_should_use_knowledge()`. RAG now runs
   as `search_knowledge_base` inside the agent's tool loop, gated by the role whitelist.
   **This makes 亮点四 stronger, not weaker** — retrieval is no longer a fixed pre-step
   but an agent decision bounded by 亮点三's whitelist. Redraw the diagram to match;
   `knowledge_used` at `api/main.py:336` already derives from `tools_used`.
3. **`/trace/tools` is empty after every successful request.** `_last_tool_traces` is
   assigned only on the loop-exhaustion path (`agent_orchestrator.py:335`), never on the
   success return at `:279`. 亮点七's tool-level latency and success rates are therefore
   measuring nothing.
4. **`1.0 - dist` assumes cosine distance while Chroma defaults to squared L2**, so every
   RAG relevance score behind 亮点四 is on the wrong scale and can go negative.
5. **`_extract_entities` matches `\b([45]\d{2})\b` as an error code** — any 3-digit
   number, prices and quantities included, becomes an `error_code` entity. Dropped with
   the commerce taxonomy; noted because 亮点一 currently advertises it as routing input.

### 11.5 Rewriting the two Chinese docs

Both keep their structure. Only the worked examples change.

- **`EchoMind 技术亮点.md`** — same eight sections, same 面试时怎么讲 framing, commerce
  examples swapped for CS ones (退款误判 → "explain vs. quiz" routing; 订单号抽取 →
  `problem_id` / `topic`; 不能承诺到账时间 → 不能直接给出完整答案). Add 亮点九/十/十一 from
  §11.3. Update the 数据存储设计 table: `knowledge_base` → `materials`, and note that
  `user_profile` is now read by `PlannerAgent`, not only written.
- **`EchoMind 场景扩展指南.md`** — the study assistant becomes the **base scenario** and
  its five worked examples stay as extension targets. 场景四（教育培训辅助）is now the
  product, so replace that slot with **电商客服** as a scenario to extend *into*, reusing
  the commerce agents preserved in git history. The guide's value — "here is how you port
  this runtime to a new domain" — is unchanged, and the refactor itself becomes its
  evidence: the port was done once, for real.

### 11.6 Plan deltas

| § | Change |
|---|---|
| 1 #5 | reinstated as written — replace, do not keep alongside |
| 3.2 | add the composite intents implied by §11.2; the routing layer must be able to emit `supporting_agents` for them |
| 3.2 | add the urgency trigger table from §11.2 to `_pattern_recognize` / urgency escalation |
| 4 Day 2 | `_needs_escalation` gains the three-failed-hints rule, which reads working memory |
| 4 Day 4 | eval corpus: **≥5 of the ~15 dialog cases must be multi-agent**, asserting non-empty `supporting_agents` |
| 4 Day 4 | doc work is now a rewrite of both Chinese docs per §11.5, not a README touch-up |
| 6 | cutline unchanged. Note that cutting QuizAgent (item 2) removes two of the four composite cases in §11.2 — if it is cut, replace them with Practice+Concept and Practice+Planner pairs rather than dropping the multi-agent assertion |

### 11.7 Revised definition of done

§8 unchanged, plus:

```
POST /chat  "explain BFS then quiz me"   → primary + supporting, both ran
POST /chat  3rd failed hint, same problem → urgency HIGH, handoff offered
POST /eval/run                            → ≥5 multi-agent cases pass
grep -rn "_INTENT_ROUTING\|_build_knowledge_context" --include=*.py  → no hits
grep -rnP "[\x{4e00}-\x{9fff}]" --include=*.py . | grep -v tests      → no hits
```

The CJK sweep is the translation-leak guard from §9. The multi-agent assertion is the
one that protects the headline highlight: without it, a five-prompt switch statement
passes every other check in this document.
