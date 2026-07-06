"""Phase 4: Tests for DeepAgentsAdapter.

Covers:
- run() with empty messages raises ValueError
- _normalize_messages strips leading system message
- _translate_event maps on_chat_model_stream → text event
- _translate_event maps on_tool_start → tool_call event (already_executed=True)
- _translate_event maps on_tool_end → tool_result event
- _translate_event maps on_chain_start name="task" → subagent start event
- _translate_event maps on_chain_end name="task" → subagent end event
- _translate_event maps on_chat_model_end with usage → status event
- _translate_event maps error events → error StreamEvent
- _translate_event returns [] for unrecognized events
- excluded_tools includes the 7 filesystem tool names
- run() yields a final status event when no usage was emitted

All tests mock ``create_deep_agent`` and ``astream_events`` so no real
LLM calls are made.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.runtime.adapters.deepagents import DeepAgentsAdapter, _EXCLUDED_FILESYSTEM_TOOLS
from src.runtime.models import StreamEvent


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_event(
    event: str,
    *,
    name: str = "",
    data: dict | None = None,
    run_id: str = "r-1",
) -> dict:
    return {
        "event": event,
        "name": name,
        "data": data or {},
        "run_id": run_id,
        "tags": [],
        "metadata": {},
    }


class _MockChunk:
    """Stand-in for LangChain AIMessageChunk."""
    def __init__(self, content: str):
        self.content = content


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
    def test_on_chat_model_stream_yields_text_event(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event(
            "on_chat_model_stream",
            data={"chunk": _MockChunk("hello")},
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "text"
        assert result[0].data["content"] == "hello"

    def test_on_chat_model_stream_with_empty_content_returns_empty(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event(
            "on_chat_model_stream",
            data={"chunk": _MockChunk("")},
        )
        result = adapter._translate_event(event)
        assert result == []

    def test_on_tool_start_yields_tool_call_with_already_executed(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event(
            "on_tool_start",
            name="shell_exec",
            data={"input": {"command": "echo hi"}},
            run_id="r-tool-1",
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "tool_call"
        assert result[0].data["name"] == "shell_exec"
        assert result[0].data["args"] == {"command": "echo hi"}
        assert result[0].data["call_id"] == "r-tool-1"
        assert result[0].already_executed is True

    def test_on_tool_end_yields_tool_result_with_already_executed(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event(
            "on_tool_end",
            name="shell_exec",
            data={"output": "hi\n"},
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "tool_result"
        assert result[0].data["name"] == "shell_exec"
        assert result[0].data["output"] == "hi\n"
        assert result[0].data["error"] is None
        assert result[0].already_executed is True

    def test_on_tool_end_with_error_string(self):
        """If the shim returned an ERROR: string, the tool_result should
        parse it into output='' and error=<message>."""
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event(
            "on_tool_end",
            name="boom_tool",
            data={"output": "ERROR: something failed"},
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "tool_result"
        assert result[0].data["output"] == ""
        assert result[0].data["error"] == "something failed"

    def test_on_chain_start_task_yields_subagent_start(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event(
            "on_chain_start",
            name="task",
            data={"input": {"name": "web-searcher", "subagent_type": "research"}},
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "subagent"
        assert result[0].data["action"] == "start"
        assert result[0].data["name"] == "web-searcher"
        assert result[0].data["subagent_type"] == "research"

    def test_on_chain_end_task_yields_subagent_end(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event(
            "on_chain_end",
            name="task",
            data={"output": _MockMessage("subagent result")},
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "subagent"
        assert result[0].data["action"] == "end"
        assert result[0].data["output"] == "subagent result"

    def test_on_chat_model_end_with_usage_yields_status(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        msg = _MockMessage(
            "final",
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
            },
        )
        event = _make_event(
            "on_chat_model_end",
            data={"output": msg},
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "status"
        assert result[0].data["usage"]["input_tokens"] == 100
        assert result[0].data["usage"]["output_tokens"] == 50
        assert result[0].data["usage"]["total_tokens"] == 150

    def test_on_chat_model_end_without_usage_returns_empty(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        msg = _MockMessage("final", usage_metadata=None)
        event = _make_event(
            "on_chat_model_end",
            data={"output": msg},
        )
        result = adapter._translate_event(event)
        assert result == []

    def test_error_event_yields_error_stream_event(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event(
            "on_chain_error",
            data={"error": "something broke"},
        )
        result = adapter._translate_event(event)
        assert len(result) == 1
        assert result[0].type == "error"
        assert result[0].data["code"] == "DEEPAGENTS_EVENT_ERROR"

    def test_unrecognized_event_returns_empty(self):
        adapter = DeepAgentsAdapter(api_key="fake")
        event = _make_event("on_some_unknown_event", data={"foo": "bar"})
        result = adapter._translate_event(event)
        assert result == []


# ── excluded_tools ──────────────────────────────────────────────────────


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
        """End-to-end: mock create_deep_agent + astream_events."""
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()

        # Build a mock deep_agent whose astream_events yields our events.
        mock_deep_agent = MagicMock()
        async def _mock_astream_events(*args, **kwargs):
            yield _make_event(
                "on_chat_model_stream",
                data={"chunk": _MockChunk("hello")},
            )
            yield _make_event(
                "on_chat_model_stream",
                data={"chunk": _MockChunk(" world")},
            )
            yield _make_event(
                "on_chat_model_end",
                data={"output": _MockMessage(
                    "final",
                    usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                )},
            )
        mock_deep_agent.astream_events = _mock_astream_events

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

        mock_deep_agent = MagicMock()
        async def _mock_astream_events(*args, **kwargs):
            yield _make_event(
                "on_chat_model_stream",
                data={"chunk": _MockChunk("hi")},
            )
            # No on_chat_model_end with usage
        mock_deep_agent.astream_events = _mock_astream_events

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

        mock_deep_agent = MagicMock()
        async def _mock_astream_events(*args, **kwargs):
            raise RuntimeError("deepagents blew up")
            yield  # unreachable — make it an async generator
        mock_deep_agent.astream_events = _mock_astream_events

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
    async def test_run_passes_excluded_tools_to_create_deep_agent(self):
        """create_deep_agent should receive the 7 excluded tool names."""
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()

        mock_deep_agent = MagicMock()
        async def _mock_astream_events(*args, **kwargs):
            return
            yield  # type: ignore[unreachable]
        mock_deep_agent.astream_events = _mock_astream_events

        with patch("deepagents.create_deep_agent", return_value=mock_deep_agent) as mock_create, \
             patch("langchain.chat_models.init_chat_model", return_value=MagicMock()):
            await _collect(adapter.run(
                [{"role": "user", "content": "x"}], ctx
            ))

        # Inspect the call args
        call_kwargs = mock_create.call_args.kwargs
        excluded = call_kwargs.get("excluded_tools", set())
        assert excluded == _EXCLUDED_FILESYSTEM_TOOLS

    @pytest.mark.asyncio
    async def test_run_passes_subagents_none_when_empty(self):
        """When agent.subagents is empty, subagents=None should be passed
        to create_deep_agent (deepagents treats None as 'no subagents')."""
        adapter = DeepAgentsAdapter(api_key="fake")
        ctx = _make_ctx()  # agent has no subagents by default

        mock_deep_agent = MagicMock()
        async def _mock_astream_events(*args, **kwargs):
            return
            yield  # type: ignore[unreachable]
        mock_deep_agent.astream_events = _mock_astream_events

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
        async def _mock_astream_events(*args, **kwargs):
            return
            yield  # type: ignore[unreachable]
        mock_deep_agent.astream_events = _mock_astream_events

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
        async def _mock_astream_events(*args, **kwargs):
            return
            yield  # type: ignore[unreachable]
        mock_deep_agent.astream_events = _mock_astream_events

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

        mock_deep_agent = MagicMock()
        async def _mock_astream_events(input_payload, *args, **kwargs):
            captured_input.update(input_payload)
            return
            yield  # type: ignore[unreachable]
        mock_deep_agent.astream_events = _mock_astream_events

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
