"""P3a: HarnessRuntime — sole orchestrator.

Replaces the direct ``DirectLLMAdapter()`` call in ``chat.py``. The
runtime owns the full pipeline:
1. Resolve agent definition (AgentRegistry)
2. Build per-run HarnessContext
3. Pre-flight guardrails
4. Adapter.run() (with RetryPolicy + CircuitBreaker — P1)
5. Intercept tool_call events → ctx.tool_engine.execute()
6. Post-flight guardrails
7. Final state / checkpoint commit (P1)

P0 implements steps 1-5 with a minimal retry wrapper. P1 adds
CircuitBreaker, Hooks, CheckpointStore commit, and full message-history
management for the compact tool.

``chat.py`` calls ``HarnessRuntime.run()`` exactly once per request.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from src.infra.db.engine import async_session
from src.runtime.abc import AgentRuntime
from src.runtime.adapters.base import RunAdapter
from src.runtime.adapters.direct_llm import DirectLLMAdapter
from src.runtime.models import RuntimeConfig, StreamEvent

from .agents import AgentDefinition, AgentNotFoundError
from .context import HarnessContext
from .registry import HarnessRegistry
from .tool_engine import ToolEngine, ToolError

logger = logging.getLogger(__name__)


class HarnessRuntime(AgentRuntime):
    """Sole orchestrator: registry → context → guardrails → adapter → tools."""

    def __init__(self, registry: HarnessRegistry) -> None:
        self._registry = registry
        self._adapters: dict[str, RunAdapter] = {}

    # ── Public API ──
    async def run(
        self,
        session_id: str,
        messages: list[dict],
        config: RuntimeConfig,
        *,
        user_id: str = "",
        trace_id: str | None = None,
        workspace_settings: dict | None = None,
        workspace_root: str = "",
    ) -> AsyncIterator[StreamEvent]:
        """Execute one agent run end-to-end.

        Yields ``StreamEvent``s for the SSE stream. The caller
        (``chat.py``) is responsible for persisting user/assistant
        messages — the runtime focuses on orchestration + tool exec.
        """
        trace_id = trace_id or uuid.uuid4().hex

        # 0. Resolve agent definition
        agent = await self._resolve_agent(config)
        if agent is None:
            yield StreamEvent(
                type="error",
                data={
                    "code": "AGENT_NOT_FOUND",
                    "message": f"Agent {config.agent!r} not found in workspace {config.workspace_id!r}",
                },
            )
            return

        # 1. Build per-run context
        ctx = self._build_context(
            agent=agent,
            config=config,
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id,
            workspace_settings=workspace_settings or {},
            workspace_root=workspace_root,
        )

        # 2. Pre-flight guardrails
        try:
            pre = await ctx.guardrails.check_input(messages, ctx)
        except Exception as exc:
            logger.exception("Pre-flight guardrail error: %s", exc)
            pre = None

        if pre is not None and pre.action == "block":
            yield StreamEvent(
                type="error",
                data={
                    "code": "GUARDRAIL_BLOCKED",
                    "message": pre.reason or "Input blocked by guardrail",
                    "guardrail": pre.guardrail_name,
                },
            )
            return

        safe_messages = (
            pre.modified_messages if pre and pre.modified_messages else messages
        )

        # 3. Adapter execution (with minimal retry — full policy in P1)
        adapter = self._resolve_adapter(agent.adapter, config)
        async for event in self._run_with_retry(adapter, safe_messages, ctx):
            if event.type == "tool_call":
                # 4. Intercept tool_call → harness-managed execution
                tool_name = event.data.get("name", "")
                tool_args = event.data.get("args", {}) or {}
                try:
                    result = await ctx.tool_engine.execute(
                        tool_name, tool_args, ctx
                    )
                    yield StreamEvent(
                        type="tool_result",
                        data={
                            "name": tool_name,
                            "output": result.output,
                            "error": result.error,
                            "metadata": result.metadata,
                        },
                    )
                except ToolError as exc:
                    yield StreamEvent(
                        type="tool_result",
                        data={
                            "name": tool_name,
                            "output": "",
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
            else:
                yield event

    # ── Internals ──
    async def _resolve_agent(
        self, config: RuntimeConfig
    ) -> AgentDefinition | None:
        """Resolve agent by id (config.agent) or name.

        P0: if ``config.agent`` is empty or not found, fall back to a
        synthetic default agent so existing chat flows that don't
        specify an agent id keep working. This keeps ``chat.py``'s
        current behavior (direct LLM, no tools) intact while enabling
        tool use for agents that opt in.
        """
        if not config.agent:
            return self._default_agent(config)

        async with async_session() as db:
            agent = await self._registry.agents.get(db, config.agent)
            if agent is not None:
                return agent
            # Try by name within the workspace.
            agent = await self._registry.agents.get_by_name(
                db, config.workspace_id, config.agent
            )
            if agent is not None:
                return agent
        return self._default_agent(config)

    def _default_agent(self, config: RuntimeConfig) -> AgentDefinition:
        """Build a synthetic default agent from RuntimeConfig fields.

        Used when no AgentDefinition is found — preserves Phase 1/2
        behavior (direct LLM call, no tools, no guardrails beyond the
        built-in pipeline).
        """
        return AgentDefinition(
            id="",
            name="default",
            workspace_id=config.workspace_id,
            system_prompt="",
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            tools=[],
            guardrails=[],
            skills=[],
            hooks=[],
            memory=None,
            adapter="direct_llm",
            metadata={},
        )

    def _build_context(
        self,
        *,
        agent: AgentDefinition,
        config: RuntimeConfig,
        session_id: str,
        user_id: str,
        trace_id: str,
        workspace_settings: dict,
        workspace_root: str,
    ) -> HarnessContext:
        """Build a per-run HarnessContext with tool_engine scoped to agent."""
        from .tools import BUILTIN_HANDLERS

        tool_engine = ToolEngine(
            registry=self._registry.tools,
            allowed_tools=agent.tools,
            builtin_handlers=BUILTIN_HANDLERS,
            mcp_manager=self._registry.mcp,
            sandbox=self._registry.sandbox,
        )

        return HarnessContext(
            workspace_id=config.workspace_id,
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            agent=agent,
            tool_engine=tool_engine,
            guardrails=self._registry.guardrails,
            collector=self._registry.collector,
            secrets={},
            workspace_settings=workspace_settings,
            workspace_root=workspace_root,
        )

    def _resolve_adapter(
        self, adapter_name: str, config: RuntimeConfig
    ) -> RunAdapter:
        """Resolve adapter by name. P0 only supports ``direct_llm``."""
        if adapter_name in self._adapters:
            return self._adapters[adapter_name]

        if adapter_name == "direct_llm":
            adapter = DirectLLMAdapter(model=config.model)
        else:
            # ADK / LangGraph arrive in Phase 4/7; fall back to direct_llm.
            logger.warning(
                "Adapter %r not implemented in P3a; falling back to direct_llm",
                adapter_name,
            )
            adapter = DirectLLMAdapter(model=config.model)

        self._adapters[adapter_name] = adapter
        return adapter

    async def _run_with_retry(
        self,
        adapter: RunAdapter,
        messages: list[dict],
        ctx: HarnessContext,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> AsyncIterator[StreamEvent]:
        """Run adapter with exponential backoff on retryable exceptions.

        P0 uses a fixed retry policy; P1 replaces this with the
        configurable ``RetryPolicy`` + ``CircuitBreaker`` classes.
        """
        attempt = 0
        while True:
            try:
                async for event in adapter.run(messages, ctx):
                    yield event
                return
            except (TimeoutError, ConnectionError) as exc:
                attempt += 1
                if attempt > max_retries:
                    yield StreamEvent(
                        type="error",
                        data={
                            "code": "RETRY_EXHAUSTED",
                            "message": f"{type(exc).__name__}: {exc}",
                            "attempts": attempt,
                        },
                    )
                    return
                delay = min(base_delay * (2 ** (attempt - 1)), 30.0)
                logger.warning(
                    "Adapter retry %d/%d after %.1fs: %s",
                    attempt,
                    max_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                # Non-retryable; surface immediately.
                yield StreamEvent(
                    type="error",
                    data={
                        "code": "ADAPTER_ERROR",
                        "message": f"{type(exc).__name__}: {exc}",
                    },
                )
                return


# Module-level singleton — set by main.py lifespan.
_runtime: HarnessRuntime | None = None


def get_runtime() -> HarnessRuntime:
    """Return the initialized HarnessRuntime singleton."""
    global _runtime
    if _runtime is None:
        from .registry import get_registry
        _runtime = HarnessRuntime(get_registry())
    return _runtime


def set_runtime(runtime: HarnessRuntime) -> None:
    """Inject a runtime (used by tests)."""
    global _runtime
    _runtime = runtime


def reset_runtime() -> None:
    """Reset the singleton. Used by tests for isolation."""
    global _runtime
    _runtime = None
