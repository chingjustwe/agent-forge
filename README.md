# Agent Platform

A multi-tenant AI agent platform with RBAC, observability, quota management, and admin dashboard.

## Architecture

```
Frontend (React/Vite)  →  API Gateway (FastAPI)  →  Agent Runtime
                                │                       │
                    ┌───────────┼───────────┐           │
                    │           │           │           │
               SQLite/DB   OTel Export   LLM API   DirectLLMAdapter
                                                  (DeepSeek, SSE streaming)
```

Three-layer design: **Frontend** (React SPA) → **API Gateway** (auth, RBAC, routing, audit) → **Agent Runtime** (pluggable adapters behind `AgentRuntime` ABC). The gateway only depends on the abstract interface — adding a new adapter (ADK, LangGraph) requires zero gateway changes.

See the [master design spec](docs/superpowers/specs/2026-06-26-remote-agent-platform-design.md) for the full architecture document.

## Project Status

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Chat MVP (DirectLLM + SSE + React chat) | **Done** |
| 2 | Multi-Tenant + RBAC + JWT | **Done** (OIDC is a stub) |
| 3 | Agent Harness (tools, sandbox, guardrails, retry) | Partially stubbed |
| 4 | Google ADK Adapter | Not started |
| 5 | Observability (traces, metrics, quota, dashboard) | **Done** |
| 6 | Admin UI & Audit Log | **Done** |
| 7 | LangGraph Adapter | Not started |
| — | Workspace Optimization | **Done** — User-workspace M2M, YAML RBAC, invite flow, SMTP, UI polish |

See the [roadmap](docs/ROADMAP.md) for upcoming features (password change, batch invites, OIDC, webhooks, etc.).

## Directory Structure

```
agent-platform/
├── src/
│   ├── main.py                  # App factory, lifespan, router wiring
│   ├── gateway/
│   │   ├── auth/                # JWT, OIDC stub, password hashing, RBAC roles
│   │   ├── routes/              # chat, auth, workspaces, admin, audit, observability, quota, settings
│   │   ├── middleware/          # AuthMiddleware, AuditMiddleware
│   │   └── email/               # Console + SMTP email sender
│   ├── runtime/
│   │   ├── abc.py               # AgentRuntime ABC
│   │   ├── models.py            # RuntimeConfig, StreamEvent
│   │   ├── adapters/            # RunAdapter ABC, DirectLLMAdapter (DeepSeek)
│   │   └── harness/             # GuardrailPipeline, HarnessContext
│   └── infra/
│       ├── db/                  # SQLAlchemy async engine, 10 ORM models
│       ├── telemetry/           # Collector, spans, metrics, logs, OTLP exporter, quota guardrail
│       └── settings.py          # Pydantic Settings (env-driven)
├── frontend/                    # React 18 + Vite + TypeScript + recharts
│   └── src/
│       ├── styles.css           # CSS design system (dark/light theme via CSS variables)
│       ├── api.ts               # Centralized API client + TypeScript types
│       ├── components/          # Shared UI components (Modal, Toast, ConfirmDialog, Dropdown, Select, etc.)
│       ├── context/             # React Context (WorkspaceContext)
│       └── pages/               # Sessions, Dashboard, Admin*, Agents, ApiKeys, etc.
├── tests/                       # pytest suite (37 files, ~9,700 lines, 404 tests)
├── permissions.yaml             # RBAC permission model — single source of truth
├── docs/superpowers/specs/    # Phase-by-phase design specs (7 phases)
└── .opencode/agents/            # 7 AI agents for spec-driven development
```

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 20+
- `uv` (recommended) or `pip`

### 1. Backend

```bash
# Install dependencies
uv sync --dev
# or: pip install -e ".[dev]"

# Start server
uvicorn src.main:create_app --reload --port 8000
# or: python -m src.main
```

API available at `http://localhost:8000/api/v1/`

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

UI available at `http://localhost:5175`

### 3. Configure LLM API Key

```bash
# Create .env file from example (or export the variable)
cp .env.example .env
# Edit .env and set your DeepSeek API key:
# LLM_API_KEY=sk-<your-key>
```

### 4. (Optional) Configure SMTP for Email

To send real invitation emails instead of printing to console:

