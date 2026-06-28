# Remote Agent Platform — Phase 1: Chat MVP

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** End-to-end chat flow: user opens browser, types a message, gets an LLM reply.

**Architecture:** FastAPI backend serves a React SPA. Gateway routes receive chat messages via SSE, pass them to a DirectLLM adapter (calls Anthropic/OpenAI via httpx), and stream responses back. No harness, no auth, no multi-tenant — just the core loop.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx, Pydantic v2, pytest + httpx mock, React 18 + Vite

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `src/gateway/__init__.py`
- Create: `src/gateway/routes/__init__.py`
- Create: `src/runtime/__init__.py`
- Create: `src/runtime/adapters/__init__.py`
- Create: `src/infra/__init__.py`
- Create: `src/infra/db/__init__.py`
- Create: `tests/__init__.py`
- Create: `conftest.py`
- Create: `.gitignore`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "agent-platform"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "httpx>=0.28.0",
    "pydantic>=2.10.0",
    "pydantic-settings>=2.7.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "pytest-httpx>=0.35",
    "httpx>=0.28.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create `src/__init__.py`**

```python
```

- [ ] **Step 3: Create directory `__init__.py` files**

```python
# src/gateway/__init__.py
# src/gateway/routes/__init__.py
# src/runtime/__init__.py
# src/runtime/adapters/__init__.py
# src/infra/__init__.py
# src/infra/db/__init__.py
# tests/__init__.py
```

All empty files.

- [ ] **Step 4: Create `.gitignore`**

```
__pycache__/
*.pyc
.env
*.db
frontend/node_modules/
frontend/dist/
.venv/
```

- [ ] **Step 5: Install dependencies**

```bash
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
```

Expected: all packages installed successfully.

- [ ] **Step 6: Verify test infra**

Create `tests/test_imports.py`:

```python
def test_imports():
    from fastapi import FastAPI
    from pydantic import BaseModel
    import httpx
    assert FastAPI
    assert BaseModel
    assert httpx
```

Run:

```bash
pytest tests/test_imports.py -v
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/ tests/ .gitignore
git commit -m "chore: project scaffold with FastAPI, pytest, httpx"
```

---

### Task 2: Core models and interfaces

**Files:**
- Create: `src/runtime/models.py`
- Create: `src/runtime/abc.py`
- Create: `src/runtime/adapters/base.py`
- Create: `tests/test_models.py`
- Create: `tests/test_abc.py`

- [ ] **Step 1: Write failing tests for models**

`tests/test_models.py`:

```python
import pytest
from pydantic import ValidationError
from src.runtime.models import RuntimeConfig, StreamEvent


class TestRuntimeConfig:
    def test_minimal_config(self):
        config = RuntimeConfig()
        assert config.agent == ""
        assert config.max_tokens == 4096
        assert config.temperature == 0.7

    def test_invalid_temperature(self):
        with pytest.raises(ValidationError):
            RuntimeConfig(temperature=3.0)


class TestStreamEvent:
    def test_text_event(self):
        event = StreamEvent(type="text", data={"content": "hello"})
        assert event.type == "text"
        assert event.data["content"] == "hello"

    def test_invalid_type(self):
        with pytest.raises(ValidationError):
            StreamEvent(type="unknown", data={})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_models.py -v
```

Expected: FAIL with ImportError (module not found)

- [ ] **Step 3: Implement models**

`src/runtime/models.py`:

```python
from typing import Literal
from pydantic import BaseModel, Field


class RuntimeConfig(BaseModel):
    agent: str = ""
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    workspace_id: str = ""
    extra: dict = {}


class StreamEvent(BaseModel):
    type: Literal["text", "tool_call", "tool_result", "error", "status"]
    data: dict
    metadata: dict = {}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_models.py -v
```

Expected: PASS

- [ ] **Step 5: Write failing tests for ABC**

`tests/test_abc.py`:

```python
import pytest
from src.runtime.abc import AgentRuntime
from src.runtime.models import RuntimeConfig, StreamEvent


class TestAgentRuntime:
    def test_abc_cannot_instantiate(self):
        with pytest.raises(TypeError):
            AgentRuntime()

    def test_concrete_runtime(self):
        class DummyRuntime(AgentRuntime):
            async def run(self, session_id, messages, config):
                yield StreamEvent(type="status", data={"state": "done"})

        runtime = DummyRuntime()
        import inspect
        assert inspect.isasyncgenfunction(runtime.run)
```

