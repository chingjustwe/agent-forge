# Session Handoff — Remote Agent Platform

> Prepared: 2026-06-26
> Next action: Execute Phase 1 implementation in a new session via subagent-driven approach

---

## Project Overview

**Remote Agent Platform**: A multi-tenant, SSO-enabled, enterprise-grade remote agent platform with three-layer architecture (AGUI / API Gateway / Agent Runtime), framework-agnostic agent execution, and full governance (Agent Harness).

### Core Principles

- Three-layer architecture: AGUI → API Gateway → Agent Runtime (strict downward dependency)
- Framework-agnostic — Gateway knows `AgentRuntime` ABC only, adapters are pluggable at compile time
- Agent Harness is a shared governance layer across all runtimes, not framework-specific
- Forward path: every phase produces a working, testable end-to-end flow
- Python 3.11+, FastAPI, React SPA

---

## What's Done (this brainstorming session)

### Files created

```
docs/superpowers/
├── specs/
│   ├── 2026-06-26-remote-agent-platform-design.md       — Full design spec (15 sections)
│   ├── 2026-06-26-agent-platform-phase1-spec.md          — Phase 1: Chat MVP
│   ├── 2026-06-26-agent-platform-phase2-spec.md          — Phase 2: Multi-Tenant + SSO + RBAC
│   ├── 2026-06-26-agent-platform-phase3-spec.md          — Phase 3: Agent Harness
│   ├── 2026-06-26-agent-platform-phase4-spec.md          — Phase 4: ADK Adapter
│   ├── 2026-06-26-agent-platform-phase5-spec.md          — Phase 5: Observability
│   ├── 2026-06-26-agent-platform-phase6-spec.md          — Phase 6: Admin UI & Audit Log
│   └── 2026-06-26-agent-platform-phase7-spec.md          — Phase 7: LangGraph Adapter
├── plans/
│   └── 2026-06-26-agent-platform-phase1.md              — Phase 1 plan (6 tasks)
└── session-handoff-agent-platform.md                    — This file
```

### Key decisions made during session

| Decision | Detail |
|----------|--------|
| **Language** | Python 3.11+ |
| **Web framework** | FastAPI (async, SSE/WS, Pydantic integration) |
| **Deployment** | Single binary (monolith) — one uvicorn process |
| **Adapter selection** | Compile-time pick, not hot-plug |
| **Frontend** | React 18 + Vite + TypeScript |
| **Architecture** | AGUI + Gateway (merged in code) → AgentRuntime ABC → RunAdapter |
| **Harness ownership** | Runtime owns it, shared by all adapters |
| **Tenant model** | Organization → Workspace → User (three-level) |
| **RBAC roles** | Tenant Admin / Ws Owner / Ws Admin / Member / Viewer |
| **SSO** | OIDC first (Google/Azure/Okta), built-in email+password as fallback |
| **Observability** | Built-in storage (always on) + optional OTel Export |
| **Phase ordering** | Chat MVP → Multi-tenant+SSO → Harness → ADK Adapter → Observability → Admin+LangGraph |

### Architecture

```
src/
├── main.py                    ← FastAPI app entry
├── gateway/                   ← API Gateway + AGUI routes
│   ├── auth/                  ← SSO, RBAC, API Key
│   ├── routes/                ← AGUI and API route handlers
│   └── middleware/            ← Audit log, Rate Limit
├── runtime/                   ← Agent Runtime
│   ├── abc.py                 ← AgentRuntime abstract base
│   ├── models.py              ← Shared Pydantic models
│   ├── harness/               ← Agent governance layer
│   ├── session/               ← Session state management
│   └── adapters/              ← Pluggable runtimes
│       ├── base.py            ← RunAdapter interface
│       ├── adk/               ← Google ADK
│       └── langgraph/         ← LangGraph (reserved)
└── infra/                     ← DB + Telemetry
    ├── db/                    ← SQLite / PostgreSQL
    └── telemetry/             ← OTel observability
```

---

## Phase 1 Implementation (the one to execute next)

### Task list

1. **Task 1**: Project scaffold — pyproject.toml, directory structure, pytest infra, .gitignore
2. **Task 2**: Core models (RuntimeConfig, StreamEvent) + ABCs (AgentRuntime, RunAdapter)
3. **Task 3**: DirectLLMAdapter — calls Anthropic Messages API via httpx, streaming
4. **Task 4**: Gateway routes — /api/chat SSE endpoint, /health
5. **Task 5**: React frontend — basic chat SPA with SSE consumption
6. **Task 6**: Wire everything — main.py with static file serving, manual e2e test

Each task follows TDD: test → code → verify → commit.

### Key conventions from plan

- `src/main.py` has `create_app()` factory function (no global app)
- Gateway routes under `/api/chat` POST returning `text/event-stream`
- `DirectLLMAdapter` does NOT use harness — it calls Anthropic directly via httpx
- Frontend dev server proxies `/api` and `/health` to backend at `http://127.0.0.1:8000`
- Frontend builds to `frontend/dist/` and is served by FastAPI via `StaticFiles`
- `.env` stores `ANTHROPIC_API_KEY` for manual e2e testing
- NO conftest.py in Phase 1 — tests define their own fixtures

### Dependencies

```bash
pip install -e ".[dev]"
# Installs: fastapi, uvicorn, httpx, pydantic, pydantic-settings
# Dev: pytest, pytest-asyncio, pytest-httpx
```

---

## Later Phases (quick reference)

| Phase | Key additions | Approach |
|-------|--------------|----------|
| **2** | Multi-tenant + SSO + RBAC | Tenant/Workspace/User models + DB setup, OIDC Authlib, JWT sessions, role-based UI, Audit middleware |
| **3** | Agent Harness | Sandbox, Tool/MCP engine, Guardrails, Retry |
| **4** | ADK Adapter | Add google-adk as default, keep DirectLLMAdapter as fallback, reuse Harness |
| **5** | Observability | Internal trace/metrics/log store, Dashboard, OTel Export, Quota model |
| **6** | Admin UI & Audit Log | User/workspace admin pages, full audit log, quota management UI |
| **7** | LangGraph Adapter | Replace DirectLLMAdapter with LangGraph, reuse Harness |

---

## Useful commands

```bash
# Run server
uvicorn src.main:create_app --host 0.0.0.0 --port 8000 --factory --reload

# Run tests
pytest tests/ -v

# Run frontend dev server (from frontend/)
cd frontend && npm run dev

# Build frontend
cd frontend && npm run build
```
