# Remote Agent Platform — Phase 3 Spec: Agent Harness

> **Scope:** Build the common governance layer shared by all agent adapters. Replace DirectLLMAdapter's raw httpx call with a harness-wrapped execution pipeline. All agent traffic now passes through sandbox, tool engine, guardrails, and retry logic.

---

## 1. Harness Architecture

```
AgentRuntime.run()
  │
  ├── 1. Pre-flight Guardrails
  │     ├── Content filter (blocked keywords, regex patterns)
  │     ├── PII detection (email, phone, SSN → redact)
  │     └── Policy check (is this model/tool allowed for this workspace?)
  │
  ├── 2. Adapter.run()
  │     │
  │     ├── Tool call detected?
  │     │   └── ToolEngine.execute(name, args)
  │     │       ├── MCP resolver (find tool by name)
  │     │       ├── Sandbox.execute(command, timeout)
  │     │       └── Result → StreamEvent(tool_result)
  │     │
  │     └── LLM call (via adapter framework)
  │
  ├── 3. Post-flight Guardrails
  │     ├── Output content filter
  │     ├── PII masking (redact patterns in response)
  │     └── Budget deduct / usage recording
  │
  └── 4. Retry / Circuit Breaker
        └── Wraps adapter + tool calls
```

## 2. Runtime Orchestration

`AgentRuntime.run()` owns the full execution pipeline. DirectLLMAdapter stays unchanged — it simply calls the LLM and yields `StreamEvent`. All governance (guardrails, tool execution, retry) happens in the runtime layer, not in the adapter.

```python
# AgentRuntime.run() pseudocode
async def run(
    self,
    session_id: str,
    messages: list[dict],
    config: RuntimeConfig,
) -> AsyncIterator[StreamEvent]:
    adapter = self._resolve_adapter(config.agent)  # "direct_llm" | "adk" | ...
    async with HarnessContext(...) as ctx:

        # 1. Pre-flight guardrails
        pre_result = await ctx.guardrails.check(messages, direction="input")
        if pre_result.action == "block":
            yield StreamEvent(type="error", data={"message": pre_result.reason})
            return
        safe_messages = pre_result.modified_content or messages

        # 2. Adapter execution (with retry)
        attempt = 0
        while attempt <= ctx.retry_policy.max_retries:
            try:
                async for event in adapter.run(session, safe_messages, ctx):
                    if event.type == "tool_call":
                        # Tool execution passes through harness
                        result = await ctx.tool_engine.execute(
                            event.data["name"], event.data["args"]
                        )
                        yield StreamEvent(type="tool_result", data=result)
                    else:
                        yield event
                break  # success
            except RetryableError as e:
                attempt += 1
                if attempt > ctx.retry_policy.max_retries:
                    yield StreamEvent(type="error", data={"message": str(e)})
                    return
                await asyncio.sleep(ctx.retry_policy.backoff(attempt))

        # 3. Post-flight guardrails
        post_result = await ctx.guardrails.check(full_output, direction="output")
        if post_result.action == "redact":
            yield StreamEvent(type="text", data={"content": post_result.modified_content})
```

Key design rule: **Adapter does not know about harness.** The harness context is injected by the runtime — the adapter only receives a `HarnessContext` object and delegates tool execution to it when needed. The adapter's job is framework-specific agent loop; the harness's job is governance.

## 3. HarnessContext

Injected into every `RunAdapter.run()` call:

```python
class HarnessContext:
    tool_engine: ToolEngine
    sandbox: SandboxManager
    guardrails: GuardrailPipeline
    session: SessionStore
    workspace_id: str
    user_id: str
```

## 4. Components

### ToolEngine

```python
class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict                # JSON Schema
    handler: Callable | None = None  # Python function
    mcp_endpoint: str | None = None  # MCP server URL

class ToolEngine:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition): ...
    def resolve(self, name: str) -> ToolDefinition: ...

    async def execute(self, name: str, args: dict) -> dict:
        # 1. Resolve tool
        # 2. Check sandbox policy (is this tool allowed?)
        # 3. Execute via handler or MCP endpoint
        # 4. Apply timeout (default: 60s)
        # 5. Return result or raise ToolError
```

### MCP Resolver

```python
class MCPResolver:
    """Connects to MCP servers for tool discovery + execution."""

    async def connect(self, endpoint: str, auth_token: str | None = None): ...
    async def list_tools(self) -> list[ToolDefinition]: ...
    async def call_tool(self, name: str, args: dict) -> dict: ...
```