- [ ] **Step 6: Run tests to verify they fail**

```bash
pytest tests/test_abc.py -v
```

Expected: FAIL with ImportError

- [ ] **Step 7: Implement ABC**

`src/runtime/abc.py`:

```python
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from src.runtime.models import RuntimeConfig, StreamEvent


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

- [ ] **Step 8: Write failing tests for adapter base**

`tests/test_adapters.py`:

```python
import pytest
from src.runtime.adapters.base import RunAdapter


class TestRunAdapter:
    def test_abc_cannot_instantiate(self):
        with pytest.raises(TypeError):
            RunAdapter()

    def test_name_attribute(self):
        class DummyAdapter(RunAdapter):
            name = "dummy"

            async def run(self, session, messages, context):
                yield from ()

        adapter = DummyAdapter()
        assert adapter.name == "dummy"
        assert hasattr(adapter, "run")
```

- [ ] **Step 9: Implement adapter base**

`src/runtime/adapters/base.py`:

```python
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from src.runtime.models import StreamEvent


class RunAdapter(ABC):
    name: str = ""

    @abstractmethod
    async def run(
        self,
        session: dict,
        messages: list[dict],
        context: dict,
    ) -> AsyncIterator[StreamEvent]:
        ...
```

- [ ] **Step 10: Run all tests**

```bash
pytest tests/test_models.py tests/test_abc.py tests/test_adapters.py -v
```

Expected: all PASS

- [ ] **Step 11: Commit**

```bash
git add src/runtime/models.py src/runtime/abc.py src/runtime/adapters/base.py tests/
git commit -m "feat: core models, AgentRuntime ABC, RunAdapter ABC"
```

---

### Task 3: Direct LLM adapter

**Files:**
- Create: `src/runtime/adapters/direct_llm.py`
- Create: `tests/test_direct_llm.py`

- [ ] **Step 1: Write failing test**

`tests/test_direct_llm.py`:

```python
import pytest
from src.runtime.adapters.direct_llm import DirectLLMAdapter


@pytest.fixture
def adapter():
    return DirectLLMAdapter(
        api_key="test-key",
        base_url="https://api.anthropic.com/v1",
        model="claude-sonnet-4-20250514",
    )


class TestDirectLLMAdapter:
    def test_name(self, adapter):
        assert adapter.name == "direct_llm"

    def test_raises_on_empty_messages(self, adapter):
        import pytest

        async def run_empty():
            async for _ in adapter.run({}, [], {}):
                pass

        with pytest.raises(ValueError, match="messages"):
            import asyncio
            asyncio.run(run_empty())

    @pytest.mark.asyncio
    async def test_streams_text_events(self, adapter, httpx_mock):
        httpx_mock.add_response(
            url="https://api.anthropic.com/v1/messages",
            content=b'data: {"type":"content_block_delta","delta":{"text":"Hello"}}\n\ndata: [DONE]\n',
            headers={"Content-Type": "text/event-stream"},
        )

        results = []
        async for event in adapter.run(
            {},
            [{"role": "user", "content": "Hi"}],
            {},
        ):
            results.append(event)

        assert len(results) > 0
        assert any(e.type == "text" for e in results)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_direct_llm.py -v
```

Expected: FAIL with ImportError (module not found)

- [ ] **Step 3: Implement DirectLLMAdapter**

`src/runtime/adapters/direct_llm.py`:

```python
import json
from collections.abc import AsyncIterator

import httpx

from src.runtime.adapters.base import RunAdapter
from src.runtime.models import StreamEvent


class DirectLLMAdapter(RunAdapter):
    name = "direct_llm"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://api.anthropic.com/v1",
        model: str = "claude-sonnet-4-20250514",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def run(
        self,
        session: dict,
        messages: list[dict],
        context: dict,
    ) -> AsyncIterator[StreamEvent]:
        if not messages:
            raise ValueError("messages must not be empty")

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": context.get("max_tokens", 4096),
                    "stream": True,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data: "):
                        continue
                    payload = line.removeprefix("data: ").strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    event_type = data.get("type", "")
                    if event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        if "text" in delta:
                            yield StreamEvent(
                                type="text",
                                data={"content": delta["text"]},
                            )
                    elif event_type == "message_delta":
                        usage = data.get("usage", {})
                        if usage:
                            yield StreamEvent(
                                type="status",
                                data={"usage": usage},
                            )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_direct_llm.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/runtime/adapters/direct_llm.py tests/test_direct_llm.py
