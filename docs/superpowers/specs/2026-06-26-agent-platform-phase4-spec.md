# Remote Agent Platform — Phase 4 Spec: ADK Adapter

> **Scope:** Implement the first real RunAdapter using Google ADK (Agent Development Kit). Add ADK alongside the existing `DirectLLMAdapter` as the new default. All harness features (tools, guardrails, sandbox) continue to work unchanged.

---

## 1. Architecture

ADK's agent loop runs inside the Adapter, but tool execution is delegated to Harness:

```
AgentRuntime.run()
  └── Harness (guardrails pre/post, retry, session)
        └── ADKAdapter.run()
              │
              ├── Create ADK Agent from config (system prompt, tools, model)
              ├── Run ADK agent loop with messages
              │     │
              │     ├── LLM call → ADK handles internally
              │     │
              │     └── Tool call intercepted:
              │           ADK wants to call tool "get_weather"
              │           → ADKAdapter intercepts via ADK's ToolContext
              │           → delegates to context.tool_engine.execute("get_weather", args)
              │           → sandboxed, guardrailed
              │           → returns result to ADK
              │
              └── Stream text/tool_call/tool_result events back
```

## 2. ADKAdapter

```python
class ADKAdapter(RunAdapter):
    name = "adk"

    def __init__(self):
        self._agent_registry: dict[str, Callable] = {}

    def register_agent(self, name: str, builder: Callable): ...
        # builder(config, tools) → ADK Agent/AgentLoop

    async def run(
        self,
        session: dict,
        messages: list[dict],
        context: HarnessContext,
    ) -> AsyncIterator[StreamEvent]:
        # 1. Resolve agent name from session config
        # 2. Build ADK agent via registered builder
        # 3. Wrap ADK's tool calls → context.tool_engine.execute
        # 4. Stream ADK's events as StreamEvent
        ...
```

## 3. Agent Configuration

```yaml
# Per-workspace agent config (stored in workspace settings)
agents:
  support-bot:
    adapter: adk
    model: gemini-2.0-flash
    system_prompt: "You are a support agent..."
    tools:
      - search_knowledge_base
      - get_ticket_status
      - escalate_to_human
    max_turns: 20

  code-reviewer:
    adapter: adk
    model: gemini-2.5-pro
    system_prompt: "Review code changes..."
    tools:
      - run_tests
      - check_lint
```

## 4. Adapter Selection

`DirectLLMAdapter` is **preserved as fallback**. ADK becomes the default when no explicit adapter is specified.

Selection logic:
- `RuntimeConfig.agent = "adk"` → `ADKAdapter`
- `RuntimeConfig.agent = "direct_llm"` → `DirectLLMAdapter`
- `RuntimeConfig.agent = ""` (omitted) → `ADKAdapter` (default)

This allows Phase 1's flow to still work for testing, while production traffic routes through ADK.

## 5. API Changes

### New endpoint

```
GET /api/v1/workspaces/{id}/agents
  → 200 [{name, adapter, model, tools, status}]

PUT /api/v1/workspaces/{id}/agents/{name}
  → Body: agent config (system_prompt, tools, model, max_turns)
  → 200 {agent}
```

### Chat endpoint unchanged

```
POST /api/v1/chat
  → Body now includes config.agent to select which agent to run
  → 304 Not Modified if agent config unchanged (cached)
```

## 6. Directory Additions

```
src/runtime/adapters/adk/
├── __init__.py
├── adapter.py              ← ADKAdapter
└── builder.py              ← Helper: config → ADK Agent
src/gateway/routes/
├── agents.py               ← /api/v1/workspaces/*/agents endpoints
```

## 7. Acceptance Criteria

```
[✓] pytest tests/ -v passes
[✓] ADKAdapter runs a simple agent with text-only response
[✓] ADKAdapter runs an agent that calls a tool → tool executes via Harness
[✓] Guardrails still fire (input block, output redact) during ADK execution
[✓] POST /api/v1/chat with config.agent="support-bot" uses ADK
[✓] POST /api/v1/chat without config.agent falls back to DirectLLMAdapter
[✓] Workspace admin can configure agent via API
[✓] ADK tool calls appear in StreamEvent(tool_call, tool_result)
```