### SandboxManager

```python
class SandboxManager:
    """Isolates tool execution."""

    mode: Literal["subprocess", "docker"] = "subprocess"

    async def execute(
        self,
        command: str,
        args: list[str],
        timeout: int = 30,
        env: dict | None = None,
    ) -> SandboxResult:
        # subprocess mode: Popen with timeout, resource limits
        # docker mode: docker run --rm --network none ...
        ...

class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
```

### GuardrailPipeline

```python
class GuardrailResult:
    passed: bool
    action: Literal["allow", "block", "redact"]
    reason: str | None
    modified_content: str | None      # After redaction

class Guardrail(BaseModel):
    name: str
    type: Literal["input", "output"]
    patterns: list[str]               # Regex patterns
    action: Literal["block", "redact"]

class GuardrailPipeline:
    def __init__(self):
        self._guardrails: list[Guardrail] = []

    def add(self, guardrail: Guardrail): ...

    async def check(
        self,
        content: str,
        direction: Literal["input", "output"],
    ) -> GuardrailResult: ...
```

### RetryPolicy

```python
class RetryPolicy(BaseModel):
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    retryable_exceptions: list[type] = [TimeoutError, ConnectionError]

class CircuitBreaker:
    state: Literal["closed", "open", "half-open"]
    failure_count: int
    threshold: int = 5
    reset_timeout: float = 30.0
```

## 5. API Surface (new endpoints)

### Tool / Guardrail Management (workspace_admin+)

```
GET    /api/v1/workspaces/{id}/tools
       → 200 [{name, description, input_schema}]

POST   /api/v1/workspaces/{id}/tools
       → Body: {name, description, input_schema, handler_code | mcp_endpoint}
       → 201 {tool}

DELETE /api/v1/workspaces/{id}/tools/{name}
       → 204

GET    /api/v1/workspaces/{id}/guardrails
       → 200 [{name, type, patterns, action}]

POST   /api/v1/workspaces/{id}/guardrails
       → Body: {name, type, patterns, action}
       → 201 {guardrail}
```

### Agent Execution (now includes harness)

```
POST /api/v1/chat
  (unchanged from Phase 1, but Runtime now invokes harness automatically)
```

## 6. Data Flow (with Harness)

```
User → POST /api/v1/chat
  Gateway → validate JWT → get workspace config
    AgentRuntime.run(messages, config)
      HarnessContext(tool_engine, sandbox, guardrails, session)
        │
        ├── guardrails.check(input, "input")          ← Pre-flight
        │     if blocked → yield StreamError → return
        │
        ├── Adapter.run(session, messages, context)
        │     │
        │     ├── LLM generates tool_call
        │     │   → context.tool_engine.execute(name, args)
        │     │     → sandbox.exec(command)            ← Isolated
        │     │     → guardrails.check(result, "output") ← Post-flight per-tool
        │     │
        │     └── LLM generates text
        │       → yield StreamEvent(type="text")
        │
        └── guardrails.check(full_output, "output")    ← Post-flight full
              if redacted → yield modified text
```

## 7. Directory Additions

```
src/runtime/harness/
├── __init__.py
├── context.py               ← HarnessContext
├── tool_engine.py           ← ToolEngine, ToolDefinition
├── mcp.py                   ← MCPResolver
├── sandbox.py               ← SandboxManager
├── guardrails.py            ← Guardrail, GuardrailPipeline
├── retry.py                 ← RetryPolicy, CircuitBreaker
└── router.py                ← Wraps Adapter.run() with full pipeline
src/gateway/routes/
├── tools.py                 ← /api/v1/workspaces/*/tools endpoints
└── guardrails.py            ← /api/v1/workspaces/*/guardrails endpoints
```

## 8. Acceptance Criteria

```
[✓] pytest tests/ -v passes
[✓] ToolEngine registers tools and executes them
[✓] SandboxManager runs subprocess command with timeout
[✓] GuardrailPipeline blocks matching input patterns
[✓] GuardrailPipeline redacts PII from output
[✓] RetryPolicy retries on TimeoutError up to max_retries
[✓] CircuitBreaker opens after threshold failures
[✓] POST /api/v1/chat with a tool call → tool executes → result returned
[✓] POST /api/v1/chat with blocked content → StreamEvent(type="error") returned
[✓] Workspace admin can configure tools via API
```
