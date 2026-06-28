# Remote Agent Platform — Phase 5 Spec: Observability

> **Scope:** Add built-in observability — traces, metrics, and logs — stored internally for real-time dashboard, with optional OTel export for external analysis.

---

## 1. Architecture

```
Every request flows through instrumented boundaries:
  Gateway handler → AgentRuntime.run → Harness → Adapter.run → Tool engine → LLM call
        │                │               │           │            │          │
        └────────────────┴───────────────┴───────────┴────────────┴──────────┘
                                    │
                          TelemetryCollector
                          ├── Write to DB (always on)
                          └── OTel Exporter (optional, config enable)
```

## 2. Data Collected

### Traces

Every request gets a trace ID (propagated from Gateway to Harness). Spans recorded at:

| Span name | Parent | Attributes |
|-----------|--------|------------|
| `chat.handler` | — | method, path, status, user_id, ws_id |
| `runtime.run` | chat.handler | agent, model, session_id |
| `harness.pre_guardrails` | runtime.run | rule_count, blocked |
| `adapter.run` | runtime.run | adapter_name |
| `tool.execute` | adapter.run | tool_name, duration_ms, success |
| `harness.post_guardrails` | runtime.run | rule_count, redacted |
| `llm.call` | adapter.run | model, input_tokens, output_tokens |

### Metrics

| Metric | Type | Tags | Description |
|--------|------|------|-------------|
| `agent.requests.total` | Counter | tenant, ws, agent, model | Total requests |
| `agent.requests.duration` | Histogram | tenant, ws | Request latency (p50/p95/p99) |
| `agent.tokens.total` | Counter | tenant, ws, model | Input + output tokens |
| `agent.tools.called` | Counter | tenant, tool | Tool invocations |
| `agent.errors.total` | Counter | tenant, error_type | Error count by type |
| `agent.guardrails.blocked` | Counter | tenant, rule | Blocked requests |

### Logs

Structured JSON logs per request event:

```json
{
  "timestamp": "2026-06-26T10:00:00Z",
  "level": "info",
  "trace_id": "abc123",
  "event": "tool.execute",
  "tool": "search_knowledge_base",
  "duration_ms": 450,
  "success": true,
  "user_id": "user-uuid",
  "workspace_id": "ws-uuid"
}
```

## 3. Quota Model

### Workspace Quota

Workspace model (Phase 2) is extended with quota fields:

```python
# added to Workspace model
class Workspace:
    # ...existing fields...
    max_tokens_per_day: int = 1_000_000    # 0 = unlimited
    max_cost_per_month: float = 0.0        # 0 = unlimited, in USD
```

### Quota Usage Table

```sql
CREATE TABLE quota_usage (
    workspace_id TEXT NOT NULL,
    date TEXT NOT NULL,                    -- "2026-06-26"
    tokens_used INTEGER DEFAULT 0,
    cost REAL DEFAULT 0.0,
    PRIMARY KEY (workspace_id, date)
);
```

### QuotaGuardrail (harness integration)

A new guardrail registered in the Phase 3 GuardrailPipeline:

```python
class QuotaGuardrail:
    """Checks token/cost quota before allowing execution."""

    async def check(self, workspace_id: str) -> GuardrailResult:
        today = date.today().isoformat()
        usage = await quota_usage.get(workspace_id, today)
        ws = await workspace_store.get(workspace_id)

        if ws.max_tokens_per_day > 0 and usage.tokens_used >= ws.max_tokens_per_day:
            return GuardrailResult(
                passed=False, action="block",
                reason=f"Daily token quota exceeded ({usage.tokens_used}/{ws.max_tokens_per_day})"
            )
        return GuardrailResult(passed=True, action="allow")
```

QuotaGuardrail runs in the pre-flight phase of the harness pipeline (Phase 3 §2, step 1).

### Quota API

```
GET  /api/v1/workspaces/{id}/quota
     → 200 {max_tokens_per_day, max_cost_per_month, usage_today, tokens_used, cost_today}

PUT  /api/v1/workspaces/{id}/quota
     → Body: {max_tokens_per_day, max_cost_per_month}
     → 200 {quota}
     (workspace_admin+ only)
```

