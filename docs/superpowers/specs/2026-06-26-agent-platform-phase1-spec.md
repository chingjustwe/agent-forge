# Remote Agent Platform — Phase 1 Spec: Chat MVP

> **Scope:** Build the minimal end-to-end flow — user opens a browser, types a message, gets an LLM reply. No auth, no multi-tenant, no harness.

---

## 1. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Agent type | **Direct LLM call** (no framework) | Delay adapter complexity to Phase 4 |
| LLM provider | **Anthropic Messages API** | Simplest SSE streaming implementation |
| API key config | **From `RuntimeConfig.extra`** or env var | No secret management yet — Phase 2 adds SSO |
| Streaming protocol | **Server-Sent Events (SSE)** | One-directional text stream, simpler than WS |
| Frontend serving | **FastAPI mounts `frontend/dist/`** | No separate server, single port |
| State management | **None — stateless** | Each request is independent, no session resume |
| CORS | **Not needed** | Frontend served from same origin in production |

## 2. Data Model

### RuntimeConfig

```python
class RuntimeConfig:
    agent: str = ""                    # Reserved for later phases
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    temperature: float = 0.0..2.0      # default 0.7
    workspace_id: str = ""             # Phase 2+
    extra: dict = {}                   # api_key, custom params
```

### StreamEvent

```python
class StreamEvent:
    type: Literal["text", "tool_call", "tool_result", "error", "status"]
    data: dict                         # type-specific payload
    metadata: dict = {}                # Reserved for harness/observability later
```

Type-specific data shapes:

| type | data keys | Description |
|------|-----------|-------------|
| `text` | `content: str` | Text delta from LLM stream |
| `tool_call` | `name, args` | Agent requesting tool execution |
| `tool_result` | `name, result` | Tool execution output |
| `error` | `message` | Error in agent execution |
| `status` | `state` or `usage` | State transitions, token usage |

## 3. Interfaces

### AgentRuntime (Gateway's view)

```python
class AgentRuntime(ABC):
    async def run(
        session_id: str,
        messages: list[dict],
        config: RuntimeConfig,
    ) -> AsyncIterator[StreamEvent]
```

### RunAdapter (Adapter layer)

```python
class RunAdapter(ABC):
    name: str  # "direct_llm", "adk", "langgraph"

    async def run(
        session: dict,
        messages: list[dict],
        context: dict,
    ) -> AsyncIterator[StreamEvent]
```

### DirectLLMAdapter (Phase 1 implementation)

```python
class DirectLLMAdapter(RunAdapter):
    name = "direct_llm"

    def __init__(api_key, base_url, model)
    async def run(session, messages, context) -> AsyncIterator[StreamEvent]
        # Calls Anthropic Messages API with stream=True
        # Yields text StreamEvent per content_block_delta
        # Yields status StreamEvent per message_delta (usage)
```

## 4. API Surface

### `GET /api/v1/health`

```
→ 200 {"status": "ok"}
```

### `POST /api/v1/chat`

```
Request:
{
    "messages": [{"role": "user", "content": "Hello"}],
    "config": {
        "model": "claude-sonnet-4-20250514",
        "extra": {"api_key": "sk-ant-..."}
    }
}

Response: text/event-stream
data: {"type":"text","data":{"content":"Hello"},"metadata":{}}
data: {"type":"status","data":{"usage":{"input_tokens":10,"output_tokens":5}},"metadata":{}}
```

## 5. React Frontend

### Chat SPA

One-page app with:
- Message list (user left-aligned, assistant right-aligned, minimal styling)
- Text input + send button
- SSE consumption via fetch + ReadableStream
- Streaming state indicator
- No routing, no auth, no settings page

### Dev proxy

`vite.config.ts` proxies `/api/v1` to `http://127.0.0.1:8000`.

### Production

`npm run build` outputs to `frontend/dist/`. FastAPI mounts this directory with `StaticFiles(html=True)`.

## 6. Directory Layout

```
agent-platform/
├── pyproject.toml
├── src/
│   ├── __init__.py
│   ├── main.py                    ← create_app() factory
│   ├── gateway/
│   │   ├── __init__.py
│   │   └── routes/
│   │       ├── __init__.py
│   │       └── chat.py            ← SSE streaming endpoint
│   ├── runtime/
│   │   ├── __init__.py
│   │   ├── abc.py                 ← AgentRuntime ABC
│   │   ├── models.py              ← RuntimeConfig, StreamEvent
│   │   └── adapters/
│   │       ├── __init__.py
│   │       ├── base.py            ← RunAdapter ABC
│   │       └── direct_llm.py      ← Phase 1 adapter
│   └── infra/
│       ├── __init__.py
│       └── db/
│           ├── __init__.py
│           └── sqlite.py          ← Stub (reserved for Phase 3)
├── tests/
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_abc.py
│   ├── test_adapters.py
│   ├── test_direct_llm.py
│   └── test_gateway.py
└── frontend/
    ├── package.json
    ├── tsconfig.json
    ├── vite.config.ts
    ├── index.html
    └── src/
        ├── main.tsx
        ├── App.tsx
        └── api.ts
```

## 7. Error Response Format

All API errors follow a consistent structure across all phases:

```json
{
    "error": {
        "code": "BAD_REQUEST",
        "message": "Human-readable description"
    }
}
```

Standard codes:

| HTTP Status | `code` | When |
|-------------|--------|------|
| 400 | `BAD_REQUEST` | Invalid request body |
| 401 | `UNAUTHORIZED` | Missing or invalid auth |
| 403 | `FORBIDDEN` | Insufficient role |
| 404 | `NOT_FOUND` | Resource not found |
| 422 | `VALIDATION_ERROR` | Pydantic validation failure |
| 429 | `RATE_LIMITED` | Rate limit / quota exceeded |
| 500 | `INTERNAL_ERROR` | Unexpected server error |
| 503 | `SERVICE_UNAVAILABLE` | LLM provider unavailable |

## 8. Test Strategy

| Layer | Tool | What to test |
|-------|------|-------------|
| Models | pytest, Pydantic validation | Field defaults, type constraints, error cases |
| ABCs | pytest | Cannot instantiate, concrete subclass works |
| Adapters | pytest + pytest-httpx | Mock Anthropic SSE response, verify StreamEvent output |
| Gateway | pytest + httpx ASGITransport | Health check, chat SSE response format |
| Frontend | Manual E2E (curl + browser) | SSE streaming renders in chat UI |

## 9. Acceptance Criteria

```
[✓] pytest tests/ -v passes
[✓] curl http://localhost:8000/api/v1/health returns {"status": "ok"}
[✓] curl -N http://localhost:8000/api/v1/chat with valid API key returns SSE stream
[✓] Browser at localhost:8000 shows chat UI
[✓] User types message → sees streaming reply in UI
[✓] npm run build succeeds (frontend/dist/ created)
[✓] curl -X POST /api/v1/chat with invalid body returns 422 with standard error format
```

## 10. Dependencies

```toml
# Runtime
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
httpx>=0.28.0
pydantic>=2.10.0
pydantic-settings>=2.7.0

# Dev
pytest>=8.0
pytest-asyncio>=0.24
pytest-httpx>=0.35
```
