"""P3a: HarnessRuntime — sole orchestrator.

Replaces the direct ``DirectLLMAdapter()`` call in ``chat.py``. The
runtime owns the full pipeline:
1. Resolve agent definition (AgentRegistry)
2. Build per-run HarnessContext
3. PromptAssembler.assemble() (P1)
4. Pre-hooks: trigger("run.start") (P1)
5. Pre-flight guardrails
6. Adapter.run() with RetryPolicy + CircuitBreaker (P1)
7. Intercept tool_call events → ctx.tool_engine.execute()
   + hooks: trigger("tool.call") / trigger("tool.result") (P1)
8. Post-flight guardrails
9. Post-hooks: trigger("run.end") (P1)
10. Checkpoint commit (P1)

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
from .checkpoint import CheckpointScope
from .registry import HarnessRegistry
from .retry import CircuitBreaker, CircuitOpenError, RetryPolicy
from .tool_engine import ToolEngine, ToolError

logger = logging.getLogger(__name__)


class HarnessRuntime(AgentRuntime):
    """Sole orchestrator: registry → context → pipeline → adapter → tools."""

    def __init__(self, registry: HarnessRegistry) -> None:
        self._registry = registry
        self._adapters: dict[str, RunAdapter] = {}
        self._retry_policy = RetryPolicy()
        self._circuit_breaker = CircuitBreaker()

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

        # 2. PromptAssembler.assemble() — P1
        if ctx.prompt_assembler is not None:
            try:
                await ctx.prompt_assembler.assemble(agent, ctx)
            except Exception as exc:
                logger.warning("PromptAssembler error: %s", exc)

        # 3. Pre-hooks: trigger("run.start")
        if ctx.hooks is not None:
            await ctx.hooks.trigger("run.start", {"messages": messages}, ctx)

        # 4. Pre-flight guardrails
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

        # 5. Adapter execution (with RetryPolicy + CircuitBreaker — P1)
        adapter = self._resolve_adapter(agent.adapter, config)
        try:
            async for event in self._run_with_retry(adapter, safe_messages, ctx):
                if event.type == "tool_call":
                    # 6. Intercept tool_call → harness-managed execution
                    tool_name = event.data.get("name", "")
                    tool_args = event.data.get("args", {}) or {}

                    # Pre-tool hooks
                    if ctx.hooks is not None:
                        await ctx.hooks.trigger(
                            "tool.call",
                            {"name": tool_name, "args": tool_args},
                            ctx,
                        )

                    try:
                        result = await ctx.tool_engine.execute(
                            tool_name, tool_args, ctx
                        )
                        tool_result_data = {
                            "name": tool_name,
                            "output": result.output,
                            "error": result.error,
                            "metadata": result.metadata,
                        }
                        yield StreamEvent(
                            type="tool_result", data=tool_result_data
                        )

                        # Post-tool hooks
                        if ctx.hooks is not None:
                            await ctx.hooks.trigger(
                                "tool.result", tool_result_data, ctx
                            )

                        # Mid-run checkpoint save
                        if ctx.checkpoint is not None:
                            await ctx.checkpoint.save(
                                messages=safe_messages,
                                tool_state=ctx.working_memory,
                            )

                    except ToolError as exc:
                        err_data = {
                            "name": tool_name,
                            "output": "",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                        yield StreamEvent(type="tool_result", data=err_data)
                        if ctx.hooks is not None:
                            await ctx.hooks.trigger("tool.result", err_data, ctx)
                else:
                    yield event
        finally:
            # 7. Post-hooks + checkpoint commit
            if ctx.hooks is not None:
                await ctx.hooks.trigger("run.end", {}, ctx)
            if ctx.checkpoint is not None:
                try:
                    await ctx.checkpoint.commit()
                except Exception as exc:
                    logger.warning("Checkpoint commit failed: %s", exc)

    # ── Internals ──
    async def _resolve_agent(
        self, config: RuntimeConfig
    ) -> AgentDefinition | None:
        """Resolve agent by id (config.agent) or name."""
        if not config.agent:
            return self._default_agent(config)

        async with async_session() as db:
            agent = await self._registry.agents.get(db, config.agent)
            if agent is not None:
                return agent
            agent = await self._registry.agents.get_by_name(
                db, config.workspace_id, config.agent
            )
            if agent is not None:
                return agent
        return self._default_agent(config)

    def _default_agent(self, config: RuntimeConfig) -> AgentDefinition:
        """Build a synthetic default agent from RuntimeConfig fields."""
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
        """Build a per-run HarnessContext with all P1 subsystems wired."""
        from .tools import BUILTIN_HANDLERS

        tool_engine = ToolEngine(
            registry=self._registry.tools,
            allowed_tools=agent.tools,
            builtin_handlers=BUILTIN_HANDLERS,
            mcp_manager=self._registry.mcp,
            sandbox=self._registry.sandbox,
        )

        # Build per-run CheckpointScope
        checkpoint: CheckpointScope | None = None
        if self._registry.checkpoints is not None and session_id:
            checkpoint = CheckpointScope(
                store=self._registry.checkpoints,
                session_id=session_id,
                agent_id=agent.id,
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
            # P1 wiring
            sandbox=self._registry.sandbox,
            hooks=self._registry.hooks,
            checkpoint=checkpoint,
            prompt_assembler=self._registry.prompt_assembler,
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
    ) -> AsyncIterator[StreamEvent]:
        """Run adapter with RetryPolicy + CircuitBreaker (P1).

        Replaces the P0 hardcoded retry loop with configurable
        ``RetryPolicy`` (exponential backoff + jitter) and
        ``CircuitBreaker`` (closed/open/half-open state machine).
        """
        attempt = 0
        while True:
            # Circuit breaker check
            if not self._circuit_breaker.can_execute():
                yield StreamEvent(
                    type="error",
                    data={
                        "code": "CIRCUIT_OPEN",
                        "message": "Circuit breaker is open; adapter unavailable",
                    },
                )
                return

            try:
                async for event in adapter.run(messages, ctx):
                    yield event
                self._circuit_breaker.record_success()
                return
            except Exception as exc:
                self._circuit_breaker.record_failure()

                if not self._retry_policy.is_retryable(exc):
                    # Non-retryable; surface immediately.
                    yield StreamEvent(
                        type="error",
                        data={
                            "code": "ADAPTER_ERROR",
                            "message": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    return

                attempt += 1
                if attempt > self._retry_policy.max_retries:
                    yield StreamEvent(
                        type="error",
                        data={
                            "code": "RETRY_EXHAUSTED",
                            "message": f"{type(exc).__name__}: {exc}",
                            "attempts": attempt,
                        },
                    )
                    return

                delay = self._retry_policy.backoff(attempt)
                logger.warning(
                    "Adapter retry %d/%d after %.1fs: %s",
                    attempt,
                    self._retry_policy.max_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)


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
