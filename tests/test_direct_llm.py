import asyncio
import json

import pytest
from src.runtime.adapters.direct_llm import DirectLLMAdapter
from src.runtime.harness.agents import AgentDefinition
from src.runtime.harness.context import HarnessContext
from src.runtime.harness.tool_engine import ToolDefinition, ToolEngine, ToolResult


def _make_ctx(
    *,
    max_tokens: int = 4096,
    model: str = "deepseek-chat",
    system_prompt: str = "",
    temperature: float = 0.7,
    tool_engine: ToolEngine | None = None,
) -> HarnessContext:
    """Build a minimal HarnessContext for DirectLLM adapter tests."""
    agent = AgentDefinition(
        id="",
        name="default",
        workspace_id="ws-test",
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system_prompt=system_prompt,
        adapter="direct_llm",
    )
    return HarnessContext(
        workspace_id="ws-test",
        user_id="u-test",
        session_id="s-test",
        trace_id="t-test",
        agent=agent,
        tool_engine=tool_engine,
    )


@pytest.fixture
def adapter():
    return DirectLLMAdapter(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
    )


class TestDirectLLMAdapter:
    def test_name(self, adapter):
        assert adapter.name == "direct_llm"

    def test_raises_on_empty_messages(self, adapter):
        async def run_empty():
            async for _ in adapter.run([], _make_ctx()):
                pass

        with pytest.raises(ValueError, match="messages"):
            asyncio.run(run_empty())

    @pytest.mark.asyncio
    async def test_streams_text_events(self, adapter, httpx_mock):
        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=b'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\ndata: [DONE]\n',
            headers={"Content-Type": "text/event-stream"},
        )

        results = []
        async for event in adapter.run(
            [{"role": "user", "content": "Hi"}],
            _make_ctx(),
        ):
            results.append(event)

        assert len(results) > 0
        assert any(e.type == "text" for e in results)
        assert any(e.type == "status" for e in results)

    @pytest.mark.asyncio
    async def test_prepends_system_prompt(self, adapter, httpx_mock):
        """When agent.system_prompt is set, adapter prepends it to messages."""
        import json as _json

        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=b'data: [DONE]\n',
            headers={"Content-Type": "text/event-stream"},
        )

        async for _ in adapter.run(
            [{"role": "user", "content": "Hi"}],
            _make_ctx(system_prompt="You are a helpful assistant."),
        ):
            pass

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        body = _json.loads(requests[0].content)
        msgs = body["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are a helpful assistant."
        assert msgs[1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_uses_agent_model_and_max_tokens(self, adapter, httpx_mock):
        """Adapter reads model + max_tokens from ctx.agent, not adapter default."""
        import json as _json

        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=b'data: [DONE]\n',
            headers={"Content-Type": "text/event-stream"},
        )

        async for _ in adapter.run(
            [{"role": "user", "content": "Hi"}],
            _make_ctx(model="deepseek-reasoner", max_tokens=2048),
        ):
            pass

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        body = _json.loads(requests[0].content)
        assert body["model"] == "deepseek-reasoner"
        assert body["max_tokens"] == 2048


# ── Tool calling ────────────────────────────────────────────────────────


def _sse(*chunks: dict) -> bytes:
    """Build an SSE response body from a sequence of JSON payloads."""
    parts = []
    for chunk in chunks:
        parts.append(f"data: {json.dumps(chunk)}\n\n")
    parts.append("data: [DONE]\n")
    return "".join(parts).encode()


def _make_tool_engine(
    tools: list[ToolDefinition],
    handler=None,
) -> ToolEngine:
    """Build a ToolEngine with the given tools and an optional handler."""
    from src.runtime.harness.tools import BUILTIN_HANDLERS

    engine = ToolEngine(
        registry=_ToolRegistry(tools),
        allowed_tools=[t.name for t in tools],
        builtin_handlers=BUILTIN_HANDLERS,
    )
    if handler is not None:
        engine._builtin_handlers = {tools[0].handler or tools[0].name: handler}
    return engine


class _ToolRegistry:
    """Minimal ToolRegistry stub for adapter tests."""

    def __init__(self, tools: list[ToolDefinition]) -> None:
        self._tools = {(t.workspace_id, t.name): t for t in tools}

    def get(self, name: str, workspace_id: str | None = None):
        return self._tools.get((workspace_id, name)) or self._tools.get((None, name))

    def list(self, workspace_id: str | None = None):
        return list(self._tools.values())


class TestDirectLLMToolCalling:
    """Tests for the OpenAI-compatible tool-calling loop in DirectLLMAdapter."""

    @pytest.mark.asyncio
    async def test_sends_tools_parameter_when_tools_available(self, adapter, httpx_mock):
        """When tools are available, the request body includes a ``tools`` field."""
        engine = _make_tool_engine([
            ToolDefinition(
                name="get_time",
                description="Get current time",
                input_schema={"type": "object", "properties": {}},
            )
        ])
        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=_sse({"choices": [{"delta": {"content": "It's noon."}}]}),
            headers={"Content-Type": "text/event-stream"},
        )

        async for _ in adapter.run(
            [{"role": "user", "content": "What time is it?"}],
            _make_ctx(tool_engine=engine),
        ):
            pass

        body = json.loads(httpx_mock.get_requests()[0].content)
        assert "tools" in body
        assert body["tools"][0]["function"]["name"] == "get_time"

    @pytest.mark.asyncio
    async def test_no_tools_parameter_when_engine_is_none(self, adapter, httpx_mock):
        """When no tool_engine is wired, ``tools`` must not be in the request."""
        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=_sse({"choices": [{"delta": {"content": "Hi"}}]}),
            headers={"Content-Type": "text/event-stream"},
        )

        async for _ in adapter.run(
            [{"role": "user", "content": "Hi"}],
            _make_ctx(),  # tool_engine=None
        ):
            pass

        body = json.loads(httpx_mock.get_requests()[0].content)
        assert "tools" not in body

    @pytest.mark.asyncio
    async def test_tool_call_loop_executes_and_continues(self, adapter, httpx_mock):
        """LLM calls a tool → adapter executes it → sends result back → LLM
        produces final text answer."""
        engine = _make_tool_engine([
            ToolDefinition(
                name="get_time",
                description="Get current time",
                input_schema={
                    "type": "object",
                    "properties": {"timezone": {"type": "string"}},
                },
            )
        ])

        # Mock tool execution.
        async def _handler(args, ctx):
            return ToolResult(
                name="get_time",
                output="2026-07-10T22:00:00+08:00",
            )

        engine._builtin_handlers = {"get_time": _handler}

        # First response: LLM calls the tool.
        # Second response: LLM gives final text answer.
        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=_sse(
                {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_abc",
                                "type": "function",
                                "function": {
                                    "name": "get_time",
                                    "arguments": '{"timezone": "Asia/Shanghai"}',
                                },
                            }]
                        },
                        "finish_reason": "tool_calls",
                    }]
                },
            ),
            headers={"Content-Type": "text/event-stream"},
        )
        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=_sse({"choices": [{"delta": {"content": "It's 10 PM."}}]}),
            headers={"Content-Type": "text/event-stream"},
        )

        events = []
        async for event in adapter.run(
            [{"role": "user", "content": "What time is it?"}],
            _make_ctx(tool_engine=engine),
        ):
            events.append(event)

        # Expect: tool_call, tool_result, text
        types = [e.type for e in events]
        assert "tool_call" in types
        assert "tool_result" in types
        assert "text" in types

        # tool_call should have already_executed=True
        tc = next(e for e in events if e.type == "tool_call")
        assert tc.already_executed is True
        assert tc.data["name"] == "get_time"
        assert tc.data["args"]["timezone"] == "Asia/Shanghai"

        # tool_result should carry the output
        tr = next(e for e in events if e.type == "tool_result")
        assert tr.already_executed is True
        assert "2026-07-10" in tr.data["output"]

        # Two HTTP requests: initial + follow-up after tool result
        assert len(httpx_mock.get_requests()) == 2

        # The second request must include the tool result message
        body2 = json.loads(httpx_mock.get_requests()[1].content)
        tool_msgs = [m for m in body2["messages"] if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call_abc"

    @pytest.mark.asyncio
    async def test_streaming_tool_call_fragments_are_accumulated(self, adapter, httpx_mock):
        """Tool call arguments arrive in multiple SSE chunks — they must be
        concatenated correctly before execution."""
        engine = _make_tool_engine([
            ToolDefinition(
                name="get_time",
                description="Get current time",
                input_schema={"type": "object", "properties": {}},
            )
        ])

        async def _handler(args, ctx):
            return ToolResult(name="get_time", output="noon")

        engine._builtin_handlers = {"get_time": _handler}

        # Arguments split across two chunks: '{"time' + 'zone":"UTC"}'
        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=_sse(
                {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "get_time", "arguments": ""},
                            }]
                        },
                    }]
                },
                {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "function": {"arguments": '{"time'},
                            }]
                        },
                    }]
                },
                {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "function": {"arguments": 'zone":"UTC"}'},
                            }]
                        },
                    }]
                },
            ),
            headers={"Content-Type": "text/event-stream"},
        )
        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=_sse({"choices": [{"delta": {"content": "Done"}}]}),
            headers={"Content-Type": "text/event-stream"},
        )

        events = []
        async for event in adapter.run(
            [{"role": "user", "content": "time?"}],
            _make_ctx(tool_engine=engine),
        ):
            events.append(event)

        tc = next(e for e in events if e.type == "tool_call")
        assert tc.data["args"] == {"timezone": "UTC"}

    @pytest.mark.asyncio
    async def test_tool_execution_error_yields_error_in_result(self, adapter, httpx_mock):
        """When tool execution raises, the tool_result event carries the error."""
        engine = _make_tool_engine([
            ToolDefinition(
                name="boom",
                description="Explodes",
                input_schema={"type": "object", "properties": {}},
            )
        ])

        async def _handler(args, ctx):
            raise RuntimeError("kaboom")

        engine._builtin_handlers = {"boom": _handler}

        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=_sse(
                {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "boom", "arguments": "{}"},
                            }]
                        },
                    }]
                },
            ),
            headers={"Content-Type": "text/event-stream"},
        )
        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=_sse({"choices": [{"delta": {"content": "Sorry."}}]}),
            headers={"Content-Type": "text/event-stream"},
        )

        events = []
        async for event in adapter.run(
            [{"role": "user", "content": "go"}],
            _make_ctx(tool_engine=engine),
        ):
            events.append(event)

        tr = next(e for e in events if e.type == "tool_result")
        assert tr.data["error"] is not None
        assert "kaboom" in tr.data["error"]