```bash
# Edit .env and set SMTP credentials (e.g., Brevo free tier: 300 emails/day)
# SMTP_HOST=smtp-relay.brevo.com
# SMTP_PORT=587
# SMTP_USER=<your-brevo-account-email>
# SMTP_PASSWORD=<your-smtp-key>   # NOT the API key — use the SMTP key from Brevo dashboard
# SMTP_FROM=noreply@yourdomain.com
```

See `.env.example` for more providers (Gmail, Resend). Leave SMTP unset to use the console sender during development.

### 5. First User

Register via API (server must be running first):

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"yourpassword","name":"Admin"}'
```

The first user role is `member`. To enable admin access, update the DB:

```bash
sqlite3 data/agent_platform.db "UPDATE users SET role='tenant_admin' WHERE email='admin@example.com';"
```

> **Note:** The SQLite database is located at `data/agent_platform.db`. It is created automatically when the server starts for the first time.

Then log in at `http://localhost:5175`.

## Frontend

The frontend is a React 18 SPA with a custom CSS design system (no third-party UI library). It supports dark/light theme switching, and uses shared components for consistent interactions: Modal dialogs for forms, Toast notifications for feedback, ConfirmDialog for confirmations, and custom Select/Dropdown components.

## Frontend Pages

| Route | Page |
|-------|------|
| `/login` | Login / Register |
| `/invite` | Accept invite & set password |
| `/invitations/:token` | Accept workspace invitation |
| `/sessions` | Session list |
| `/sessions/:id` | Chat (SSE streaming) |
| `/dashboard` | Observability dashboard |
| `/requests` | Request log list |
| `/requests/:traceId` | Request detail (traces, tool calls) |
| `/agents` | Agent CRUD |
| `/api-keys` | API key management |
| `/quota` | Quota management |
| `/invitations` | Workspace invitation management |
| `/admin` | Admin overview |
| `/admin/users` | User CRUD + invite |
| `/admin/workspaces` | Workspace management |
| `/admin/audit` | Audit log viewer |
| `/admin/usage` | Usage statistics |
| `/admin/observability` | OTel export config |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/auth/register` | Register new user |
| `POST` | `/api/v1/auth/login` | Login (JWT) |
| `POST` | `/api/v1/auth/logout` | Logout |
| `GET` | `/api/v1/auth/invite/{token}` | Get invite details |
| `POST` | `/api/v1/auth/invite/{token}/accept` | Accept invite |
| `GET` | `/api/v1/health` | Health check |
| `POST` | `/api/v1/chat` | Chat (SSE streaming) |
| `GET` | `/api/v1/admin/*` | Admin API (users, tenants, workspaces, audit, usage) |
| `GET` | `/api/v1/workspaces/{id}/observability/*` | Traces, metrics, errors |
| `GET/PUT` | `/api/v1/workspaces/{id}/quota` | Quota management |
| `GET/PUT` | `/api/v1/workspaces/{id}/settings` | OTel export settings |

## Development

```bash
# Run all tests
python -m pytest tests/ -v

# Run with live reload (both terminals)
python -m src.main                  # backend
cd frontend && npm run dev          # frontend
```

## Documentation

All design specs are in [`docs/superpowers/specs/`](docs/superpowers/specs/):

| Spec | Description |
|------|-------------|
| [Master Design](docs/superpowers/specs/2026-06-26-remote-agent-platform-design.md) | Full platform architecture (15 sections) |
| [Phase 1](docs/superpowers/specs/2026-06-26-agent-platform-phase1-spec.md) | Chat MVP |
| [Phase 2](docs/superpowers/specs/2026-06-26-agent-platform-phase2-spec.md) | Multi-Tenant + SSO + RBAC |
| [Phase 3](docs/superpowers/specs/2026-06-26-agent-platform-phase3-spec.md) | Agent Harness |
| [Phase 4](docs/superpowers/specs/2026-06-26-agent-platform-phase4-spec.md) | ADK Adapter |
| [Phase 5](docs/superpowers/specs/2026-06-26-agent-platform-phase5-spec.md) | Observability |
| [Phase 6](docs/superpowers/specs/2026-06-26-agent-platform-phase6-spec.md) | Admin UI & Audit Log |
| [Phase 7](docs/superpowers/specs/2026-06-26-agent-platform-phase7-spec.md) | LangGraph Adapter |
