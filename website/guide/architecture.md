# Architecture

Agent Forge uses a three-layer design. The API Gateway depends only on the `AgentRuntime` abstract interface, so adding a new adapter (ADK, LangGraph) requires zero gateway changes.

```
Frontend (React/Vite)  →  API Gateway (FastAPI)  →  Agent Runtime
                                │                       │
                    ┌───────────┼───────────┐           │
                    │           │           │           │
               SQLite/DB   OTel Export   LLM API   DirectLLMAdapter
                                                  (DeepSeek, SSE streaming)
```

## Layers

### Frontend (React SPA)

React 18 + Vite + TypeScript + recharts. A custom CSS design system (no third-party UI library) with dark/light theming. Shared components: `Modal`, `Toast`, `ConfirmDialog`, `Dropdown`, `Select`, `EmptyState`, `Skeleton`.

### API Gateway (FastAPI)

Handles auth (JWT, OIDC stub), RBAC permission checks, audit logging, and quota enforcement. Routes live under `src/gateway/routes/`.

### Agent Runtime

Pluggable adapters behind the `AgentRuntime` ABC (`src/runtime/abc.py`). The runtime emits `StreamEvent` objects (`src/runtime/models.py`) that the gateway streams to the client over SSE.

| Component | Path | Responsibility |
|-----------|------|----------------|
| `AgentRuntime` ABC | `src/runtime/abc.py` | Abstract execution interface |
| `RunAdapter` ABC | `src/runtime/adapters/` | Per-framework adapter contract |
| `DirectLLMAdapter` | `src/runtime/adapters/` | DeepSeek via OpenAI-compatible API, SSE |
| Harness | `src/runtime/harness/` | Guardrail pipeline, sandbox, tools, retry, circuit breaker |

## Observability

Telemetry lives under `src/infra/telemetry/`: a collector, spans, metrics, logs, an OTLP exporter, and a quota guardrail. Traces and metrics surface in the dashboard.

## Data Model

SQLAlchemy async engine with ~10 ORM models in `src/infra/db/models.py`, backed by SQLite (configurable).

> See the [master design spec](https://github.com/chingjustwe/agent-forge/blob/main/docs/superpowers/specs/2026-06-26-remote-agent-platform-design.md) for the full architecture document.
