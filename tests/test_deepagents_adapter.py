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

from src.runtime.adapters.deepagents import (
    DeepAgentsAdapter,
    _COLLIDING_FILESYSTEM_TOOLS,
    _EXCLUDED_FILESYSTEM_TOOLS,
    _EXCLUDED_TODO_TOOL,
    _TASK_TOOL,
)
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


def _make_reasoning_delta(
    content: str,
    *,
    delta_type: str = "reasoning-delta",
    field: str = "reasoning_content",
) -> dict:
    """v3 ``method=messages`` event with a ``content-block-delta`` reasoning delta.

    ``field`` controls which key holds the content inside the delta dict,
    allowing tests to exercise all supported provider variants.
    """
    return _make_event(
        "messages",
        data=[{
            "event": "content-block-delta",
            "delta": {"type": delta_type, field: content},
        }],
    )


def _make_content_block_finish_tool_call(
    name: str, args: dict, call_id: str = "call_abc"
) -> dict:
    """v3 ``method=messages`` + ``content-block-finish`` with type=tool_call."""
    return _make_event(
        "messages",
        data=[{
            "event": "content-block-finish",
            "index": 1,
            "content": {"type": "tool_call", "id": call_id, "name": name, "args": args},
        }, {"run_id": "r-1"}],
    )


def _make_message_finish(usage: dict | None = None) -> dict:
    """v3 ``method=messages`` + ``message-finish`` with optional usage."""
    payload: dict = {"event": "message-finish"}
    if usage:
        payload["usage"] = usage
    return _make_event("messages", data=[payload, {"run_id": "r-1"}])


def _make_tool_finished(
    tool_call_id: str, output: str, tool_name: str = ""
) -> dict:
    """v3 ``method=tools`` + ``tool-finished`` event."""
    return {
        "type": "event",
        "method": "tools",
        "params": {
            "namespace": [],
            "timestamp": 0,
            "data": {
                "event": "tool-finished",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "output": output,
            },
        },
    }


def _make_tool_error(
    tool_call_id: str, message: str, tool_name: str = ""
) -> dict:
    """v3 ``method=tools`` + ``tool-error`` event."""
    return {
        "type": "event",
        "method": "tools",
        "params": {
            "namespace": [],
            "timestamp": 0,
            "data": {
                "event": "tool-error",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "message": message,
            },
        },
    }


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
        """A delta whose type is not 'text-delta' or reasoning → no event."""
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

    def test_reasoning_delta_yields_reasoning_event(self):
        """v3 content-block-delta with reasoning-delta → StreamEvent(type='reasoning')."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_reasoning_delta("Let me think about this...")
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "reasoning"
        assert result[0].data["content"] == "Let me think about this..."

    def test_thinking_delta_yields_reasoning_event(self):
        """v3 content-block-delta with thinking-delta type → reasoning event."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_reasoning_delta(
            "analyzing...", delta_type="thinking-delta", field="thinking"
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "reasoning"
        assert result[0].data["content"] == "analyzing..."

    def test_reasoning_delta_with_reasoning_field(self):
        """Provider using 'reasoning' key (not 'reasoning_content') → works."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_reasoning_delta(
            "step by step...", field="reasoning"
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "reasoning"
        assert result[0].data["content"] == "step by step..."

    def test_reasoning_delta_with_empty_content_returns_empty(self):
        """Empty reasoning content → no event emitted."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_reasoning_delta("")
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

    # ── Real LangGraph v3 format tests ──────────────────────────────────

    def test_content_block_finish_tool_call_yields_tool_call_event(self):
        """v3 content-block-finish with type=tool_call → StreamEvent(type='tool_call')."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_content_block_finish_tool_call(
            "read_file", {"path": "/tmp/foo.py"}, call_id="call_xyz"
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "tool_call"
        assert result[0].data["name"] == "read_file"
        assert result[0].data["args"] == {"path": "/tmp/foo.py"}
        assert result[0].data["call_id"] == "call_xyz"
        assert result[0].already_executed is True

    def test_content_block_start_returns_empty(self):
        """content-block-start is skipped (tool_call emitted on finish)."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event(
            "messages",
            data=[{
                "event": "content-block-start",
                "index": 1,
                "content": {"type": "tool_call", "id": "c-1", "name": "foo"},
            }, {"run_id": "r-1"}],
        )
        result = adapter._translate_event(event)
        assert result == []

    def test_message_finish_with_usage_yields_status(self):
        """v3 message-finish with usage data → status event."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_message_finish(
            usage={"input_tokens": 200, "output_tokens": 100, "total_tokens": 300}
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "status"
        assert result[0].data["usage"]["input_tokens"] == 200
        assert result[0].data["usage"]["output_tokens"] == 100

    def test_message_finish_without_usage_returns_empty(self):
        """message-finish without usage data → no event."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_message_finish()
        result = adapter._translate_event(event)
        assert result == []

    def test_tools_channel_tool_finished_yields_tool_result(self):
        """v3 method=tools + tool-finished → StreamEvent(type='tool_result')."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_tool_finished("call_xyz", "file contents", tool_name="read_file")
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "tool_result"
        assert result[0].data["output"] == "file contents"
        assert result[0].data["error"] is None
        assert result[0].data["call_id"] == "call_xyz"
        assert result[0].already_executed is True

    def test_tools_channel_tool_error_yields_tool_result_with_error(self):
        """v3 method=tools + tool-error → tool_result with error."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_tool_error("call_xyz", "Permission denied", tool_name="shell_exec")
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "tool_result"
        assert result[0].data["output"] == ""
        assert result[0].data["error"] == "Permission denied"
        assert result[0].data["call_id"] == "call_xyz"

    def test_tools_channel_tool_started_returns_empty(self):
        """tool-started on tools channel → skipped (tool_call from messages)."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = {
            "type": "event",
            "method": "tools",
            "params": {
                "namespace": [],
                "timestamp": 0,
                "data": {
                    "event": "tool-started",
                    "tool_call_id": "c-1",
                    "tool_name": "foo",
                    "input": {},
                },
            },
        }
        result = adapter._translate_event(event)
        assert result == []

    def test_reasoning_delta_with_v3_reasoning_field(self):
        """v3 reasoning-delta uses 'reasoning' field (not 'reasoning_content')."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_reasoning_delta(
            "let me think...", delta_type="reasoning-delta", field="reasoning"
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "reasoning"
        assert result[0].data["content"] == "let me think..."


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

    def test_colliding_subset_is_within_excluded_fs_set(self):
        """Colliding tools (ls/glob/grep) are a subset of the fs built-ins."""
        assert _COLLIDING_FILESYSTEM_TOOLS <= _EXCLUDED_FILESYSTEM_TOOLS
        assert _COLLIDING_FILESYSTEM_TOOLS == {"ls", "glob", "grep"}

    def test_todo_and_task_constants(self):
        assert _EXCLUDED_TODO_TOOL == "write_todos"
        assert _TASK_TOOL == "task"