git commit -m "feat: DirectLLMAdapter for Anthropic streaming"
```

---

### Task 4: Gateway chat route

**Files:**
- Create: `src/gateway/routes/chat.py`
- Create: `tests/test_gateway.py`
- Modify: `src/gateway/routes/__init__.py`
- Modify: `src/main.py` (stub)

- [ ] **Step 1: Write failing test for chat SSE endpoint**

`tests/test_gateway.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


@pytest.mark.asyncio
async def test_health_endpoint(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_chat_streaming(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
            },
        ) as resp:
            assert resp.status_code == 200
            chunks = []
            async for chunk in resp.aiter_lines():
                if chunk.startswith("data: "):
                    chunks.append(chunk)
            assert len(chunks) > 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_gateway.py -v
```

Expected: FAIL with ImportError (create_app not found)

- [ ] **Step 3: Create stub main.py**

`src/main.py`:

```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Platform")
    return app
```

- [ ] **Step 4: Run test again**

```bash
pytest tests/test_gateway.py::test_health_endpoint -v
```

Expected: PASS
```bash
pytest tests/test_gateway.py::test_chat_streaming -v
```
Expected: FAIL with 405 (route not implemented)

- [ ] **Step 5: Implement chat route**

`src/gateway/routes/chat.py`:

```python
import json
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.runtime.models import RuntimeConfig, StreamEvent
from src.runtime.adapters.direct_llm import DirectLLMAdapter

router = APIRouter()


def _get_adapter(config: RuntimeConfig) -> DirectLLMAdapter:
    return DirectLLMAdapter(
        api_key=config.extra.get("api_key", ""),
        model=config.model,
    )


async def _event_stream(
    messages: list[dict],
    config: RuntimeConfig,
) -> AsyncIterator[str]:
    adapter = _get_adapter(config)
    async for event in adapter.run({}, messages, {}):
        yield f"data: {event.model_dump_json()}\n\n"


@router.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    config = RuntimeConfig(**body.get("config", {}))
    return StreamingResponse(
        _event_stream(messages, config),
        media_type="text/event-stream",
    )
```

- [ ] **Step 6: Wire route into main.py**

`src/main.py`:

```python
from fastapi import FastAPI
from src.gateway.routes.chat import router as chat_router


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Platform")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    app.include_router(chat_router)

    return app
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
pytest tests/test_gateway.py -v
```

Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add src/gateway/routes/chat.py src/main.py tests/test_gateway.py
git commit -m "feat: gateway chat SSE endpoint with DirectLLMAdapter"
```

---

### Task 5: React frontend

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/api.ts`

- [ ] **Step 1: Create package.json**

`frontend/package.json`:

```json
{
  "name": "agent-platform-frontend",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "typescript": "^5.6.3",
    "vite": "^5.4.11"
  }
}
```

- [ ] **Step 2: Create tsconfig.json**

`frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true
  },
  "include": ["src"]
}
```

- [ ] **Step 3: Create vite.config.ts**

`frontend/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
});
```

- [ ] **Step 4: Create index.html**

`frontend/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Agent Platform</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create main.tsx**

