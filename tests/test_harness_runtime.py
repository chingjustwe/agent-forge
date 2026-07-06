"""P3a: Tests for HarnessRuntime orchestration.

Covers:
- _resolve_agent: empty agent → default; not found → default; found → returned
- _default_agent: built from RuntimeConfig fields
- _resolve_adapter: direct_llm returns DirectLLMAdapter; unknown falls back
- _build_context: tool_engine scoped to agent.tools; guardrails wired
- run() happy path: yields text events from adapter
- run() guardrail block: yields GUARDRAIL_BLOCKED error and stops
- run() guardrail redact: passes modified messages to adapter
- run() tool_call interception: yields tool_result event
- run() tool_call error: yields tool_result with error field
- _run_with_retry: retries on TimeoutError; surfaces RETRY_EXHAUSTED
- _run_with_retry: non-retryable error yields ADAPTER_ERROR immediately

DB-backed agent resolution is mocked to keep these tests focused on
orchestration logic. AgentRegistry CRUD is covered separately in
test_agent_registry.py.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.runtime.adapters.base import RunAdapter
from src.runtime.adapters.direct_llm import DirectLLMAdapter
from src.runtime.harness.agents import AgentDefinition
from src.runtime.harness.context import HarnessContext
from src.runtime.harness.registry import HarnessRegistry
from src.runtime.harness.runtime import HarnessRuntime
from src.runtime.harness.tool_engine import ToolPermissionError
from src.runtime.models import RuntimeConfig, StreamEvent


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_config(
    *,
    agent: str = "",
    workspace_id: str = "ws-rt",
    model: str = "deepseek-chat",
) -> RuntimeConfig:
    return RuntimeConfig(
        agent=agent,
        model=model,
        max_tokens=2048,
        temperature=0.5,
        workspace_id=workspace_id,
    )


def _make_agent(
    *,
    id: str = "a-rt",
    name: str = "rt-agent",
    tools: list[str] | None = None,
    adapter: str = "direct_llm",
    model: str = "deepseek-chat",
) -> AgentDefinition:
    return AgentDefinition(
        id=id,
        name=name,
        workspace_id="ws-rt",
        model=model,
        tools=tools if tools is not None else [],
        adapter=adapter,
    )


class _FakeAdapter(RunAdapter):
    """Yields a configured list of events. Tracks messages it received."""

    name = "fake"

    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = events
        self.received_messages: list[list[dict]] | None = None
        self.received_ctx: HarnessContext | None = None
        self.call_count = 0

    async def run(
        self, messages: list[dict], ctx: HarnessContext
    ) -> AsyncIterator[StreamEvent]:
        self.call_count += 1
        self.received_messages = list(messages)
        self.received_ctx = ctx
        for event in self._events:
            yield event


def _make_runtime(
    *,
    agent: AgentDefinition | None = None,
    adapter: RunAdapter | None = None,
    guardrails: Any | None = None,
) -> tuple[HarnessRuntime, HarnessRegistry]:
    """Build a runtime with a real registry but mocked agent resolution.

    Returns (runtime, registry) so tests can inspect registry state.
    """
    registry = HarnessRegistry.create()
    runtime = HarnessRuntime(registry)
    if guardrails is not None:
        registry.guardrails = guardrails
    # Bypass DB-backed agent resolution.
    runtime._resolve_agent = AsyncMock(return_value=agent)  # type: ignore
    if adapter is not None:
        # Pre-seed adapter cache so _resolve_adapter returns our fake.
        runtime._adapters[agent.adapter if agent else "direct_llm"] = adapter
    return runtime, registry


async def _collect(stream: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    out: list[StreamEvent] = []
    async for ev in stream:
        out.append(ev)
    return out


# ── _resolve_agent / _default_agent ─────────────────────────────────────


class TestResolveAgent:
    @pytest.mark.asyncio
    async def test_empty_agent_returns_default(self):
        """When config.agent is empty, _resolve_agent returns a synthetic default."""
        runtime, _ = _make_runtime(agent=None)
        # Restore the real method (we mocked it in _make_runtime).
        runtime._resolve_agent = HarnessRuntime._resolve_agent.__get__(runtime)  # type: ignore
        config = _make_config(agent="")
        agent = await runtime._resolve_agent(config)
        assert agent is not None
        assert agent.id == ""
        assert agent.name == "default"
        assert agent.model == config.model
        assert agent.adapter == "direct_llm"

    @pytest.mark.asyncio
    async def test_default_agent_carries_config_fields(self):
        runtime, _ = _make_runtime(agent=None)
        runtime._resolve_agent = HarnessRuntime._resolve_agent.__get__(runtime)  # type: ignore
        config = _make_config(model="custom-model")
        agent = await runtime._resolve_agent(config)
        assert agent.model == "custom-model"
        assert agent.max_tokens == config.max_tokens
        assert agent.temperature == config.temperature


class TestResolveAdapter:
    def test_direct_llm_returns_direct_adapter(self):
        runtime, _ = _make_runtime(agent=_make_agent(adapter="direct_llm"))
        config = _make_config()
        adapter = runtime._resolve_adapter("direct_llm", config)
        assert isinstance(adapter, DirectLLMAdapter)

    def test_unknown_adapter_falls_back_to_direct(self):
        runtime, _ = _make_runtime(agent=_make_agent(adapter="adk"))
        config = _make_config()
        adapter = runtime._resolve_adapter("adk", config)
        # Unknown adapters fall back to DirectLLMAdapter in P0.
        assert isinstance(adapter, DirectLLMAdapter)

    def test_adapter_is_cached(self):
        runtime, _ = _make_runtime(agent=_make_agent())
        config = _make_config()
        a1 = runtime._resolve_adapter("direct_llm", config)
        a2 = runtime._resolve_adapter("direct_llm", config)
        assert a1 is a2


# ── _build_context ──────────────────────────────────────────────────────


class TestBuildContext:
    @pytest.mark.asyncio
    async def test_context_wires_agent_tools_into_engine(self):
        agent = _make_agent(tools=["todo_write", "todo_read"])
        runtime, _ = _make_runtime(agent=agent)
        config = _make_config()
        ctx = runtime._build_context(
            agent=agent, config=config, session_id="s-1", user_id="u-1",
            trace_id="t-1", workspace_settings={}, workspace_root="",
        )
        assert ctx.agent is agent
        assert ctx.tool_engine is not None
        assert ctx.tool_engine.is_allowed("todo_write") is True
        assert ctx.tool_engine.is_allowed("todo_read") is True
        # Non-whitelisted tool is blocked.
        assert ctx.tool_engine.is_allowed("shell_exec") is False

    @pytest.mark.asyncio
    async def test_context_wires_registry_guardrails(self):
        agent = _make_agent()
        runtime, registry = _make_runtime(agent=agent)
        config = _make_config()
        ctx = runtime._build_context(
            agent=agent, config=config, session_id="s-1", user_id="u-1",
            trace_id="t-1", workspace_settings={}, workspace_root="",
        )
        assert ctx.guardrails is registry.guardrails

    @pytest.mark.asyncio
    async def test_context_carries_identity_fields(self):
        agent = _make_agent()
        runtime, _ = _make_runtime(agent=agent)
        config = _make_config()
        ctx = runtime._build_context(
            agent=agent, config=config, session_id="s-xyz", user_id="u-abc",
            trace_id="t-def", workspace_settings={"k": "v"},
            workspace_root="/tmp/ws",
        )
        assert ctx.session_id == "s-xyz"
        assert ctx.user_id == "u-abc"
        assert ctx.trace_id == "t-def"
        assert ctx.workspace_settings == {"k": "v"}
        assert ctx.workspace_root == "/tmp/ws"


# ── run() orchestration ─────────────────────────────────────────────────


class TestRunOrchestration:
    @pytest.mark.asyncio
    async def test_yields_text_events_from_adapter(self):
        agent = _make_agent()
        fake = _FakeAdapter([
            StreamEvent(type="text", data={"delta": "Hello"}),
            StreamEvent(type="text", data={"delta": " world"}),
        ])
        runtime, _ = _make_runtime(agent=agent, adapter=fake)
        events = await _collect(runtime.run(
            "s-1", [{"role": "user", "content": "hi"}], _make_config(),
            user_id="u-1",
        ))
        assert len(events) == 2
        assert events[0].type == "text"
        assert events[0].data["delta"] == "Hello"
        assert events[1].data["delta"] == " world"
        # Adapter received the original messages.
        assert fake.received_messages == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_guardrail_block_yields_error_and_stops(self):
        """When pre-flight guardrail blocks, adapter must not run."""
        from src.runtime.harness.guardrails.base import (
            Guardrail,
            GuardrailPipeline,
            GuardrailResult,
        )

        class _Blocker(Guardrail):
            name = "blocker"
            direction = "input"

            async def check(self, content, ctx) -> GuardrailResult:
                return GuardrailResult(
                    passed=False, action="block", reason="input forbidden"
                )

        pipe = GuardrailPipeline()
        pipe.add(_Blocker())
        agent = _make_agent()
        fake = _FakeAdapter([StreamEvent(type="text", data={"delta": "x"})])
        runtime, _ = _make_runtime(agent=agent, adapter=fake, guardrails=pipe)
        events = await _collect(runtime.run(
            "s-1", [{"role": "user", "content": "hi"}], _make_config(),
            user_id="u-1",
        ))
        assert len(events) == 1
        assert events[0].type == "error"
        assert events[0].data["code"] == "GUARDRAIL_BLOCKED"
        assert "forbidden" in events[0].data["message"]
        # Adapter must not have been called.
        assert fake.call_count == 0

    @pytest.mark.asyncio
    async def test_guardrail_redact_passes_modified_messages(self):
        """Redact action: modified_messages replaces original input."""
        from src.runtime.harness.guardrails.base import (
            Guardrail,
            GuardrailPipeline,
            GuardrailResult,
        )

        class _Redactor(Guardrail):
            name = "redactor"
            direction = "input"

            async def check(self, content, ctx) -> GuardrailResult:
                # Replace each message content with REDACTED.
                modified = [
                    {**m, "content": "[REDACTED]"} if isinstance(m, dict) else m
                    for m in content
                ]
                return GuardrailResult(
                    passed=True, action="redact", reason="redacted",
                    modified_messages=modified,
                )

        pipe = GuardrailPipeline()
        pipe.add(_Redactor())
        agent = _make_agent()
        fake = _FakeAdapter([StreamEvent(type="text", data={"delta": "ok"})])
        runtime, _ = _make_runtime(agent=agent, adapter=fake, guardrails=pipe)
        await _collect(runtime.run(
            "s-1", [{"role": "user", "content": "secret"}], _make_config(),
            user_id="u-1",
        ))
        # Adapter should have received the redacted messages.
        assert fake.received_messages == [{"role": "user", "content": "[REDACTED]"}]

    @pytest.mark.asyncio
    async def test_tool_call_interception_yields_tool_result(self):
        """When adapter emits tool_call, runtime executes it and yields tool_result."""
        agent = _make_agent(tools=["todo_write"])
        fake = _FakeAdapter([
            StreamEvent(type="tool_call", data={
                "name": "todo_write",
                "args": {"todos": [{"content": "task", "status": "pending"}]},
            }),
            StreamEvent(type="text", data={"delta": "done"}),
        ])
        runtime, _ = _make_runtime(agent=agent, adapter=fake)
        events = await _collect(runtime.run(
            "s-1", [{"role": "user", "content": "do"}], _make_config(),
            user_id="u-1",
        ))
        assert len(events) == 2
        assert events[0].type == "tool_result"
        assert events[0].data["name"] == "todo_write"
        assert "Replaced task list" in events[0].data["output"]
        assert events[0].data["error"] is None
        assert events[1].type == "text"

    @pytest.mark.asyncio
    async def test_tool_call_permission_error_yields_error_result(self):
        """Adapter calls a tool not in agent.tools — yields tool_result with error."""
        agent = _make_agent(tools=["todo_read"])  # only read, not write
        fake = _FakeAdapter([
            StreamEvent(type="tool_call", data={
                "name": "shell_exec",
                "args": {"command": "echo hi"},
            }),
        ])
        runtime, _ = _make_runtime(agent=agent, adapter=fake)
        events = await _collect(runtime.run(
            "s-1", [{"role": "user", "content": "x"}], _make_config(),
            user_id="u-1",
        ))
        assert len(events) == 1
        assert events[0].type == "tool_result"
        assert events[0].data["name"] == "shell_exec"
        assert "ToolPermissionError" in (events[0].data["error"] or "")

    @pytest.mark.asyncio
    async def test_tool_call_unknown_tool_yields_error_result(self):
        """Adapter calls a tool not registered — yields tool_result with error."""
        agent = _make_agent(tools=["bogus_tool"])  # whitelisted but not registered
        fake = _FakeAdapter([
            StreamEvent(type="tool_call", data={
                "name": "bogus_tool", "args": {},
            }),
        ])
        runtime, _ = _make_runtime(agent=agent, adapter=fake)
        events = await _collect(runtime.run(
            "s-1", [{"role": "user", "content": "x"}], _make_config(),
            user_id="u-1",
        ))
        assert len(events) == 1
        assert events[0].type == "tool_result"
        assert "ToolNotFoundError" in (events[0].data["error"] or "")

    @pytest.mark.asyncio
    async def test_guardrail_exception_is_swallowed(self):
        """If a guardrail raises, runtime logs and treats as no block."""
        from src.runtime.harness.guardrails.base import (
            Guardrail,
            GuardrailPipeline,
            GuardrailResult,
        )

        class _CrashingGuard(Guardrail):
            name = "crasher"
            direction = "input"

            async def check(self, content, ctx) -> GuardrailResult:
                raise RuntimeError("boom")

        pipe = GuardrailPipeline()
        pipe.add(_CrashingGuard())
        agent = _make_agent()
        fake = _FakeAdapter([StreamEvent(type="text", data={"delta": "ok"})])
        runtime, _ = _make_runtime(agent=agent, adapter=fake, guardrails=pipe)
        events = await _collect(runtime.run(
            "s-1", [{"role": "user", "content": "x"}], _make_config(),
            user_id="u-1",
        ))
        # Adapter still ran; no error event from guardrail.
        assert len(events) == 1
        assert events[0].type == "text"
        assert fake.call_count == 1


# ── _run_with_retry ─────────────────────────────────────────────────────


class TestRunWithRetry:
    @pytest.mark.asyncio
    async def test_retries_on_timeout_then_succeeds(self):
        """Adapter times out once, then succeeds on retry."""
        call_count = 0

        class _RetryAdapter(RunAdapter):
            name = "retry"

            async def run(self, messages, ctx):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise TimeoutError("first call timeout")
                yield StreamEvent(type="text", data={"delta": "ok"})

        agent = _make_agent()
        runtime, _ = _make_runtime(agent=agent)
        # Patch sleep to make the test fast.
        with patch("src.runtime.harness.runtime.asyncio.sleep", new=AsyncMock()):
            events = await _collect(runtime._run_with_retry(
                _RetryAdapter(), [{"role": "user", "content": "x"}],
                HarnessContext(
                    workspace_id="ws", user_id="u", session_id="s",
                    trace_id="t", agent=agent,
                ),
            ))
        assert call_count == 2
        assert len(events) == 1
        assert events[0].type == "text"
        assert events[0].data["delta"] == "ok"

    @pytest.mark.asyncio
    async def test_retries_exhausted_yields_error(self):
        """Adapter keeps timing out — runtime yields RETRY_EXHAUSTED."""
        call_count = 0

        class _AlwaysTimeoutAdapter(RunAdapter):
            name = "always_timeout"

            async def run(self, messages, ctx):
                nonlocal call_count
                call_count += 1
                raise TimeoutError("always timeout")
                yield  # type: ignore[unreachable] — make it an async generator

        agent = _make_agent()
        runtime, _ = _make_runtime(agent=agent)
        # P1: retry policy is configurable via runtime._retry_policy
        runtime._retry_policy.max_retries = 2
        with patch("src.runtime.harness.runtime.asyncio.sleep", new=AsyncMock()):
            events = await _collect(runtime._run_with_retry(
                _AlwaysTimeoutAdapter(), [{"role": "user", "content": "x"}],
                HarnessContext(
                    workspace_id="ws", user_id="u", session_id="s",
                    trace_id="t", agent=agent,
                ),
            ))
        # 1 initial + 2 retries = 3 calls.
        assert call_count == 3
        assert len(events) == 1
        assert events[0].type == "error"
        assert events[0].data["code"] == "RETRY_EXHAUSTED"
        assert events[0].data["attempts"] == 3

    @pytest.mark.asyncio
    async def test_non_retryable_error_yields_adapter_error(self):
        """ValueError is not retryable — yields ADAPTER_ERROR immediately."""
        class _ValueErrorAdapter(RunAdapter):
            name = "value_error"

            async def run(self, messages, ctx):
                raise ValueError("bad input")
                yield  # type: ignore[unreachable]

        agent = _make_agent()
        runtime, _ = _make_runtime(agent=agent)
        events = await _collect(runtime._run_with_retry(
            _ValueErrorAdapter(), [{"role": "user", "content": "x"}],
            HarnessContext(
                workspace_id="ws", user_id="u", session_id="s",
                trace_id="t", agent=agent,
            ),
        ))
        assert len(events) == 1
        assert events[0].type == "error"
        assert events[0].data["code"] == "ADAPTER_ERROR"
        assert "ValueError" in events[0].data["message"]