# ── Tool-exclusion middleware (whitelist enforcement) ──────────────────


class TestToolExclusionMiddleware:
    """Verify ``_ToolExclusionMiddleware`` is passed via ``middleware=`` so
    the agent's tool whitelist actually constrains deepagents' auto-injected
    built-ins (bug: "无论选啥，测试都是全量注入")."""

    def _make_ctx_with_tool_engine(self, allowed_tools: list[str]):
        """Build a ctx with a real ToolEngine scoped to ``allowed_tools``."""
        from src.runtime.harness.context import HarnessContext
        from src.runtime.harness.tool_engine import ToolEngine, ToolRegistry

        agent = _make_agent()
        ctx = HarnessContext(
            workspace_id="ws-1",
            user_id="u-1",
            session_id="s-1",
            trace_id="t-1",
            agent=agent,
        )
        ctx.working_memory["system_prompt"] = "You are a test agent."
        ctx.tool_engine = ToolEngine(
            registry=ToolRegistry(),
            allowed_tools=allowed_tools,
        )
        return ctx

    async def _capture_middleware(self, ctx) -> dict:
        """Run the adapter with a mocked create_deep_agent and return the
        kwargs it was called with."""
        adapter = DeepAgentsAdapter(api_key="fake")
        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _awaitable_stream([])

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent) as mock_create, \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            await _collect(adapter.run(
                [{"role": "user", "content": "x"}], ctx
            ))

        return mock_create.call_args.kwargs

    @pytest.mark.asyncio
    async def test_middleware_passed_when_excluded_nonempty(self):
        """When there are built-ins to exclude, ``middleware=`` receives a
        non-empty list containing ``_ToolExclusionMiddleware`` followed by
        ``_SystemPromptStripperMiddleware``."""
        ctx = self._make_ctx_with_tool_engine(allowed_tools=[])
        kwargs = await self._capture_middleware(ctx)
        mw = kwargs.get("middleware")
        assert mw is not None
        assert len(mw) == 2  # _ToolExclusionMiddleware + _SystemPromptStripperMiddleware
        # The first middleware exposes the excluded set as ``_excluded``.
        excluded = mw[0]._excluded
        # All deepagents built-ins excluded (no whitelist, no subagents).
        assert "read_file" in excluded
        assert "write_todos" in excluded
        assert "task" in excluded
        assert "ls" in excluded  # colliding + not whitelisted → excluded
        # The second middleware is the system-prompt stripper.
        from src.runtime.adapters.deepagents import _SystemPromptStripperMiddleware
        assert isinstance(mw[1], _SystemPromptStripperMiddleware)
        assert mw[1]._strip_fs is True
        assert mw[1]._strip_todos is True
        assert mw[1]._strip_execute is True

    @pytest.mark.asyncio
    async def test_whitelisted_colliding_tool_is_not_excluded(self):
        """When ``ls`` is whitelisted, it should NOT be in the excluded set
        (deepagents' version is kept as the sole provider)."""
        ctx = self._make_ctx_with_tool_engine(allowed_tools=["ls", "read"])
        kwargs = await self._capture_middleware(ctx)
        mw = kwargs.get("middleware")
        assert mw is not None
        excluded = mw[0]._excluded
        assert "ls" not in excluded  # whitelisted → kept
        assert "glob" in excluded    # not whitelisted → excluded
        assert "grep" in excluded    # not whitelisted → excluded
        # Non-colliding built-ins always excluded:
        assert "read_file" in excluded
        assert "write_todos" in excluded

    @pytest.mark.asyncio
    async def test_empty_whitelist_excludes_all_builtins(self):
        """Empty whitelist → all 9 deepagents built-ins excluded."""
        ctx = self._make_ctx_with_tool_engine(allowed_tools=[])
        kwargs = await self._capture_middleware(ctx)
        mw = kwargs.get("middleware")
        excluded = mw[0]._excluded
        for t in ("ls", "read_file", "write_file", "edit_file", "glob",
                   "grep", "execute", "write_todos", "task"):
            assert t in excluded, f"{t!r} should be excluded"

    @pytest.mark.asyncio
    async def test_task_kept_when_subagents_configured(self):
        """When subagents are configured, ``task`` must NOT be excluded
        (it's the subagent-delegation mechanism)."""
        from src.runtime.harness.agents import AgentDefinition, SubagentSpec

        agent = AgentDefinition(
            id="a-sub",
            name="parent",
            workspace_id="ws-1",
            adapter="deepagents",
            system_prompt="x",
            subagents=[
                SubagentSpec(name="child", description="d", system_prompt="s"),
            ],
        )
        from src.runtime.harness.context import HarnessContext
        from src.runtime.harness.tool_engine import ToolEngine, ToolRegistry

        ctx = HarnessContext(
            workspace_id="ws-1", user_id="u-1", session_id="s-1",
            trace_id="t-1", agent=agent,
        )
        ctx.working_memory["system_prompt"] = "x"
        ctx.tool_engine = ToolEngine(
            registry=ToolRegistry(), allowed_tools=[]
        )

        kwargs = await self._capture_middleware(ctx)
        mw = kwargs.get("middleware")
        excluded = mw[0]._excluded
        assert "task" not in excluded  # subagents configured → kept
        # Other built-ins still excluded:
        assert "write_todos" in excluded
        assert "read_file" in excluded

    @pytest.mark.asyncio
    async def test_colliding_tools_skipped_from_shims(self):
        """Platform shims for colliding tools (ls/glob/grep) must NOT be
        passed via ``tools=`` (deepagents' versions handle them)."""
        from src.runtime.harness.tool_engine import ToolDefinition, ToolRegistry, ToolEngine

        # Register ls + read + glob in the registry, whitelist all three.
        registry = ToolRegistry()
        for name in ("ls", "read", "glob"):
            registry.register(ToolDefinition(
                name=name, description="d", input_schema={},
            ))
        ctx = _make_ctx()
        ctx.tool_engine = ToolEngine(
            registry=registry, allowed_tools=["ls", "read", "glob"],
        )

        kwargs = await self._capture_middleware(ctx)
        tools_passed = kwargs.get("tools")
        tool_names = [t.name for t in tools_passed]
        # read → platform shim (non-colliding)
        assert "read" in tool_names
        # ls/glob → colliding, skipped (deepagents' versions handle them)
        assert "ls" not in tool_names
        assert "glob" not in tool_names


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

    @pytest.mark.asyncio
    async def test_run_with_checkpointer_passes_only_last_user_message(self):
        """When a checkpointer is present (second+ round), only the last
        user message should be passed to deepagents — the checkpoint
        already holds the full conversation history."""
        from src.runtime.harness.checkpoint import SQLiteCheckpointStore

        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()
        # Wire a real checkpoint store so the checkpointer branch is taken.
        # Wave 2.5: ctx.checkpoint is the store directly (no CheckpointScope).
        ctx.checkpoint = SQLiteCheckpointStore()

        captured_input: dict = {}

        async def _capturing_stream(input_payload, *args, **kwargs):
            captured_input.update(input_payload)
            async def _gen():
                return
                yield  # type: ignore[unreachable]
            return _gen()

        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _capturing_stream

        # Simulate a second round: full history with multiple turns.
        full_history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "what is your prompt?"},
        ]
        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent), \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            await _collect(adapter.run(full_history, ctx))

        messages = captured_input.get("messages", [])
        # Only the last user message should be passed.
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "what is your prompt?"

    @pytest.mark.asyncio
    async def test_run_without_checkpointer_passes_full_history(self):
        """Without a checkpointer (first round),
        the full message history should be passed to deepagents."""
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()
        # No checkpoint — ctx.checkpoint is None by default.

        captured_input: dict = {}

        async def _capturing_stream(input_payload, *args, **kwargs):
            captured_input.update(input_payload)
            async def _gen():
                return
                yield  # type: ignore[unreachable]
            return _gen()

        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _capturing_stream

        full_history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "follow up"},
        ]
        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent), \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            await _collect(adapter.run(full_history, ctx))

        messages = captured_input.get("messages", [])
        # Without checkpointer, all non-system messages should be passed.
        assert len(messages) == 3
        assert messages[0]["content"] == "hello"
        assert messages[2]["content"] == "follow up"


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
                    model="deepseek-v4-flash",
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
        assert subagents[0]["model"] == "deepseek-v4-flash"

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


