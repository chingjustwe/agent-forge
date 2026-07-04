# Agent Platform — Development Guide

## About This Project

Agent Platform is an open-source, self-hostable, multi-tenant AI agent platform with RBAC, observability, quota management, and admin dashboard. It provides governance (sandboxing, guardrails, tool management) and observability on top of pluggable agent framework adapters (Google ADK, LangGraph, or direct LLM). The platform itself is **not** an agent framework — it orchestrates and manages agents built by any framework.

Design specs live in [`docs/superpowers/specs/`](docs/superpowers/specs/). The project follows a 7-phase spec-driven development approach.

## Project Structure

```
agent-platform/
├── src/                     # Python backend
│   ├── main.py              # create_app() factory, lifespan, DI wiring
│   ├── gateway/
│   │   ├── auth/            # JWT, OIDC, password, RBAC roles
│   │   ├── routes/          # API route handlers (chat, auth, admin, etc.)
│   │   ├── middleware/      # Auth + audit middleware
│   │   └── email/           # SMTP / console email sender
│   ├── runtime/
│   │   ├── abc.py           # AgentRuntime abstract base
│   │   ├── models.py        # RuntimeConfig, StreamEvent (Pydantic)
│   │   ├── adapters/        # RunAdapter ABC + DirectLLMAdapter
│   │   └── harness/         # GuardrailPipeline, HarnessContext
│   └── infra/
│       ├── db/              # SQLAlchemy async engine + ORM models
│       ├── telemetry/       # Collector, spans, metrics, logs, OTLP, quota
│       └── settings.py      # Pydantic Settings
├── frontend/                # React 18 SPA (Vite + TypeScript + recharts)
├── tests/                   # pytest suite (~1,850 lines, 20 test files)
├── docs/superpowers/        # Design specs and implementation plans
├── .opencode/agents/        # AI agent definitions for spec-driven development
└── pyproject.toml           # Python project config (uv)
```

## Current Status

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Chat MVP (DirectLLM + SSE + React chat) | **Done** |
| 2 | Multi-Tenant + RBAC + JWT (OIDC stub) | **Done** (OIDC not fully wired) |
| 3 | Agent Harness (tools, sandbox, guardrails, retry) | Partially stubbed |
| 4 | ADK Adapter | Not started |
| 5 | Observability (traces, metrics, quota, dashboard) | **Done** |
| 6 | Admin UI & Audit Log | **Done** |
| 7 | LangGraph Adapter | Not started |

Tech stack: Python 3.11+, FastAPI, SQLAlchemy async (SQLite), React 18, Vite, TypeScript, recharts. LLM provider: DeepSeek (OpenAI-compatible API).

---

## Agent Development Workflow

### Agent Architecture

```
.opencode/agents/
├── orchestrator.md       ← Phase coordinator — dispatches tasks, routes feedback
├── py-backend.md         ← Python backend (FastAPI, SQLAlchemy, pytest)
├── py-adapter.md         ← AI framework integration (ADK, LangGraph, Harness)
├── react-frontend.md     ← React frontend (Vite, TypeScript, recharts)
├── spec-reviewer.md      ← Spec compliance review (read-only)
├── code-reviewer.md      ← Code quality review (read-only)
└── troubleshooter.md     ← Full-stack diagnostics & fixes — investigates
                            symptoms, diagnoses root causes, fixes bugs
                            and reliability issues across all layers
```

### Available Skills

Reusable skills in `.agents/skills/` that agents can load during implementation:

| Skill | Purpose |
|-------|---------|
| `fastapi-python` | FastAPI best practices, Pydantic v2 patterns, async I/O |
| `frontend-design` | Design philosophy, anti-default guidance for distinctive UI |
| `tdd-orchestrator` | Red-green-refactor discipline, multi-agent TDD coordination |
| `ui-ux-pro-max` | 161 color palettes, 57 font pairings, 99 UX guidelines, chart types, accessibility |

## Execution Model

