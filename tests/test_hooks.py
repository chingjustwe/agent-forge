"""Tests for HookRegistry and builtin hooks.

Covers:
- HookRegistry: register/list, multi-event registration, unregister,
  event-scoped listing, trigger with no hooks, priority ordering,
  payload flow-through, exception swallowing
- AuditLogHook: records tool.call, ignores irrelevant events
- MetricHook: counts tool calls, run.start, errors
- TraceHook: creates span on tool.call, removes span on tool.result
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.runtime.harness.agents import AgentDefinition
from src.runtime.harness.context import HarnessContext
from src.runtime.harness.hooks import (
    AuditLogHook,
    Hook,
    HookRegistry,
    MetricHook,
    TraceHook,
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_ctx() -> HarnessContext:
    agent = AgentDefinition(
        id="a-1", name="test", workspace_id="ws-1", adapter="deepagents"
    )
    return HarnessContext(
        workspace_id="ws-1",
        user_id="u-1",
        session_id="s-1",
        trace_id="t-1",
        agent=agent,
    )


class _CountingHook(Hook):
    name = "counting"
    events = ["run.start", "run.end"]
    priority = 0

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, event, payload, ctx) -> dict:
        self.calls.append(event)
        return payload


class _ModifyingHook(Hook):
    name = "modifying"
    events = ["run.start"]
    priority = 0

    async def execute(self, event, payload, ctx) -> dict:
        payload["modified"] = True
        return payload


class _ErrorHook(Hook):
    name = "error_hook"
    events = ["run.start"]
    priority = 10

    async def execute(self, event, payload, ctx) -> dict:
        raise RuntimeError("boom")


class _PriorityHook(Hook):
    """Records its name into a shared list when executed."""

    events = ["run.start"]

    def __init__(self, name: str, priority: int, order: list[str]) -> None:
        self.name = name
        self.priority = priority
        self._order = order

    async def execute(self, event, payload, ctx) -> dict:
        self._order.append(self.name)
        return payload


class _ObservingHook(Hook):
    """Records whether payload was modified by an earlier hook."""

    name = "observing"
    events = ["run.start"]
    priority = 5

    def __init__(self) -> None:
        self.saw_modified: bool = False

    async def execute(self, event, payload, ctx) -> dict:
        self.saw_modified = payload.get("modified", False)
        return payload


# ── TestHookRegistry ────────────────────────────────────────────────────


class TestHookRegistry:
    @pytest.mark.asyncio
    async def test_register_and_list(self):
        reg = HookRegistry()
        reg.register(_CountingHook())
        assert len(reg.list()) == 1

    @pytest.mark.asyncio
    async def test_register_multiple_events(self):
        reg = HookRegistry()
        reg.register(_CountingHook())
        assert len(reg.list(event="run.start")) == 1
        assert len(reg.list(event="run.end")) == 1

    @pytest.mark.asyncio
    async def test_unregister(self):
        reg = HookRegistry()
        reg.register(_CountingHook())
        assert reg.unregister("counting") is True
        assert len(reg.list()) == 0

    @pytest.mark.asyncio
    async def test_unregister_returns_false_for_unknown(self):
        reg = HookRegistry()
        assert reg.unregister("nonexistent") is False

    @pytest.mark.asyncio
    async def test_list_by_event(self):
        reg = HookRegistry()

        class _ToolHook(Hook):
            name = "tool_hook"
            events = ["tool.call"]
            priority = 0

            async def execute(self, event, payload, ctx):
                return payload

        class _MsgHook(Hook):
            name = "msg_hook"
            events = ["message.user"]
            priority = 0

            async def execute(self, event, payload, ctx):
                return payload

        reg.register(_ToolHook())
        reg.register(_MsgHook())

        tool_hooks = reg.list(event="tool.call")
        assert len(tool_hooks) == 1
        assert tool_hooks[0].name == "tool_hook"

    @pytest.mark.asyncio
    async def test_trigger_no_hooks(self):
        reg = HookRegistry()
        ctx = _make_ctx()
        payload = {"x": 1}
        result = await reg.trigger("nonexistent.event", payload, ctx)
        assert result == {"x": 1}

    @pytest.mark.asyncio
    async def test_trigger_priority_order(self):
        reg = HookRegistry()
        order: list[str] = []
        # priorities 10, 5, 1 — lower runs first → order: 1, 5, 10
        reg.register(_PriorityHook(name="p10", priority=10, order=order))
        reg.register(_PriorityHook(name="p5", priority=5, order=order))
        reg.register(_PriorityHook(name="p1", priority=1, order=order))

        ctx = _make_ctx()
        await reg.trigger("run.start", {}, ctx)
        assert order == ["p1", "p5", "p10"]

    @pytest.mark.asyncio
    async def test_trigger_payload_flows_through(self):
        reg = HookRegistry()
        # _ModifyingHook has priority 0 (runs first), _ObservingHook has
        # priority 5 (runs second) — observer should see the modified flag.
        observer = _ObservingHook()
        reg.register(_ModifyingHook())
        reg.register(observer)

        ctx = _make_ctx()
        result = await reg.trigger("run.start", {}, ctx)
        assert observer.saw_modified is True
        assert result.get("modified") is True

    @pytest.mark.asyncio
    async def test_trigger_swallows_exceptions(self):
        reg = HookRegistry()
        # _ModifyingHook (priority 0) runs first and sets modified=True.
        # _ErrorHook (priority 10) runs second and raises. The exception
        # is swallowed and the last good (modified) payload is returned.
        reg.register(_ModifyingHook())
        reg.register(_ErrorHook())

        ctx = _make_ctx()
        result = await reg.trigger("run.start", {}, ctx)
        assert result.get("modified") is True


# ── TestBuiltinHooks ────────────────────────────────────────────────────


class TestBuiltinHooks:
    @pytest.mark.asyncio
    async def test_audit_log_hook_records(self):
        reg = HookRegistry()
        reg.register(AuditLogHook())
        ctx = _make_ctx()
        await reg.trigger("tool.call", {"name": "search"}, ctx)
        audit_log = ctx.working_memory.get("audit_log")
        assert audit_log is not None
        assert len(audit_log) == 1
        assert audit_log[0]["event"] == "tool.call"
        assert audit_log[0]["trace_id"] == "t-1"
        assert audit_log[0]["workspace_id"] == "ws-1"
        assert audit_log[0]["session_id"] == "s-1"

    @pytest.mark.asyncio
    async def test_audit_log_hook_ignores_irrelevant_events(self):
        reg = HookRegistry()
        reg.register(AuditLogHook())
        ctx = _make_ctx()
        await reg.trigger("message.user", {"text": "hi"}, ctx)
        assert "audit_log" not in ctx.working_memory

    @pytest.mark.asyncio
    async def test_metric_hook_counts_tool_calls(self):
        reg = HookRegistry()
        reg.register(MetricHook())
        ctx = _make_ctx()
        await reg.trigger("tool.call", {"name": "search"}, ctx)
        await reg.trigger("tool.call", {"name": "fetch"}, ctx)
        metrics = ctx.working_memory["metrics"]
        assert metrics["tool.search.count"] == 1
        assert metrics["tool.fetch.count"] == 1
        assert metrics["tool.calls.total"] == 2

    @pytest.mark.asyncio
    async def test_metric_hook_counts_run_start(self):
        reg = HookRegistry()
        reg.register(MetricHook())
        ctx = _make_ctx()
        await reg.trigger("run.start", {}, ctx)
        metrics = ctx.working_memory["metrics"]
        assert metrics["run.count"] == 1

    @pytest.mark.asyncio
    async def test_metric_hook_counts_errors(self):
        reg = HookRegistry()
        reg.register(MetricHook())
        ctx = _make_ctx()
        await reg.trigger("error", {"message": "oops"}, ctx)
        metrics = ctx.working_memory["metrics"]
        assert metrics["error.count"] == 1

    @pytest.mark.asyncio
    async def test_trace_hook_creates_span_on_tool_call(self):
        reg = HookRegistry()
        reg.register(TraceHook())
        ctx = _make_ctx()
        ctx.tracer = MagicMock()
        ctx.tracer.span = MagicMock(return_value=object())
        await reg.trigger("tool.call", {"name": "search"}, ctx)
        spans = ctx.working_memory.get("_trace_spans")
        assert spans is not None
        assert "tool.search" in spans
        assert spans["tool.search"] is not None
        ctx.tracer.span.assert_called_once()

    @pytest.mark.asyncio
    async def test_trace_hook_removes_span_on_tool_result(self):
        reg = HookRegistry()
        reg.register(TraceHook())
        ctx = _make_ctx()
        ctx.tracer = MagicMock()
        ctx.tracer.span = MagicMock(return_value=object())
        # Create the span first.
        await reg.trigger("tool.call", {"name": "search"}, ctx)
        assert "tool.search" in ctx.working_memory["_trace_spans"]
        # Now remove it via tool.result.
        await reg.trigger("tool.result", {"name": "search"}, ctx)
        assert "tool.search" not in ctx.working_memory["_trace_spans"]
