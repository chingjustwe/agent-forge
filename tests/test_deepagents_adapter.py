"""Phase 4: Tests for DeepAgentsAdapter.

Covers:
- run() with empty messages raises ValueError
- _normalize_messages strips leading system message
- _translate_event maps v3 ``method=messages`` + ``content-block-delta`` → text event
- _translate_event maps v3 ``method=messages`` + ``tool-call`` → tool_call event (already_executed=True)
- _translate_event maps v3 ``method=messages`` + ``tool-result`` → tool_result event
- _translate_event maps v3 ``method=on_chain_start`` name="task" → subagent start event
- _translate_event maps v3 ``method=on_chain_end`` name="task" → subagent end event
- _translate_event maps v3 ``method=values`` w/ usage_metadata → status event
- _translate_event maps error events → error StreamEvent
- _translate_event returns [] for unrecognized events
- _EXCLUDED_FILESYSTEM_TOOLS lists the 7 deepagents filesystem tool names
  (kept for reference; deepagents 0.6.12 uses permissions, not excluded_tools)
- run() yields a final status event when no usage was emitted
- run() passes permissions (not excluded_tools) to create_deep_agent
- run() strips system messages, passes subagents, handles empty tools/checkpoint

All tests mock ``create_deep_agent`` and ``astream_events`` so no real
LLM calls are made. Mock events use the LangGraph v3 JSON-RPC format:
``{"type": "event", "method": "...", "params": {"data": ..., "name": ...}}``.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.runtime.adapters.deepagents import DeepAgentsAdapter, _EXCLUDED_FILESYSTEM_TOOLS
from src.runtime.models import StreamEvent


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_event(
    method: str,
    *,
    data: Any = None,
    name: str = "",
    event_type: str = "event",
) -> dict:
    """Build a LangGraph v3 JSON-RPC-style event.

    Format: ``{"type": event_type, "method": method, "params": {"data": data, "name": name}}``
    """
    return {
        "type": event_type,
        "method": method,
        "params": {"data": data, "name": name},
    }


def _make_text_delta(text: str) -> dict:
    """v3 ``method=messages`` event with a ``content-block-delta`` text delta."""
    return _make_event(
        "messages",
        data=[{
            "event": "content-block-delta",
            "delta": {"type": "text-delta", "text": text},
        }],
    )


def _make_tool_call(name: str, args: dict, call_id: str = "c-1") -> dict:
    """v3 ``method=messages`` event with a ``tool-call`` sub-event."""
    return _make_event(
        "messages",
        data=[{
            "event": "tool-call",
            "name": name,
            "args": args,
            "id": call_id,
        }],
    )


def _make_tool_result(name: str, output: str) -> dict:
    """v3 ``method=messages`` event with a ``tool-result`` sub-event."""
    return _make_event(
        "messages",
        data=[{
            "event": "tool-result",
            "name": name,
            "output": output,
        }],
    )


def _make_values_event(messages: list) -> dict:
    """v3 ``method=values`` event carrying a state snapshot.

    ``messages`` is a list of message-like objects (the last one's
    ``usage_metadata`` is checked for token usage).
    """
    return _make_event("values", data={"messages": messages})


def _make_chain_start(name: str, input_data: dict) -> dict:
    """v3 ``method=on_chain_start`` event."""
    return _make_event("on_chain_start", data=input_data, name=name)


def _make_chain_end(name: str, output: Any) -> dict:
    """v3 ``method=on_chain_end`` event."""
    return _make_event("on_chain_end", data={"output": output}, name=name)


def _make_error_event(message: str, method: str = "unknown") -> dict:
    """v3 error event (``type=error``)."""
    return {
        "type": "error",
        "method": method,
        "params": {"data": message},
    }


class _MockMessage:
    """Stand-in for LangChain BaseMessage with usage_metadata."""
    def __init__(self, content: str, usage_metadata: dict | None = None):
        self.content = content
        self.usage_metadata = usage_metadata


def _make_agent():
    from src.runtime.harness.agents import AgentDefinition
    return AgentDefinition(
        id="a-1",
        name="test-agent",
        workspace_id="ws-1",
        adapter="deepagents",
        system_prompt="You are a test agent.",
    )


def _make_ctx(agent=None):
    from src.runtime.harness.context import HarnessContext
    agent = agent or _make_agent()
    ctx = HarnessContext(
        workspace_id="ws-1",
        user_id="u-1",
        session_id="s-1",
        trace_id="t-1",
        agent=agent,
    )
    ctx.working_memory["system_prompt"] = "You are a test agent."
    return ctx


async def _collect(stream: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    out: list[StreamEvent] = []
    async for ev in stream:
        out.append(ev)
    return out


def _awaitable_stream(events: list[dict]):
    """Return an async function that yields an async iterator of events.

    The adapter does ``event_stream = await deep_agent.astream_events(...)``
    then ``async for event in event_stream``. So the mock must be an
    async function returning an async generator.
    """
    async def _gen():
        for e in events:
            yield e
    async def _runner(*args, **kwargs):
        return _gen()
    return _runner


# ── _normalize_messages ─────────────────────────────────────────────────


class TestNormalizeMessages:
    def test_strips_system_messages(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = adapter._normalize_messages(messages)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_passes_through_non_system_messages(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = adapter._normalize_messages(messages)
        assert len(result) == 2

    def test_empty_messages_returns_empty(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        assert adapter._normalize_messages([]) == []


# ── _translate_event ────────────────────────────────────────────────────


class TestTranslateEvent:
    def test_messages_content_block_delta_yields_text_event(self):
        """v3 method=messages + content-block-delta → StreamEvent(type='text')."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_text_delta("hello")
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "text"
        assert result[0].data["content"] == "hello"

    def test_messages_content_block_delta_with_empty_text_returns_empty(self):
        """Empty text-delta → no event (avoid emitting empty text chunks)."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_text_delta("")
        result = adapter._translate_event(event)
        assert result == []

    def test_messages_content_block_delta_with_non_text_delta_returns_empty(self):
        """A delta whose type is not 'text-delta' (e.g. tool-use-delta) is ignored."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event(
            "messages",
            data=[{
                "event": "content-block-delta",
                "delta": {"type": "tool-use-delta"},
            }],
        )
        result = adapter._translate_event(event)
        assert result == []

    def test_messages_tool_call_yields_tool_call_with_already_executed(self):
        """v3 method=messages + tool-call → StreamEvent(type='tool_call', already_executed=True)."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_tool_call(
            "shell_exec",
            {"command": "echo hi"},
            call_id="c-tool-1",
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "tool_call"
        assert result[0].data["name"] == "shell_exec"
        assert result[0].data["args"] == {"command": "echo hi"}
        assert result[0].data["call_id"] == "c-tool-1"
        assert result[0].already_executed is True

    def test_messages_tool_result_yields_tool_result_with_already_executed(self):
        """v3 method=messages + tool-result → StreamEvent(type='tool_result')."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_tool_result("shell_exec", "hi\n")
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "tool_result"
        assert result[0].data["name"] == "shell_exec"
        assert result[0].data["output"] == "hi\n"
        assert result[0].data["error"] is None
        assert result[0].already_executed is True

    def test_messages_tool_result_with_error_string(self):
        """If the shim returned an 'ERROR: ...' string, the tool_result
        should parse it into output='' and error=<message>."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_tool_result("boom_tool", "ERROR: something failed")
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "tool_result"
        assert result[0].data["output"] == ""
        assert result[0].data["error"] == "something failed"

    def test_messages_with_empty_data_returns_empty(self):
        """method=messages with empty data list → no events."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event("messages", data=[])
        result = adapter._translate_event(event)
        assert result == []

    def test_messages_with_non_dict_first_element_returns_empty(self):
        """method=messages where data[0] is not a dict → no events."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event("messages", data=["not-a-dict"])
        result = adapter._translate_event(event)
        assert result == []

    def test_messages_with_unrecognized_sub_event_returns_empty(self):
        """method=messages with an unknown sub-event type → no events."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event(
            "messages",
            data=[{"event": "some-future-event-type"}],
        )
        result = adapter._translate_event(event)
        assert result == []

    def test_on_chain_start_task_yields_subagent_start(self):
        """v3 method=on_chain_start name='task' → StreamEvent(type='subagent', action='start')."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_chain_start(
            "task",
            {"name": "web-searcher", "subagent_type": "research"},
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "subagent"
        assert result[0].data["action"] == "start"
        assert result[0].data["name"] == "web-searcher"
        assert result[0].data["subagent_type"] == "research"

    def test_on_chain_start_non_task_returns_empty(self):
        """method=on_chain_start with name != 'task' → no events."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_chain_start("agent", {"name": "ignored"})
        result = adapter._translate_event(event)
        assert result == []

    def test_on_chain_end_task_yields_subagent_end(self):
        """v3 method=on_chain_end name='task' → StreamEvent(type='subagent', action='end')."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_chain_end("task", "subagent result")
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "subagent"
        assert result[0].data["action"] == "end"
        assert result[0].data["output"] == "subagent result"

    def test_values_with_usage_yields_status(self):
        """v3 method=values with usage_metadata on last message → status event."""
        adapter = DeepAgentsAdapter(api_key="fake")
        msg = _MockMessage(
            "final",
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
            },
        )
        event = _make_values_event([msg])
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "status"
        assert result[0].data["usage"]["input_tokens"] == 100
        assert result[0].data["usage"]["output_tokens"] == 50
        assert result[0].data["usage"]["total_tokens"] == 150

    def test_values_without_usage_returns_empty(self):
        """method=values with no usage_metadata on last message → no events."""
        adapter = DeepAgentsAdapter(api_key="fake")
        msg = _MockMessage("final", usage_metadata=None)
        event = _make_values_event([msg])
        result = adapter._translate_event(event)
        assert result == []

    def test_values_with_empty_messages_returns_empty(self):
        """method=values with empty messages list → no events."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_values_event([])
        result = adapter._translate_event(event)
        assert result == []

    def test_values_with_non_dict_data_returns_empty(self):
        """method=values where data is not a dict → no events."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event("values", data="not-a-dict")
        result = adapter._translate_event(event)
        assert result == []

    def test_error_event_yields_error_stream_event(self):
        """type=error → StreamEvent(type='error')."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_error_event("something broke", method="on_chain_error")
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "error"
        assert result[0].data["code"] == "DEEPAGENTS_EVENT_ERROR"
        assert "something broke" in result[0].data["message"]
        assert result[0].data["event"] == "on_chain_error"

    def test_unrecognized_method_returns_empty(self):
        """An event with an unknown method → no events."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event("some-future-method", data={"foo": "bar"})
        result = adapter._translate_event(event)
        assert result == []


# ── _EXCLUDED_FILESYSTEM_TOOLS ──────────────────────────────────────────


class TestExcludedTools:
    def test_excluded_tools_contains_seven_filesystem_tools(self):
        assert _EXCLUDED_FILESYSTEM_TOOLS == {
            "ls", "read_file", "write_file", "edit_file",
            "glob", "grep", "execute",
        }

    def test_excluded_tools_is_frozen(self):
        # frozenset is immutable
        assert isinstance(_EXCLUDED_FILESYSTEM_TOOLS, frozenset)


# ── run() orchestration ─────────────────────────────────────────────────


class TestRunOrchestration:
    @pytest.mark.asyncio
    async def test_run_with_empty_messages_raises_value_error(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()
        with pytest.raises(ValueError, match="messages must not be empty"):
            async for _ in adapter.run([], ctx):
                pass

    @pytest.mark.asyncio
    async def test_run_yields_text_events_from_stream(self):
        """End-to-end: mock create_deep_agent + astream_events.

        Verifies that v3 method=messages content-block-delta events are
        translated to text StreamEvents, and method=values with usage
        is translated to a status StreamEvent.
        """
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()

        events_to_stream = [
            _make_text_delta("hello"),
            _make_text_delta(" world"),
            _make_values_event([_MockMessage(
                "final",
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                },
            )]),
        ]
        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _awaitable_stream(events_to_stream)

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent), \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            events = await _collect(adapter.run(
                [{"role": "user", "content": "hi"}], ctx
            ))

        # 2 text events + 1 status event
        assert len(events) == 3
        assert events[0].type == "text"
        assert events[0].data["content"] == "hello"
        assert events[1].type == "text"
        assert events[1].data["content"] == " world"
        assert events[2].type == "status"
        assert events[2].data["usage"]["total_tokens"] == 15

    @pytest.mark.asyncio
    async def test_run_yields_final_status_when_no_usage_emitted(self):
        """If the stream produces no usage event, run() should emit a
        final empty-usage status event (spec §2.1)."""
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()

        events_to_stream = [_make_text_delta("hi")]
        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _awaitable_stream(events_to_stream)

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent), \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            events = await _collect(adapter.run(
                [{"role": "user", "content": "x"}], ctx
            ))

        # 1 text event + 1 fallback status event
        assert len(events) == 2
        assert events[0].type == "text"
        assert events[1].type == "status"
        assert events[1].data["usage"]["total_tokens"] == 0

    @pytest.mark.asyncio
    async def test_run_yields_error_event_on_exception(self):
        """If astream_events raises, run() should yield an error event."""
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()

        async def _exploding_stream(*args, **kwargs):
            raise RuntimeError("deepagents blew up")

        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _exploding_stream

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent), \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            events = await _collect(adapter.run(
                [{"role": "user", "content": "x"}], ctx
            ))

        assert len(events) == 1
        assert events[0].type == "error"
        assert events[0].data["code"] == "DEEPAGENTS_ERROR"
        assert "deepagents blew up" in events[0].data["message"]

    @pytest.mark.asyncio
    async def test_run_passes_permissions_to_create_deep_agent(self):
        """When workspace_root is set, create_deep_agent receives
        filesystem permissions scoping access to the workspace."""
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()
        ctx.workspace_root = "/tmp/test-ws"

        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _awaitable_stream([])

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent) as mock_create, \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            await _collect(adapter.run(
                [{"role": "user", "content": "x"}], ctx
            ))

        call_kwargs = mock_create.call_args.kwargs
        perms = call_kwargs.get("permissions")
        assert perms is not None
        assert len(perms) == 2  # allow workspace_root/** + deny /**
        assert call_kwargs.get("excluded_tools") is None

    @pytest.mark.asyncio
    async def test_run_passes_subagents_none_when_empty(self):
        """When agent.subagents is empty, subagents=None should be passed
        to create_deep_agent (deepagents treats None as 'no subagents')."""
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()  # agent has no subagents by default

        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _awaitable_stream([])

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent) as mock_create, \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            await _collect(adapter.run(
                [{"role": "user", "content": "x"}], ctx
            ))

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs.get("subagents") is None

    @pytest.mark.asyncio
    async def test_run_with_no_tool_engine_passes_empty_tools(self):
        """When ctx.tool_engine is None, tools=[] should be passed."""
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()
        ctx.tool_engine = None

        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _awaitable_stream([])

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent) as mock_create, \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            await _collect(adapter.run(
                [{"role": "user", "content": "x"}], ctx
            ))

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs.get("tools") == []

    @pytest.mark.asyncio
    async def test_run_with_no_checkpoint_passes_none_checkpointer(self):
        """When ctx.checkpoint is None, checkpointer=None should be passed."""
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()
        ctx.checkpoint = None

        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _awaitable_stream([])

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent) as mock_create, \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            await _collect(adapter.run(
                [{"role": "user", "content": "x"}], ctx
            ))

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs.get("checkpointer") is None

    @pytest.mark.asyncio
    async def test_run_normalizes_messages_strips_system(self):
        """run() should strip leading system message before passing to
        deepagents (deepagents injects its own system prompt)."""
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()

        captured_input: dict = {}

        async def _capturing_stream(input_payload, *args, **kwargs):
            captured_input.update(input_payload)
            async def _gen():
                return
                yield  # type: ignore[unreachable]
            return _gen()

        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _capturing_stream

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent), \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            await _collect(adapter.run(
                [
                    {"role": "system", "content": "should be stripped"},
                    {"role": "user", "content": "hello"},
                ],
                ctx,
            ))

        messages = captured_input.get("messages", [])
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hello"


# ── Phase 4b: subagent wiring ───────────────────────────────────────────


class TestSubagentWiring:
    """Phase 4b: DeepAgentsAdapter wires SubagentMapper output into
    create_deep_agent(subagents=...)."""

    @pytest.mark.asyncio
    async def test_run_passes_subagent_specs_to_create_deep_agent(self):
        """When agent.subagents is non-empty, the mapped list should be
        passed to create_deep_agent as the subagents kwarg."""
        from src.runtime.harness.agents import AgentDefinition, SubagentSpec

        agent = AgentDefinition(
            id="a-sub",
            name="parent",
            workspace_id="ws-1",
            adapter="deepagents",
            system_prompt="x",
            subagents=[
                SubagentSpec(
                    name="child",
                    description="A child subagent.",
                    system_prompt="You are a child.",
                    tools=[],
                    model="deepseek-chat",
                ),
            ],
        )
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx(agent=agent)

        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _awaitable_stream([])

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent) as mock_create, \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            await _collect(adapter.run(
                [{"role": "user", "content": "x"}], ctx
            ))

        call_kwargs = mock_create.call_args.kwargs
        subagents = call_kwargs.get("subagents")
        assert subagents is not None
        assert len(subagents) == 1
        assert subagents[0]["name"] == "child"
        assert subagents[0]["model"] == "deepseek-chat"

    @pytest.mark.asyncio
    async def test_run_subagent_mapping_failure_falls_back_to_empty(self):
        """If SubagentMapper raises, the run must continue with no subagents
        (best-effort, per spec §2.1)."""
        from src.runtime.harness.agents import AgentDefinition, SubagentSpec

        agent = AgentDefinition(
            id="a-fail",
            name="parent",
            workspace_id="ws-1",
            adapter="deepagents",
            system_prompt="x",
            subagents=[
                SubagentSpec(
                    name="boom",
                    description="d",
                    system_prompt="s",
                ),
            ],
        )
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx(agent=agent)

        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _awaitable_stream([])

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent) as mock_create, \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()), \
             patch(
                 "src.runtime.harness.subagents.SubagentMapper.to_subagents",
                 side_effect=RuntimeError("boom"),
             ):
            await _collect(adapter.run(
                [{"role": "user", "content": "x"}], ctx
            ))

        # SubagentMapper raised → subagents=None passed (empty list → None).
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs.get("subagents") is None

    @pytest.mark.asyncio
    async def test_run_yields_subagent_event_on_task_chain_start(self):
        """method=on_chain_start name='task' → StreamEvent(type='subagent',
        action='start')."""
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()

        task_event = _make_chain_start(
            "task",
            {"name": "researcher", "subagent_type": "web"},
        )
        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _awaitable_stream([task_event])

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent), \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            events = await _collect(adapter.run(
                [{"role": "user", "content": "x"}], ctx
            ))

        subagent_events = [e for e in events if e.type == "subagent"]
        assert len(subagent_events) == 1
        assert subagent_events[0].data["action"] == "start"
        assert subagent_events[0].data["name"] == "researcher"
        assert subagent_events[0].data["subagent_type"] == "web"

    @pytest.mark.asyncio
    async def test_run_yields_subagent_event_on_task_chain_end(self):
        """method=on_chain_end name='task' → StreamEvent(type='subagent',
        action='end')."""
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()

        task_event = _make_chain_end("task", "subagent result")
        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _awaitable_stream([task_event])

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent), \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            events = await _collect(adapter.run(
                [{"role": "user", "content": "x"}], ctx
            ))

        subagent_events = [e for e in events if e.type == "subagent"]
        assert len(subagent_events) == 1
        assert subagent_events[0].data["action"] == "end"
        assert "subagent result" in subagent_events[0].data["output"]