Each Phase is executed in an isolated git worktree, managed by the orchestrator agent.

### Phase Workflow

```
Orchestrator reads phase spec
  └── For each task (in dependency order):
        ├── Dispatch implementer agent
        │     ├── Implement code + unit tests
        │     ├── Run tests: ALL must pass
        │     ├── Run lint/typecheck
        │     └── Commit
        ├── Dispatch spec-reviewer
        │     ├── ✓ PASS → proceed to code review
        │     └── ✗ FAIL → route exact feedback to implementer → re-dispatch
        └── Dispatch code-reviewer
              ├── ✓ PASS → mark task completed
              └── ✗ FAIL → route exact feedback to implementer → re-dispatch

All tasks complete → final code-reviewer (holistic) → report to human
```

### Test Coverage Rules

**Mandatory for every code change:**

1. Every new function/method/route MUST have corresponding unit tests
2. Every bug fix MUST add a test that reproduces the bug before fixing
3. All tests MUST pass before marking a task done
4. Test types by layer:

   | Layer | Tool | Coverage |
   |-------|------|----------|
   | Pydantic models | pytest | Validation, defaults, edge cases |
   | Adapters | pytest + pytest-httpx | Mock external APIs, verify StreamEvent output |
   | Gateway routes | pytest + httpx ASGITransport | Status codes, response format, auth enforcement |
   | Frontend | vitest (if added) | Component rendering, SSE consumption |
   | Harness | pytest-asyncio | Guardrails, tool execution, retry, circuit breaker |

5. Mock external dependencies (LLM APIs, OIDC providers, OTel collectors) — never make real network calls in tests
6. Async tests use `pytest-asyncio` with `@pytest.mark.asyncio`
7. FastAPI integration tests use `httpx.AsyncClient` with `ASGITransport`

### Regression Prevention

- Run the FULL test suite after every change, not just the new tests
- Existing tests must continue to pass — if a change breaks them, either the change needs adjustment OR the old test needs updating (with documented reasoning)
- Code-reviewer checks for regression risk: mocking patterns, shared state, test isolation
- Parallel tasks (frontend + backend) run their tests independently to avoid false failures

### Review Cycle Rules

- No limit on review iterations — loop until both reviewers pass
- spec-reviewer and code-reviewer are read-only: they identify issues but do NOT write fixes
- The orchestrator routes exact reviewer feedback to the implementer — feedback is not summarized or filtered
- spec-reviewer must pass BEFORE code-reviewer runs (wrong order = restart the task)

## Agent Responsibilities

### Implementer (py-backend, py-adapter, react-frontend)

- Write code + tests
- Run tests before reporting DONE
- Self-review: check for edge cases, error handling
- Report: what was implemented, test results, any concerns
- If blocked, explain why

### Spec Reviewer

- Compare implementation against phase spec acceptance criteria
- Check: all required functionality present? Nothing extra?
- Report: PASS with evidence, or FAIL with specific gaps (file + line + exact spec quote)

### Code Quality Reviewer

- Review: error handling, type hints, test coverage, security, conventions
- Check: are existing tests still passing? Any regression risk?
- Report: PASS or FAIL with severity (CRITICAL / MAJOR / MINOR) per issue

## Getting Started

To start a new phase:

1. Read the target phase spec in `docs/superpowers/specs/` (e.g., `2026-06-26-agent-platform-phase3-spec.md`)
2. Load the `subagent-driven-development` skill in your AI agent tool
3. The orchestrator will create a git worktree, dispatch tasks in dependency order, and coordinate review cycles
4. After all tasks pass both reviewers, merge the worktree branch back

Key references:

- Master design: `docs/superpowers/specs/2026-06-26-remote-agent-platform-design.md`
- Phase 1 plan (with full code): `docs/superpowers/plans/2026-06-26-agent-platform-phase1.md`
- Session handoff notes: `docs/superpowers/session-handoff-agent-platform.md`
- Roadmap / TODO: `docs/ROADMAP.md`