# ── Max Tokens wiring (bug: "Agent Max Tokens 不起作用") ────────────────


class TestMaxTokensWiring:
    """Regression guard for the bug report "Agent Max Tokens 不起作用".

    Root cause: langchain-openai rewrites the ``max_tokens`` kwarg to
    ``max_completion_tokens`` on the wire. DeepSeek's OpenAI-compatible API
    silently ignores the unknown ``max_completion_tokens`` field, so the
    limit never applied and the agent ignored the setting.

    The adapter must inject ``max_tokens`` via ``extra_body`` so the request
    carries ``max_tokens`` (and no ``max_completion_tokens``).
    """

    async def _capture_init_chat_model_kwargs(self, agent=None) -> dict:
        adapter = DeepAgentsAdapter(api_key="fake")
        mock_deep_agent = MagicMock()
        mock_deep_agent.astream_events = _awaitable_stream([])

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent), \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()) as mock_init:
            await _collect(adapter.run(
                [{"role": "user", "content": "x"}], _make_ctx(agent=agent)
            ))

        assert mock_init.called, "init_chat_model was never called"
        return mock_init.call_args.kwargs

    @pytest.mark.asyncio
    async def test_max_tokens_injected_via_extra_body(self):
        """An agent with ``max_tokens=1000`` must send ``max_tokens=1000`` inside
        ``extra_body``, and must NOT pass a bare ``max_tokens`` kwarg
        (otherwise langchain rewrites it to ``max_completion_tokens``)."""
        from src.runtime.harness.agents import AgentDefinition

        agent = AgentDefinition(
            id="a-max",
            name="max-tok",
            workspace_id="ws-1",
            adapter="deepagents",
            system_prompt="x",
            max_tokens=1000,
        )
        kwargs = await self._capture_init_chat_model_kwargs(agent)

        extra = kwargs.get("extra_body") or {}
        assert extra.get("max_tokens") == 1000, (
            f"max_tokens must be injected via extra_body; kwargs={kwargs}"
        )
        # The bare kwarg must NOT be passed — that is the exact bug.
        assert "max_tokens" not in kwargs, (
            f"max_tokens kwarg must not be passed (rewritten to "
            f"max_completion_tokens by langchain); kwargs={kwargs}"
        )

    @pytest.mark.asyncio
    async def test_max_tokens_small_value_passed_through(self):
        """Small ``max_tokens`` values (e.g. 10) are passed through as-is —
        no floor is applied. This lets users diagnose whether thinking
        tokens are eating the output budget."""
        from src.runtime.harness.agents import AgentDefinition

        agent = AgentDefinition(
            id="a-floor",
            name="floor-tok",
            workspace_id="ws-1",
            adapter="deepagents",
            system_prompt="x",
            max_tokens=10,
        )
        kwargs = await self._capture_init_chat_model_kwargs(agent)

        extra = kwargs.get("extra_body") or {}
        assert extra.get("max_tokens") == 10, (
            f"max_tokens should pass through without floor; kwargs={kwargs}"
        )

    @pytest.mark.asyncio
    async def test_default_max_tokens_falls_back_to_4096(self):
        """A default agent (no max_tokens) must still carry the 4096 fallback
        via ``extra_body``."""
        kwargs = await self._capture_init_chat_model_kwargs()  # _make_agent() default
        extra = kwargs.get("extra_body") or {}
        assert extra.get("max_tokens") == 4096, (
            f"default max_tokens should be 4096 via extra_body; kwargs={kwargs}"
        )
        assert "max_tokens" not in kwargs
