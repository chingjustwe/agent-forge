# Remote Agent Platform — Phase 7 Spec: LangGraph Adapter

> **Scope:** Implement RunAdapter for LangGraph. All harness features (tools, guardrails, sandbox, retry) continue to work unchanged, reused from Phase 3.

---

## 1. Architecture

LangGraph's `StateGraph` runs inside the Adapter, with tool execution delegated to Harness:

```
AgentRuntime.run()
  └── Harness (guardrails pre/post, retry, session)
        └── LangGraphAdapter.run()
              │
              ├── Build CompiledStateGraph from config
              ├── Run graph with .astream_events()
              │     │
              │     ├── Node execution → yield StreamEvent(type="text")
              │     │
              │     └── ToolNode intercepted:
              │           LangGraph calls ToolNode
              │           → replaced with HarnessToolNode
              │           → delegates to context.tool_engine.execute(name, args)
              │           → sandboxed, guardrailed
              │           → returns result to graph
              │
              └── Stream node outputs as StreamEvent
```

## 2. LangGraphAdapter

```python
class LangGraphAdapter(RunAdapter):
    name = "langgraph"

    def __init__(self):
        self._graph_registry: dict[str, Callable] = {}

    def register_graph(self, name: str, builder: Callable): ...
        # builder(config, context.tool_engine) → CompiledStateGraph

    async def run(
        self,
        session: dict,
        messages: list[dict],
        context: HarnessContext,
    ) -> AsyncIterator[StreamEvent]:
        # 1. Resolve graph name from session config
        # 2. Build compiled graph via registered builder
        # 3. Replace ToolNode with HarnessToolNode wrapper
        # 4. Run with .astream_events()
        # 5. Yield StreamEvent per node output
        ...
```

## 3. Tool Interception

LangGraph's native `ToolNode` is replaced at graph build time:

```python
class HarnessToolNode:
    """Drop-in replacement for LangGraph's ToolNode."""

    def __init__(self, tool_engine: ToolEngine):
        self._tool_engine = tool_engine

    async def __call__(self, state: dict) -> dict:
        # Intercept tool calls from LLM
        messages = state.get("messages", [])
        for msg in messages:
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    result = await self._tool_engine.execute(
                        tc["name"], tc["args"]
                    )
                    # Inject result back into state
                    state["messages"].append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": str(result),
                    })
        return state
```

## 4. Agent Configuration

```yaml
# Per-workspace agent config (stored in workspace settings)
agents:
  research-assistant:
    adapter: langgraph
    graph: research_graph
    model: claude-sonnet-4-20250514
    system_prompt: "You are a research assistant..."
    max_turns: 15
    tools:
      - search_web
      - fetch_url
      - summarize_text
```

## 5. API Changes

### New endpoint

```
GET /api/v1/workspaces/{id}/graphs
  → 200 [{name, adapter, model, tools, status}]

PUT /api/v1/workspaces/{id}/graphs/{name}
  → Body: graph config (system_prompt, tools, model, max_turns)
  → 200 {graph}
```

### Chat endpoint unchanged

```
POST /api/v1/chat
  → Body includes config.agent to select graph
  → 304 Not Modified if graph config unchanged (cached)
```

## 6. Key Differences from ADKAdapter

| Aspect | ADK (Phase 4) | LangGraph (Phase 7) |
|--------|---------------|---------------------|
| Agent model | Agent class with ToolContext | StateGraph with ToolNode |
| Tool interception | Override ToolContext.run_tool | Replace ToolNode with HarnessToolNode |
| Streaming | Native SSE support (adk.stream) | .astream_events() API |
| State | Session state managed by ADK | Custom state dict per graph |
| Graph registry | AgentRegistry (name → builder) | GraphRegistry (name → builder) |

## 7. Directory Additions

```
src/runtime/adapters/langgraph/
├── __init__.py
├── adapter.py              ← LangGraphAdapter
├── builder.py              ← Helper: config → CompiledStateGraph
└── tool_node.py            ← HarnessToolNode
src/gateway/routes/
├── graphs.py               ← /api/v1/workspaces/*/graphs endpoints
```

## 8. Acceptance Criteria

```
[✓] pytest tests/ -v passes
[✓] LangGraphAdapter runs a simple graph with text-only nodes
[✓] LangGraphAdapter runs a graph with tool calls → tool executes via Harness
[✓] Guardrails still fire (input block, output redact) during LangGraph execution
[✓] POST /api/v1/chat with config.agent="research-assistant" uses LangGraph
[✓] LangGraph tool calls appear in StreamEvent(tool_call, tool_result)
[✓] Graph builder can register and resolve graphs by name
[✓] HarnessToolNode correctly replaces native ToolNode
```
