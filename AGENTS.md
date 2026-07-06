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
│   └── src/
│       ├── styles.css       # CSS design system (dark/light theme, CSS variables)
│       ├── api.ts           # Centralized API client + TypeScript types
│       ├── components/      # Shared UI components (Modal, Toast, ConfirmDialog, Dropdown, Select, EmptyState, Skeleton, Layout, TraceTimeline)
│       ├── context/         # React Context (WorkspaceContext)
│       └── pages/           # 15 page components (Sessions, Dashboard, Admin*, Agents, etc.)
├── tests/                   # pytest suite (~9,700 lines, 37 test files, 404 tests)
├── permissions.yaml         # Permission model — single source of truth for RBAC
├── docs/
│   ├── superpowers/         # Design specs and implementation plans
│   └── workspace-optimization/  # Workspace refactor design docs (P0-P3)
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
| — | Workspace Optimization (P0-P3) | **Done** — Many-to-many user-workspace, YAML RBAC, invite flow, SMTP, UI polish |

Tech stack: Python 3.11+, FastAPI, SQLAlchemy async (SQLite), React 18, Vite, TypeScript, recharts. LLM provider: DeepSeek (OpenAI-compatible API).

### Frontend Design System

The frontend uses a custom CSS design system with no third-party UI library:
- **Theme**: Dark/light mode via CSS variables + `[data-theme]` attribute, persisted in localStorage
- **Shared components**: `Modal`, `Toast` (context-based notifications), `ConfirmDialog`, `Dropdown` (3-dot menus), `Select` (custom dropdown), `EmptyState`, `Skeleton` (shimmer loading)
- **Sidebar**: Collapsible with grouped nav sections, SVG icons, workspace switcher, theme toggle
- **Interaction patterns**: All CRUD forms use Modal dialogs (not inline), confirmations use ConfirmDialog (not `window.confirm`), feedback uses Toast (not inline alerts)

---

## Permission Model

### Configuration

The permission model is defined in [`permissions.yaml`](permissions.yaml) at the project root — this is the **single source of truth**. All route-level permission checks use `require_permission("resource:action")` which reads from this YAML file. The old `require_workspace_role` and `require_tenant_role` APIs are deprecated.

### Roles

| Role | Scope | Description |
|------|-------|-------------|
| `viewer` | Workspace | Read-only access |
| `member` | Workspace | Normal member, can chat |
| `workspace_admin` | Workspace | Full workspace management (members, settings, API keys, invitations, archive/delete) |
| `tenant_admin` | Tenant | Super admin — all permissions, all workspaces |

### Permission Matrix

```
                                  viewer  member  ws_admin  tenant_admin
──────────────────────────────────────────────────────────────────────────
sessions:read                       ✓       ✓        ✓           ✓
sessions:write                      ✗       ✓        ✓           ✓
sessions:delete                     ✗       ✗        ✓           ✓
agents:read                         ✓       ✓        ✓           ✓
agents:write                        ✗       ✗        ✓           ✓
quota:read                          ✓       ✓        ✓           ✓
quota:write                         ✗       ✗        ✓           ✓
invitations:read                    ✗       ✗        ✓           ✓
invitations:write                   ✗       ✗        ✓           ✓
api_keys:read                       ✗       ✗        ✓           ✓
api_keys:write                      ✗       ✗        ✓           ✓
settings:read                       ✗       ✗        ✓           ✓
settings:write                      ✗       ✗        ✓           ✓
members:read                        ✗       ✗        ✓           ✓
members:write                       ✗       ✗        ✓           ✓
workspace:delete                    ✗       ✗        ✓           ✓
workspace:archive                   ✗       ✗        ✓           ✓
admin:workspaces:read               ✗       ✗     ✓(scoped)      ✓
admin:users:read                    ✗       ✗     ✓(scoped)      ✓
admin:audit:read                    ✗       ✗     ✓(scoped)      ✓
admin:usage:read                    ✗       ✗     ✓(scoped)      ✓
admin:workspaces:write              ✗       ✗        ✗           ✓
admin:users:write                   ✗       ✗        ✗           ✓
admin:tenant:write                  ✗       ✗        ✗           ✓
──────────────────────────────────────────────────────────────────────────
Admin sidebar visible               ✗       ✗        ✓           ✓
```

**Scoped admin** means `workspace_admin` can see the Admin section but data is limited to workspaces they manage. `tenant_admin` sees all data across all workspaces.

### How to add a new permission

1. Add the permission name to `permissions.yaml` under the appropriate role(s)
2. Use `Depends(require_permission("new:perm"))` in the route handler
3. If the permission controls a frontend tab, add it to `frontend_tabs` in `permissions.yaml`

No code changes needed beyond those three steps.

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