`frontend/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 6: Create api.ts**

`frontend/src/api.ts`:

```ts
export interface StreamEvent {
  type: "text" | "tool_call" | "tool_result" | "error" | "status";
  data: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export async function* streamChat(
  messages: { role: string; content: string }[],
  config?: Record<string, unknown>,
): AsyncGenerator<StreamEvent> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, config }),
  });

  if (!response.ok) {
    throw new Error(`Chat failed: ${response.status}`);
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      if (line.startsWith("data: ")) {
        const payload = line.slice(6).trim();
        if (payload === "[DONE]") return;
        yield JSON.parse(payload) as StreamEvent;
      }
    }
  }
}
```

- [ ] **Step 7: Create App.tsx**

`frontend/src/App.tsx`:

```tsx
import { useState, useRef, useCallback } from "react";
import { streamChat } from "./api";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(async () => {
    if (!input.trim() || streaming) return;
    const userMsg: ChatMessage = { role: "user", content: input };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setStreaming(true);

    let assistantContent = "";
    try {
      const history = [...messages, userMsg].map((m) => ({
        role: m.role,
        content: m.content,
      }));
      for await (const event of streamChat(history)) {
        if (event.type === "text") {
          assistantContent += event.data.content;
          setMessages((prev) => [
            ...prev.slice(0, -1),
            { role: "assistant", content: assistantContent },
          ]);
        }
      }
      if (assistantContent) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: assistantContent },
        ]);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setStreaming(false);
    }
  }, [input, messages, streaming]);

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", padding: 16 }}>
      <h1>Agent Platform</h1>
      <div
        style={{
          border: "1px solid #ccc",
          borderRadius: 8,
          padding: 16,
          marginBottom: 16,
          minHeight: 400,
          overflowY: "auto",
        }}
      >
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              marginBottom: 8,
              textAlign: m.role === "user" ? "right" : "left",
            }}
          >
            <strong>{m.role}:</strong> {m.content}
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && sendMessage()}
          placeholder="Type a message..."
          disabled={streaming}
          style={{ flex: 1, padding: 8 }}
        />
        <button onClick={sendMessage} disabled={streaming}>
          Send
        </button>
      </div>
    </div>
  );
}

export default App;
```

- [ ] **Step 8: Install and verify**

```bash
cd frontend && npm install && npm run build
```

Expected: `frontend/dist/` created with built files.

- [ ] **Step 9: Commit**

```bash
git add frontend/
git commit -m "feat: React chat UI with SSE streaming"
```

---

### Task 6: Wire everything and e2e test

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Update main.py with frontend static file serving**

`src/main.py`:

```python
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.gateway.routes.chat import router as chat_router


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Platform")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    app.include_router(chat_router)

    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app
```

- [ ] **Step 2: Run all tests**

```bash
pytest tests/ -v
```

Expected: All PASS

- [ ] **Step 3: Manual e2e test**

Create a `.env` file with an Anthropic API key:

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

Start the server:

```bash
uvicorn src.main:create_app --host 0.0.0.0 --port 8000 --factory
```

Test health:

```bash
curl http://localhost:8000/health
```

Expected: `{"status":"ok"}`

Test chat:

```bash
curl -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Say hello in one word"}],"config":{"extra":{"api_key":"sk-ant-..."}}}'
```

Expected: streaming SSE response with `data: {"type":"text","data":{"content":"Hello"}}`

Open frontend at http://localhost:8000 — should show chat UI.

- [ ] **Step 4: Commit**

```bash
git add src/main.py
git commit -m "feat: wire frontend static serving, e2e chat flow"
```

---

### Self-Review

**Spec coverage:**
- FastAPI skeleton ✓ (Task 1, 6)
- Simple agent via httpx ✓ (Task 3 — DirectLLMAdapter)
- Gateway routes ✓ (Task 4 — SSE chat endpoint)
- AGUI basic chat page ✓ (Task 5 — React SPA)
- End-to-end flow ✓ (Task 6 — manual test)

**Placeholder check:** All code blocks are complete. No TBD, TODO, or placeholder patterns.

**Type consistency:** `StreamEvent`, `RuntimeConfig` defined in Task 2, used consistently in Tasks 3-4-5. `RunAdapter` from Task 2, extended by `DirectLLMAdapter` in Task 3. `create_app` in Task 4, enhanced in Task 6.

**Test coverage:** Models (Task 2), ABC (Task 2), adapter base (Task 2), DirectLLMAdapter (Task 3), gateway endpoints (Task 4). Frontend is UI-only — E2E manual test in Task 6.

**Dependency ordering:**
- Task 1 (scaffold) ← no deps
- Task 2 (models/ABCs) ← Task 1
- Task 3 (direct_llm) ← Task 2
- Task 4 (gateway) ← Task 2, 3
- Task 5 (frontend) ← no backend deps (runs on proxy)
- Task 6 (wire) ← Task 4, 5
