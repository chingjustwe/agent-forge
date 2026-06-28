# Remote Agent Platform — Design Spec

> A multi-tenant, SSO-enabled remote agent platform with three-layer architecture (AGUI / API Gateway / Agent Runtime), framework-agnostic agent execution, and enterprise governance (harness, observability, RBAC).

## 1. Table of Contents

1. [Table of Contents](#1-table-of-contents)
2. [Motivation & Goals](#2-motivation--goals)
3. [Non-Goals](#3-non-goals)
4. [Target Users](#4-target-users)
5. [Architecture Overview](#5-architecture-overview)
6. [Directory Structure](#6-directory-structure)
7. [Tech Stack](#7-tech-stack)
8. [Core Interfaces](#8-core-interfaces)
9. [Multi-Tenant & RBAC Model](#9-multi-tenant--rbac-model)
10. [Agent Harness](#10-agent-harness)
11. [SSO & Authentication](#11-sso--authentication)
12. [Observability](#12-observability)
13. [Data Flow](#13-data-flow)
14. [Implementation Phases](#14-implementation-phases)

## 2. Motivation & Goals

Build an open-source remote agent platform that addresses the gap between simple AI chat UIs (like OpenUI) and enterprise agent infrastructure. The platform provides:

- A central place for enterprise teams to **manage, run, and observe** AI agents
- **Framework-agnostic** agent runtime — not locked into LangChain, ADK, or any specific SDK
- **Agent Harness** with governance: sandbox isolation, tool execution management, safety guardrails, fault recovery
- **Built-in observability** with optional OTel export
- Self-hostable, single-binary deployment

## 3. Non-Goals

- Not an agent framework itself — delegates to ADK, LangGraph, OpenAI Agent SDK, etc.
- Not a full-featured LLM Gateway (no model router, no key proxy) — that's a separate concern
- Not a competitor to OpenUI — OpenUI is the design inspiration for the AGUI layer, not the platform

## 4. Target Users

**Primary:** Enterprise internal teams — employees use agents configured by IT/Platform team

**Secondary:** SaaS providers offering agent capabilities to their own customers

Organizational hierarchy:
- Tenant = Organization (mapped to AD/LDAP/SSO domain)
- Workspace = Project or team isolation unit
- User = Individual with roles (owner / admin / member / viewer)

## 5. Architecture Overview

Three abstraction layers, strict downward dependency:

```
┌──────────────────────────────────────┐
│  ① AGUI Layer                       │
│  React SPA — Chat UI, Dashboard,    │
│  Config panels, Admin               │
└──────────────┬───────────────────────┘
               │ REST / SSE / WebSocket
┌──────────────▼───────────────────────┐
│  ② API Gateway Layer                │
│  FastAPI — SSO, RBAC, Routing,      │
│  Audit, Rate Limit                  │
└──────────────┬───────────────────────┘
               │ AgentRuntime ABC
┌──────────────▼───────────────────────┐
│  ③ Agent Runtime Layer              │
│  ┌─────────────────────────────┐     │
│  │  Harness (common layer)     │     │
│  │  Sandbox / Tool Engine /    │     │
│  │  MCP / Guardrails / Retry   │     │
│  └──────────┬──────────────────┘     │
│  ┌──────────▼──────────────────┐     │
│  │  Runnable Adapter           │     │
│  │  (ADK / LangGraph / ...)    │     │
│  └─────────────────────────────┘     │
└──────────────────────────────────────┘
```

**Key rule:** Gateway depends only on `AgentRuntime` ABC. It has zero knowledge of which adapter is loaded. Adapters are compiled in via build configuration.

## 6. Directory Structure

```
agent-platform/
├── pyproject.toml
├── src/
│   ├── main.py                    ← FastAPI app entry, lifespan, DI wiring
│   ├── gateway/                   ← API Gateway + AGUI
│   │   ├── __init__.py
│   │   ├── auth/                  ← SSO, RBAC, API Key
│   │   ├── routes/                ← AGUI and API route handlers
│   │   └── middleware/            ← Audit log, Rate Limit
│   ├── runtime/                   ← Agent Runtime
│   │   ├── __init__.py
│   │   ├── abc.py                 ← AgentRuntime abstract base
│   │   ├── models.py              ← Shared Pydantic models
│   │   ├── harness/               ← Agent governance common layer
│   │   │   ├── sandbox/           ← Workspace isolation
│   │   │   ├── tool/              ← Tool execution engine
│   │   │   ├── mcp/               ← MCP/Skill management
│   │   │   ├── guardrails/        ← Safety filters
│   │   │   └── retry/             ← Fault recovery & retry
│   │   ├── session/               ← Session state management
│   │   └── adapters/              ← Pluggable runtimes
│   │       ├── base.py            ← RunAdapter interface
│   │       ├── adk/               ← Google ADK
│   │       └── langgraph/         ← LangGraph (reserved)
│   └── infra/                     ← Infrastructure
│       ├── __init__.py
│       ├── db/                    ← SQLite / PostgreSQL
│       └── telemetry/             ← OTel observability
└── frontend/                      ← React SPA (Vite)
    ├── package.json
    ├── vite.config.ts
    └── src/
```

## 7. Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Web framework | **FastAPI** | Native async, SSE/WS support, Pydantic integration, OpenAPI |
| Data validation | **Pydantic v2** | FastAPI-native, fast, strict mode |
| Database | **SQLAlchemy async** (default SQLite via aiosqlite, optional asyncpg for PostgreSQL) | Mature async ORM, DB-agnostic |
| Auth | **Authlib** (OIDC/RBAC) + **python-jose** (JWT) | SSO + self-contained token auth |
| Agent SDKs | **google-adk**, **langgraph**, **openai-agents** | Plugable via adapter pattern |
| Async HTTP | **httpx** | For tool execution, external API calls |
| Observability | **OpenTelemetry Python SDK** | Standard, exportable to any backend |
| Frontend | **React 18 + Vite + TypeScript** | Component ecosystem, fast dev |
| Container | **Docker** (optional, for sandbox) | Workspace isolation |

## 8. Core Interfaces

### 8.1 Gateway → Runtime Boundary

Gateway sees only `AgentRuntime` ABC:

```python
class StreamEvent(TypedDict, total=False):
    type: Literal["text", "tool_call", "tool_result", "error", "status"]
    data: dict
    metadata: dict

class RuntimeConfig(TypedDict, total=False):
    agent: str
    model: str
    max_tokens: int
    temperature: float
    workspace_id: str
    extra: dict

class AgentRuntime(ABC):
    @abstractmethod
    async def run(
        self,
        session_id: str,
        messages: list[dict],
        config: RuntimeConfig,
    ) -> AsyncIterator[StreamEvent]:
        ...
```

### 8.2 Adapter → Harness Boundary

```python
class HarnessContext:
    """Injected by AgentRuntime into every Adapter.run() call."""
    tool_engine: ToolEngine
    sandbox: SandboxManager
    guardrails: GuardrailPipeline
    session: SessionStore

class RunAdapter(ABC):
    name: str

    @abstractmethod
    async def run(
        self,
        session: Session,
        messages: list[dict],
        context: HarnessContext,
    ) -> AsyncIterator[StreamEvent]:
        ...
```

### 8.3 Stream Events → AGUI Messages

Gateway converts internal `StreamEvent` to AGUI-compatible message format before pushing via SSE.

## 9. Multi-Tenant & RBAC Model

### 9.1 Hierarchy

```
Tenant (organization)          ← SSO domain, tenant admin
  └── Workspace (project/team) ← agent configs, members, quota
       └── User                ← roles
```

### 9.2 RBAC Matrix

| Action | Tenant Admin | Ws Owner | Ws Admin | Member | Viewer |
|--------|:-----------:|:--------:|:--------:|:------:|:------:|
| Manage tenant settings | ✓ | - | - | - | - |
| Create/delete workspace | ✓ | ✓ | - | - | - |
| Manage workspace members | ✓ | ✓ | ✓ | - | - |
| Configure agents & tools | - | ✓ | ✓ | - | - |
| Run agents | - | ✓ | ✓ | ✓ | - |
| View logs & usage | ✓ | ✓ | ✓ | ✓ | ✓ |
| View billing/quota | ✓ | ✓ | - | - | - |

### 9.3 API Key Model

Workspace-scoped API keys for programmatic access, inheriting the workspace's configured agent and RBAC restrictions.

## 10. Agent Harness

The harness is the common governance layer shared by all adapters. It wraps adapter execution with:

### 10.1 Pre-flight Guardrails
- Content filtering (input)
- PII detection
- Rate limit / quota check
- Policy enforcement (allowed tools, allowed models)

### 10.2 Tool Execution Engine
- Adapter requests tool execution through Harness, not directly
- MCP protocol support for skill/tool registration
- Tool timeout, cancellation, audit trail
- Credential injection via workspace secrets

### 10.3 Sandbox
- Default: subprocess isolation (namespace-level)
- Optional: Docker container per session/workspace
- Network egress control per workspace

### 10.4 Fault Recovery
- Automatic retry with exponential backoff
- Circuit breaker for external API calls
- Session persistence for crash recovery

### 10.5 Post-flight Guardrails
- Output content filtering
- PII masking
- Budget deduct / usage recording

## 11. SSO & Authentication

### 11.1 Supported Methods
- **OIDC** (Google Workspace, Azure AD, Okta, Keycloak)
- **SAML** (via separate IdP, Phase 4)
- **Built-in** (email+password for local dev / small teams)

### 11.2 Auth Flow
```
1. User → Browser → AGUI (redirect to IdP)
2. IdP → Callback → Gateway (verify token)
3. Gateway → Create/update user → Issue session JWT
4. Browser → Gateway (JWT in Authorization header)
```

### 11.3 Tenant Mapping
- SSO email domain → Tenant auto-discovery (or admin-managed)
- First user to claim a domain becomes tenant admin

## 12. Observability

### 12.1 Data Collected
- **Traces:** Request flow across Gateway → Runtime → Harness → Tool → LLM
- **Metrics:** Request count, latency (p50/p95/p99), token usage, error rate, active sessions
- **Logs:** Structured JSON logs per request, tool calls, guardrails triggers

### 12.2 Storage
- **Built-in (always on):** Write to SQLite/PostgreSQL tables for real-time dashboard
- **OTel Export (optional):** Export to OTel Collector / Jaeger / Grafana / Datadog

### 12.3 Implementation
- Instrumentation at each harness boundary point
- Export spans/metrics to internal store synchronously, OTel asynchronously
- Pre-aggregated metrics for dashboard performance

## 13. Data Flow

```
User → Browser
         │
         ▼
    AGUI SPA ────SSE──── Gateway (FastAPI)
                               │
                     ┌─────────┼─────────┐
                     │         │         │
                     ▼         ▼         ▼
                  Auth    Audit     Rate Limit
                               │
                               ▼
                     AgentRuntime.run()
                               │
                     ┌─────────┼─────────┐
                     │                   │
                     ▼                   ▼
                Guardrails          Session
               (pre-check)          Resume
                     │
                     ▼
               HarnessContext
               ┌───────────────┐
               │  Adapter.run  │
               │    (ADK/...)   │
               └───────┬───────┘
                       │
                ┌──────┴──────┐
                │             │
           Tool Engine    LLM Call
                │             │
           (sandboxed)     (via httpx)
                │             │
                └──────┬──────┘
                       │
                 Guardrails
                (post-check)
                       │
                       ▼
             StreamEvent → SSE → AGUI
```

## 14. Implementation Phases

### Phase 1 — Chat MVP (1-2 days)
- FastAPI skeleton, config, DI
- Simple agent: calls LLM provider (Anthropic/OpenAI) via httpx directly — no harness, no adapter
- Gateway routes + AGUI basic chat page (React SPA)
- **End-to-end: user types message → gets reply**

### Phase 2 — Multi-Tenant + SSO + RBAC (1-2 days)
- Tenant/Workspace/User model + registration
- OIDC SSO + JWT session
- RBAC enforcement in Gateway middleware
- **End-to-end: login → workspace → role-based UI**

### Phase 3 — Agent Harness (2-3 days)
- Tool/MCP execution engine
- Sandbox isolation (subprocess)
- Guardrails (input/output filtering)
- Fault recovery (retry, circuit breaker)
- **End-to-end: agent calls tools, guardrails filter content**

### Phase 4 — ADK Adapter (1 day)
- Implement RunAdapter for Google ADK
- Add as default alongside Phase 1 DirectLLMAdapter (fallback)
- Reuse Harness context
- **End-to-end: ADK agent runs with full harness governance**

### Phase 5 — Observability (2 days)
- Internal trace/metrics/log storage (SQLite tables: `request_logs`, `token_usage`, `latency_buckets`)
- Dashboard pages (request list, latency, token usage)
- Optional OTel Export (traces to OTel Collector, metrics to Prometheus via OTlp)
- Quota model (token/cost limits per workspace)
- **End-to-end: see agent execution traces in dashboard, quota enforced**

### Phase 6 — Admin UI & Audit Log (2 days)
- Admin page: tenant/user/workspace management
- Full audit log viewer (data collected since Phase 2)
- Usage & quota management UI
- **End-to-end: admin manages users, views audit log**

### Phase 7 — LangGraph Adapter (1 day)
- Implement RunAdapter for LangGraph
- ToolNode wrapper for harness delegation
- Reuse Harness context
- **End-to-end: LangGraph agent runs with full harness governance**

## 15. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Deployment model | Single binary (monolith) | Simplest for enterprise self-host, can split later |
| Adapter selection | Compile-time (build config) | Avoids Go plugin/wasm complexity |
| Harness ownership | Runtime owns it | Shared by all adapters, not framework-specific |
| Gateway ↔ Runtime | ABC interface | Decouples completely, add adapter without touching gateway |
| Tenant hierarchy | Org → Workspace → User | Matches enterprise org structure |
| SSE for streaming | Server-Sent Events | Simpler than WebSocket for unidirectional event push |
| API Key scoping | Workspace-level | Tenant-level is too coarse, user-level is too fine |
