"""P3a: HarnessRuntime — sole orchestrator.

Owns the full agent run pipeline (Wave 2.5: deepagents is the sole
adapter; DirectLLM was removed):
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

from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.engine import async_session
from src.runtime.abc import AgentRuntime
from src.runtime.adapters.base import RunAdapter
from src.runtime.models import RuntimeConfig, StreamEvent

from .agents import AgentDefinition, AgentNotFoundError
from .context import HarnessContext
from .checkpoint import CheckpointStore
from .memory import MemoryScope
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

        # 1. Build per-run context. First materialize the tools exposed by the
        # MCP servers this agent is explicitly bound to (server-level granularity
        # + union with ``agent.tools``).
        mcp_tool_names = await self._resolve_mcp_tools(agent, config.workspace_id)

        # 1. Build per-run context
        ctx = self._build_context(
            agent=agent,
            config=config,
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id,
            workspace_settings=workspace_settings or {},
            workspace_root=workspace_root,
            extra_allowed=mcp_tool_names,
        )

        # 2. Track last user message for memory recall (P2)
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content", "")
                break
        if last_user_msg:
            ctx.working_memory["last_user_message"] = last_user_msg

        # 3. PromptAssembler.assemble() — P2 (persona + skills + tools + memory + policy)
        if ctx.prompt_assembler is not None:
            try:
                await ctx.prompt_assembler.assemble(agent, ctx)
            except Exception as exc:
                logger.warning("PromptAssembler error: %s", exc)

        # 4. Pre-hooks: trigger("run.start")
        if ctx.hooks is not None:
            await ctx.hooks.trigger("run.start", {"messages": messages}, ctx)

        # 5. Pre-flight guardrails
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

        # 6. Adapter execution (with RetryPolicy + CircuitBreaker — P1)
        adapter = self._resolve_adapter(agent.adapter, config)
        try:
            async for event in self._run_with_retry(adapter, safe_messages, ctx):
                if event.type == "tool_call":
                    # 7. Intercept tool_call → harness-managed execution
                    tool_name = event.data.get("name", "")
                    tool_args = event.data.get("args", {}) or {}

                    # Pre-tool hooks
                    if ctx.hooks is not None:
                        await ctx.hooks.trigger(
                            "tool.call",
                            {"name": tool_name, "args": tool_args},
                            ctx,
                        )

                    if event.already_executed:
                        # Phase 4: DeepAgentsAdapter already executed the
                        # tool via LangChainToolShim → ToolEngine. We skip
                        # re-execution but still fire hooks for telemetry
                        # parity (spec §3.2). The adapter will yield a
                        # separate tool_result event from on_tool_end.
                        yield event
                    else:
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

                        except ToolError as exc:
                            err_data = {
                                "name": tool_name,
                                "output": "",
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                            yield StreamEvent(type="tool_result", data=err_data)
                            if ctx.hooks is not None:
                                await ctx.hooks.trigger("tool.result", err_data, ctx)
                elif event.type == "tool_result" and event.already_executed:
                    # Phase 4: tool_result from DeepAgentsAdapter's on_tool_end.
                    # The tool was already executed by the shim; just fire
                    # post-tool hooks and yield the event.
                    if ctx.hooks is not None:
                        await ctx.hooks.trigger("tool.result", event.data, ctx)
                    yield event
                else:
                    yield event
        finally:
            # 8. Post-hooks
            if ctx.hooks is not None:
                await ctx.hooks.trigger("run.end", {}, ctx)

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
                return await self._resolve_subagent_refs(agent, db)
            agent = await self._registry.agents.get_by_name(
                db, config.workspace_id, config.agent
            )
            if agent is not None:
                return await self._resolve_subagent_refs(agent, db)
        return self._default_agent(config)

    async def _resolve_subagent_refs(
        self, agent: "AgentDefinition", db: "AsyncSession"
    ) -> "AgentDefinition":
        """Expand ``agent.subagents`` references into full ``SubagentSpec``s.

        A subagent may be stored as a *reference* (``agent_id`` pointing at
        another agent in the same workspace). At run time we resolve each
        reference to a full spec built from the referenced agent's own
        config, so the subagent inherits the referenced agent's
        system_prompt / tools / model / skills. Inline specs pass through
        unchanged.

        Missing or self-referential references are dropped (best-effort) so
        a misconfigured subagent never crashes the parent run.
        """
        from .agents import SubagentSpec

        if not agent.subagents:
            return agent

        resolved: list[SubagentSpec] = []
        for spec in agent.subagents:
            if not spec.agent_id:
                resolved.append(spec)
                continue
            if spec.agent_id == agent.id:
                logger.warning(
                    "Subagent reference to self (%s) ignored for agent %s",
                    spec.agent_id,
                    agent.id,
                )
                continue
            ref = await self._registry.agents.get(db, spec.agent_id)
            if ref is None:
                logger.warning(
                    "Subagent reference %s not found; skipping",
                    spec.agent_id,
                )
                continue
            resolved.append(
                SubagentSpec(
                    name=ref.name,
                    description=f"Subagent: {ref.name}",
                    system_prompt=ref.system_prompt,
                    tools=ref.tools,
                    model=ref.model or None,
                    skills=ref.skills,
                )
            )
        return agent.model_copy(update={"subagents": resolved})

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
            adapter="deepagents",
            metadata={},
        )

    async def _resolve_mcp_tools(
        self, agent: "AgentDefinition", workspace_id: str
    ) -> set[str]:
        """Materialize the agent's bound MCP servers' tools into the platform
        ``ToolRegistry`` and return their names.

        The agent's ``mcp_servers`` whitelist grants access to *every* tool
        exposed by each selected server (server-level granularity). Tool
        registration is idempotent — keyed by ``(workspace_id, tool_name)`` in
        the ``ToolRegistry`` — so re-running across requests is safe. Discovery
        failures are logged and skipped; they never abort the run.

        The returned names are merged into the agent's tool allowlist by the
        caller (union with ``agent.tools``).
        """
        from .tool_engine import ToolDefinition

        extra: set[str] = set()
        names = getattr(agent, "mcp_servers", None) or []
        if not names:
            logger.info(
                "Agent %s (%s): no MCP servers bound — skip tool discovery",
                agent.id or agent.name, agent.adapter,
            )
            return extra
        mcp = self._registry.mcp
        if mcp is None:
            logger.warning("MCP manager not initialized — cannot discover tools")
            return extra
        for server_name in names:
            try:
                tools = await mcp.list_tools(server_name, workspace_id)
            except Exception as exc:
                # Discovery failures are fatal to tool availability but were
                # previously swallowed as a ``warning`` (so the run still
                # returned HTTP 200 and the agent silently lost the tools).
                # Promote to ERROR and emit a concrete misconfiguration hint
                # so the operator can see *why* an MCP-bound agent has no
                # tools. A common case: endpoint ends in ``/sse`` but the
                # server was registered with the default ``transport="http"``
                # (Streamable HTTP), which cannot speak the SSE handshake.
                cfg = mcp.get_server(server_name, workspace_id)
                transport = cfg.transport if cfg else "?"
                endpoint = cfg.endpoint if cfg else ""
                hint = ""
                if (
                    transport == "http"
                    and isinstance(endpoint, str)
                    and endpoint.rstrip("/").endswith("/sse")
                ):
                    hint = (
                        " The endpoint ends with '/sse' but transport is "
                        "'http'. This server speaks the MCP SSE protocol — "
                        "register it with transport='sse'."
                    )
                logger.error(
                    "Agent %s: MCP tool discovery FAILED for server %r "
                    "(transport=%s, endpoint=%s): %s: %s.%s",
                    agent.id or agent.name,
                    server_name,
                    transport,
                    endpoint,
                    type(exc).__name__,
                    exc,
                    hint,
                )
                continue
            for t in tools:
                self._registry.tools.register(
                    ToolDefinition(
                        name=t["name"],
                        description=t.get("description", ""),
                        input_schema=t.get("input_schema", {}),
                        source="mcp",
                        mcp_server=server_name,
                        workspace_id=workspace_id,
                    )
                )
                extra.add(t["name"])
            logger.info(
                "Agent %s: discovered %d tools from MCP server %r",
                agent.id or agent.name,
                len(tools),
                server_name,
            )
        return extra

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
        extra_allowed: set[str] | None = None,
    ) -> HarnessContext:
        """Build a per-run HarnessContext with all P1/P2 subsystems wired."""
        from .tools import BUILTIN_HANDLERS

        # Allowlist = agent.tools (builtin/custom) ∪ tools of bound MCP servers.
        allowed = set(agent.tools)
        if extra_allowed:
            allowed |= set(extra_allowed)

        # P2: if long-term memory is enabled, the agent must be able to call
        # the memory tools — otherwise enabling the feature is a no-op for
        # storage (the user toggles "Enable Long-Term Memory" in the UI but the
        # agent never sees save_memory/recall_memory in its toolset). Gate on
        # the same condition used to build MemoryScope so tool availability
        # stays consistent with whether ctx.memory is actually wired.
        if (
            self._registry.memory is not None
            and agent.memory
            and agent.memory.enable_long_term
        ):
            allowed.add("save_memory")
            allowed.add("recall_memory")

        tool_engine = ToolEngine(
            registry=self._registry.tools,
            allowed_tools=list(allowed),
            builtin_handlers=BUILTIN_HANDLERS,
            mcp_manager=self._registry.mcp,
            sandbox=self._registry.sandbox,
        )

        # Wire checkpoint store directly (deepagents adapter consumes
        # ctx.checkpoint as a CheckpointStore; CheckpointScope removed in
        # Wave 2.5 along with DirectLLM).
        checkpoint: CheckpointStore | None = None
        if self._registry.checkpoints is not None and session_id:
            checkpoint = self._registry.checkpoints

        # P2: Build per-run MemoryScope (only if agent has memory enabled)
        memory_scope: MemoryScope | None = None
        if self._registry.memory is not None and agent.memory and agent.memory.enable_long_term:
            memory_scope = MemoryScope(
                store=self._registry.memory,
                session_id=session_id,
                user_id=user_id,
                workspace_id=config.workspace_id,
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
            # P2 wiring
            memory=memory_scope,
            skills=self._registry.skills,
        )

    def _resolve_adapter(
        self, adapter_name: str, config: RuntimeConfig
    ) -> RunAdapter:
        """Resolve adapter by name.

        Wave 2.5: DirectLLM removed; ``deepagents`` is the only adapter.
        Unknown/legacy adapter names (``direct_llm`` / ``adk`` /
        ``langgraph``) fall back to ``deepagents`` with a loud warning
        — keeps existing behavior for old DB rows until the one-shot
        migration (D2) rewrites them to ``deepagents``.
        """
        if adapter_name in self._adapters:
            return self._adapters[adapter_name]

        if adapter_name == "deepagents":
            from src.runtime.adapters.deepagents import DeepAgentsAdapter
            adapter = DeepAgentsAdapter(model=config.model)
        else:
            logger.warning(
                "Adapter %r not implemented; falling back to deepagents",
                adapter_name,
            )
            from src.runtime.adapters.deepagents import DeepAgentsAdapter
            adapter = DeepAgentsAdapter(model=config.model)

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