## 4. Storage Schema

### SQLite tables

```sql
CREATE TABLE request_logs (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    user_id TEXT,
    workspace_id TEXT,
    agent TEXT,
    model TEXT,
    status_code INTEGER,
    duration_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tool_calls (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args TEXT,                    -- JSON
    result TEXT,                  -- JSON (truncated)
    duration_ms INTEGER,
    success INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE events_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    level TEXT NOT NULL,
    event TEXT NOT NULL,
    data TEXT,                    -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## 5. API Surface (new endpoints)

### Dashboard data

```
GET /api/v1/workspaces/{id}/observability/summary?since=2026-06-01
  → 200 {total_requests, avg_latency_ms, total_tokens, error_rate, active_sessions}

GET /api/v1/workspaces/{id}/observability/requests?limit=50&offset=0
  → 200 [{id, model, duration_ms, status, error, created_at, ...}]

GET /api/v1/workspaces/{id}/observability/requests/{trace_id}
  → 200 {request detail, span list, tool calls}

GET /api/v1/workspaces/{id}/observability/tokens/daily?since=...&until=...
  → 200 [{date, input_tokens, output_tokens, cost_usd}]

GET /api/v1/workspaces/{id}/observability/latency?since=...&until=...
  → 200 {p50_ms, p95_ms, p99_ms, over_time: [{bucket, p50, p95}]}

GET /api/v1/workspaces/{id}/observability/errors?since=...
  → 200 [{error_type, count, last_seen}]
```

### OTel Export config

```
GET  /api/v1/workspaces/{id}/settings/otel
     → 200 {enabled, endpoint, headers}

PUT  /api/v1/workspaces/{id}/settings/otel
     → Body: {enabled: true, endpoint: "http://otel-collector:4318", headers: {}}
     → 200 {otel}
```

## 6. Frontend Additions

- **Dashboard page**: Summary cards (requests, tokens, latency, errors)
- **Request list**: Sortable table with search/filter by model, status, date
- **Request detail**: Trace waterfall view, tool call list, event log
- **Token usage chart**: Daily bar chart (input vs output) — built with **recharts** `BarChart`
- **Latency chart**: p50/p95/p99 over time — built with **recharts** `LineChart`
- **Error breakdown**: Error type pie chart — built with **recharts** `PieChart`
- **Quota page**: Current usage vs limit, quota configuration form
- **Settings**: OTel config form (enable/disable, endpoint, headers)

## 7. Directory Additions

```
src/infra/telemetry/
├── __init__.py
├── collector.py           ← TelemetryCollector (DB + OTel dispatch)
├── spans.py               ← Span definitions, context managers
├── metrics.py             ← Metric definitions, instrument setup
├── logs.py                ← Structured logger
└── otlp.py                ← OTel exporter (OTLP HTTP)
src/gateway/routes/
├── observability.py       ← /api/v1/workspaces/*/observability/* endpoints
├── quota.py               ← /api/v1/workspaces/*/quota endpoints
└── settings.py            ← OTel config endpoint
frontend/package.json      ← + recharts dependency
frontend/src/
├── pages/
│   ├── Dashboard.tsx
│   ├── RequestList.tsx
│   ├── QuotaPage.tsx
│   └── Settings.tsx
└── components/
    ├── LatencyChart.tsx
    ├── TokenChart.tsx
    └── TraceTimeline.tsx
```

## 8. Acceptance Criteria

```
[✓] pytest tests/ -v passes
[✓] Chat request creates request_log row
[✓] Tool call creates tool_calls row
[✓] Dashboard endpoint returns summary data
[✓] Request detail page shows trace spans
[✓] Token usage chart shows daily data
[✓] Latency chart shows p50/p95/p99
[✓] OTel export enabled → spans appear in Jaeger/Grafana
[✓] OTel export disabled → no external export, internal DB still works
[✓] Dashboard renders in browser
[✓] Error breakdown matches error logs
[✓] Quota page shows current usage vs limit
[✓] Request exceeding daily token quota returns 429 with RATE_LIMITED error
[✓] Workspace admin can update quota via API
```
