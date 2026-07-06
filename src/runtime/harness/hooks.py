"""P3a-P1: HookRegistry — lifecycle hooks.

Hooks are registered for specific events and run in priority order
(lower = earlier). The payload dict flows through each hook; a hook may
modify it and return the modified version.

Built-in hooks (registered by default by HarnessRegistry.create()):
- ``AuditLogHook`` — records tool.call / run.start / error events
- ``MetricHook``   — increments counters, records durations
- ``TraceHook``    — opens/closes spans around events

Custom hooks can be registered at runtime via ``HookRegistry.register()``.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext

logger = logging.getLogger(__name__)

# All valid hook event names.
HOOK_EVENTS = frozenset({
    "run.start",
    "run.end",
    "message.user",
    "message.assistant",
    "tool.call",
    "tool.result",
    "guardrail.pre",
    "guardrail.post",
    "error",
})


class Hook(ABC):
    """Abstract lifecycle hook.

    ``events`` lists the events this hook should fire on. ``execute``
    receives the actual event name so the hook can branch internally.
    """

    name: str
    events: list[str]
    priority: int = 0  # lower runs first

    @abstractmethod
    async def execute(
        self,
        event: str,
        payload: dict,
        ctx: "HarnessContext",
    ) -> dict:
        """Process the event. Return the (possibly modified) payload."""
        ...


class HookRegistry:
    """Registry of hooks, keyed by event name.

    ``trigger()`` runs all hooks for a given event in priority order.
    The payload flows through each hook sequentially. Exceptions in
    individual hooks are logged and swallowed so one bad hook doesn't
    break the pipeline.
    """

    def __init__(self) -> None:
        # event -> list of hooks (sorted by priority)
        self._hooks: dict[str, list[Hook]] = {}

    def register(self, hook: Hook) -> None:
        """Register a hook for all of its ``events``."""
        for event in hook.events:
            self._hooks.setdefault(event, [])
            self._hooks[event].append(hook)
            self._hooks[event].sort(key=lambda h: h.priority)
        logger.debug(
            "Registered hook %r for events %r", hook.name, hook.events
        )

    def unregister(self, name: str) -> bool:
        """Remove a hook by name from all events. Returns True if found."""
        removed = False
        for event, hooks_list in list(self._hooks.items()):
            new_list = [h for h in hooks_list if h.name != name]
            if len(new_list) < len(hooks_list):
                removed = True
                self._hooks[event] = new_list
        return removed

    def list(self, event: str | None = None) -> list[Hook]:
        if event:
            return list(self._hooks.get(event, []))
        seen: set[int] = set()
        out: list[Hook] = []
        for hooks in self._hooks.values():
            for h in hooks:
                if id(h) not in seen:
                    seen.add(id(h))
                    out.append(h)
        return out

    async def trigger(
        self,
        event: str,
        payload: dict,
        ctx: "HarnessContext",
    ) -> dict:
        """Run all hooks for ``event`` in priority order.

        Payload flows through each hook. Exceptions are logged and
        swallowed — the last successfully-returned payload is used.
        """
        for hook in self._hooks.get(event, []):
            try:
                result = await hook.execute(event, payload, ctx)
                if isinstance(result, dict):
                    payload = result
            except Exception as exc:
                logger.warning(
                    "Hook %r error on event %r: %s", hook.name, event, exc
                )
        return payload


# ── Built-in hooks ──────────────────────────────────────────────────────


class AuditLogHook(Hook):
    """Records tool.call, run.start, and error events to the audit log.

    Writes to ``ctx.working_memory["audit_log"]`` (a list of dicts).
    In production this would persist to the ``audit_logs`` table.
    """

    name = "audit_log"
    events = ["run.start", "tool.call", "error"]
    priority = 10

    async def execute(
        self,
        event: str,
        payload: dict,
        ctx: "HarnessContext",
    ) -> dict:
        entry = {
            "event": event,
            "trace_id": ctx.trace_id,
            "workspace_id": ctx.workspace_id,
            "session_id": ctx.session_id,
            "timestamp": time.time(),
            "payload": payload,
        }
        audit_log: list = ctx.working_memory.setdefault("audit_log", [])
        audit_log.append(entry)
        return payload


class MetricHook(Hook):
    """Increments counters and records durations for tool calls.

    Stores metrics in ``ctx.working_memory["metrics"]`` (a dict).
    In production this would delegate to ``ctx.metrics``.
    """

    name = "metric"
    events = ["run.start", "tool.call", "error"]
    priority = 5

    async def execute(
        self,
        event: str,
        payload: dict,
        ctx: "HarnessContext",
    ) -> dict:
        metrics: dict = ctx.working_memory.setdefault("metrics", {})
        if event == "tool.call":
            tool_name = payload.get("name", "unknown")
            key = f"tool.{tool_name}.count"
            metrics[key] = metrics.get(key, 0) + 1
            metrics["tool.calls.total"] = metrics.get("tool.calls.total", 0) + 1
        elif event == "run.start":
            metrics["run.count"] = metrics.get("run.count", 0) + 1
        elif event == "error":
            metrics["error.count"] = metrics.get("error.count", 0) + 1
        return payload


class TraceHook(Hook):
    """Opens/closes spans around run and tool events.

    Delegates to ``ctx.tracer`` for actual span management.
    """

    name = "trace"
    events = ["run.start", "tool.call", "tool.result", "run.end"]
    priority = 1

    async def execute(
        self,
        event: str,
        payload: dict,
        ctx: "HarnessContext",
    ) -> dict:
        if event == "tool.call":
            tool_name = payload.get("name", "unknown")
            try:
                span = ctx.tracer.span(
                    f"tool.{tool_name}",
                    ctx.trace_id,
                    attributes={"tool": tool_name},
                )
                spans = ctx.working_memory.setdefault("_trace_spans", {})
                spans[f"tool.{tool_name}"] = span
            except Exception:
                pass
        elif event == "tool.result":
            tool_name = payload.get("name", "unknown")
            spans = ctx.working_memory.get("_trace_spans", {})
            spans.pop(f"tool.{tool_name}", None)
        return payload
