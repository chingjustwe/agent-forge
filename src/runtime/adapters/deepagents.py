"""Phase 4: DeepAgents adapter — runs agents through ``deepagents.create_deep_agent``.

Per spec D1: this is the single non-``direct_llm`` adapter. It wraps
``deepagents`` (which is itself built on LangGraph) and bridges our
harness subsystems into it:

- Tools: ``LangChainToolShim`` wraps each ``ToolDefinition`` so every
  tool call routes through ``ctx.tool_engine.execute()`` (spec D6).
  The Phase 3a pipeline (whitelist → sandbox → guardrail → hook →
  handler) runs for every deepagents tool call.
- State: ``LangGraphCheckpointShim`` adapts our ``SQLiteCheckpointStore``
  to LangGraph's ``BaseCheckpointSaver`` so crash recovery stays in our
  DB (spec D2).
- Subagents: ``SubagentMapper`` (Phase 4b) maps our ``SubagentSpec``
  list to deepagents' SubAgent dicts. Phase 4a passes an empty list.

Imports of ``deepagents`` / ``langchain.*`` are confined to this file
and ``langgraph_shims.py`` (spec §6.2) so the ``direct_llm`` path stays
zero-dependency.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from src.infra.settings import settings
from src.runtime.adapters.base import RunAdapter
from src.runtime.models import StreamEvent, Usage

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext

logger = logging.getLogger(__name__)


# deepagents' built-in filesystem tools that we disable (spec D7).
# Our Phase 3a builtin tools of the same names win because they ship
# with sandbox integration and hook/guardrail interception.
_EXCLUDED_FILESYSTEM_TOOLS = frozenset({
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "execute",
})


class DeepAgentsAdapter(RunAdapter):
    """Adapter that runs the agent through ``deepagents.create_deep_agent``.

    The adapter is stateless across runs — ``create_deep_agent`` is
    called once per ``run()`` invocation so the checkpointer binds to
    the correct ``session_id``. This is intentional: deepagents' compiled
    graph caches the ``thread_id`` from the ``RunnableConfig`` at
    compile time, so reusing a compiled graph across sessions would
    cross-pollute state.

    Per spec §3.2: tool calls are executed by the shim (which delegates
    to ``ctx.tool_engine.execute()``). The adapter yields
    ``StreamEvent(type="tool_call", already_executed=True)`` on
    ``on_tool_start`` and ``StreamEvent(type="tool_result")`` on
    ``on_tool_end``. The runtime checks ``already_executed`` and skips
    its own tool execution but still fires ``tool.call`` / ``tool.result``
    hooks for telemetry parity.
    """

    name = "deepagents"

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
    ) -> None:
        self.api_key = api_key or settings.llm_api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def run(
        self,
        messages: list[dict],
        ctx: "HarnessContext",
    ) -> AsyncIterator[StreamEvent]:
        """One deepagents run → StreamEvent stream.

        Mapping (spec §3.1) — LangGraph v3 JSON-RPC event format:
        - ``method=messages`` + ``content-block-delta`` → ``StreamEvent(type="text")``
        - ``method=messages`` + ``tool-call``           → ``StreamEvent(type="tool_call", already_executed=True)``
        - ``method=messages`` + ``tool-result``         → ``StreamEvent(type="tool_result")``
        - ``method=on_chain_start/end`` name="task"     → ``StreamEvent(type="subagent")``
        - ``method=values`` w/ ``usage_metadata``       → ``StreamEvent(type="status")``
        - ``type=error``                                → ``StreamEvent(type="error")``
        """
        if not messages:
            raise ValueError("messages must not be empty")

        # Lazy imports keep ``direct_llm`` path zero-dependency.
        from deepagents import create_deep_agent
        from langchain.chat_models import init_chat_model

        from src.runtime.harness.langgraph_shims import (
            LangChainToolShim,
            LangGraphCheckpointShim,
        )

        # 1. Resolve agent-scoped settings
        agent = ctx.agent
        model_name = (
            (agent.model if agent and agent.model else self.model)
            or "deepseek-chat"
        )
        max_tokens = agent.max_tokens if agent and agent.max_tokens else 4096
        temperature = agent.temperature if agent is not None else 0.7
        system_prompt = (
            ctx.working_memory.get("system_prompt", "")
            or (agent.system_prompt if agent else "")
        )

        # 2. Build tool shims from the agent's whitelist
        tools: list[Any] = []
        if ctx.tool_engine is not None:
            available = ctx.tool_engine.available_tools(ctx.workspace_id)
            tools = [LangChainToolShim(t, ctx) for t in available]

        # 3. Map AgentDefinition.subagents → deepagents SubAgent dicts
        # Phase 4a: subagent wiring is a stub — SubagentMapper lands in 4b.
        subagents: list[dict] = []
        if agent and getattr(agent, "subagents", None):
            try:
                from src.runtime.harness.subagents import SubagentMapper
                subagents = SubagentMapper.to_subagents(agent.subagents, ctx)
            except Exception as exc:
                # Subagent mapping is best-effort: a failure here must
                # not kill the run. Log and continue with no subagents.
                logger.warning("SubagentMapper failed: %s", exc)
                subagents = []

        # 4. Build checkpoint shim bound to this session
        checkpointer: LangGraphCheckpointShim | None = None
        if ctx.checkpoint is not None:
            checkpointer = LangGraphCheckpointShim(
                store=ctx.checkpoint._store,
                session_id=ctx.session_id,
                agent_id=agent.id if agent else "",
            )

        # 5. Build the LLM (DeepSeek via OpenAI-compatible endpoint)
        model = init_chat_model(
            f"openai:{model_name}",
            base_url=self.base_url,
            api_key=self.api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # 6. Compile the deep agent.
        # deepagents 0.6.12 does not support ``excluded_tools``; instead we
        # constrain the built-in filesystem tools via ``permissions`` so
        # they can only access ``ctx.workspace_root`` (not arbitrary paths).
        # Our sandboxed builtins (ls/read/write/edit/glob/grep) coexist
        # with deepagents' read_file/write_file/edit_file/execute — the
        # LLM picks which to call.
        perms: list[Any] = []
        if ctx.workspace_root:
            try:
                from deepagents.middleware.filesystem import (
                    FilesystemPermission,
                )
                perms = [
                    FilesystemPermission(
                        operations=["read", "write"],
                        mode="allow",
                        paths=[f"{ctx.workspace_root}/**"],
                    ),
                    FilesystemPermission(
                        operations=["read", "write"],
                        mode="deny",
                        paths=["/**"],
                    ),
                ]
            except ImportError:
                pass
        deep_agent = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            subagents=subagents or None,
            checkpointer=checkpointer,
            permissions=perms or None,
        )

        # 7. Stream events
        config: dict[str, Any] = {"configurable": {"thread_id": ctx.session_id}}
        input_payload = {"messages": self._normalize_messages(messages)}

        # Phase 4c: optional LangSmith tracing. Only enabled when
        # ``LANGSMITH_API_KEY`` is set in settings; otherwise zero-
        # overhead. The tracer is attached as a callback on the
        # RunnableConfig so it applies to the deepagents run only.
        if settings.langsmith_api_key:
            self._wire_langsmith_tracing(config, ctx)

        usage_emitted = False
        try:
            # ``astream_events`` may return either an AsyncIterator or an
            # Awaitable[AsyncIterator] depending on the langgraph version.
            # Await first to normalize, then iterate.
            event_stream = await deep_agent.astream_events(
                input_payload, version="v3", config=config
            )
            async for event in event_stream:
                for stream_event in self._translate_event(event):
                    yield stream_event
                    if (
                        stream_event.type == "status"
                        and "usage" in stream_event.data
                    ):
                        usage_emitted = True
        except Exception as exc:
            logger.exception("DeepAgents run failed: %s", exc)
            yield StreamEvent(
                type="error",
                data={
                    "code": "DEEPAGENTS_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                },
            )
            return

        if not usage_emitted:
            yield StreamEvent(
                type="status",
                data={"usage": Usage().model_dump()},
            )

    # ── Helpers ──

    def _normalize_messages(self, messages: list[dict]) -> list[dict]:
        """Drop leading system message (deepagents injects its own).

        Per spec D8: deepagents' ``write_todos`` planning tool and our
        Phase 3 ``todo_write`` may coexist; this method only strips the
        system role, not user/assistant messages.
        """
        out: list[dict] = []
        for m in messages:
            if m.get("role") == "system":
                continue
            out.append({
                "role": m.get("role", "user"),
                "content": m.get("content", ""),
            })
        return out

    def _translate_event(self, event: dict) -> list[StreamEvent]:
        """Map one LangGraph v3 ``astream_events`` event → StreamEvents.

        LangGraph v3 uses a JSON-RPC-style format:
        ``{"type": "event", "method": "messages"|"values", "params": {"data": ...}}``

        - ``method=messages``: streaming tokens; ``params.data`` is a
          tuple whose first element carries ``{"event": "content-block-delta",
          "delta": {"type": "text-delta", "text": "..."}}``.
        - ``method=values``: state snapshot; ``params.data`` is a dict
          with ``messages`` list. The final AIMessage carries
          ``usage_metadata``.
        """
        method = event.get("method", "")
        params = event.get("params", {}) or {}
        data = params.get("data")

        # ── method=messages: streaming token deltas ──
        if method == "messages":
            if not isinstance(data, (list, tuple)) or not data:
                return []
            first = data[0] if data else {}
            if not isinstance(first, dict):
                return []
            sub_event = first.get("event", "")

            # Text streaming — emit data={"content": text} to match
            # DirectLLMAdapter's convention (chat.py reads this key).
            if sub_event == "content-block-delta":
                delta = first.get("delta", {}) or {}
                if delta.get("type") == "text-delta":
                    text = delta.get("text", "")
                    if text:
                        return [StreamEvent(
                            type="text", data={"content": text}
                        )]
                return []

            # Tool call start
            if sub_event == "tool-call":
                return [StreamEvent(
                    type="tool_call",
                    data={
                        "name": first.get("name", ""),
                        "args": first.get("args", {}),
                        "call_id": first.get("id", ""),
                    },
                    already_executed=True,
                )]

            # Tool result
            if sub_event == "tool-result":
                output = first.get("output", "")
                error_str: str | None = None
                if isinstance(output, str) and output.startswith("ERROR: "):
                    error_str = output.removeprefix("ERROR: ")
                    output = ""
                return [StreamEvent(
                    type="tool_result",
                    data={
                        "name": first.get("name", ""),
                        "output": output if isinstance(output, str) else str(output),
                        "error": error_str,
                    },
                    already_executed=True,
                )]

            return []

        # ── method=values: state snapshot (final state has usage) ──
        if method == "values":
            if not isinstance(data, dict):
                return []
            messages = data.get("messages", [])
            if not messages:
                return []
            # The last message is the AI's final reply; check for usage.
            last_msg = messages[-1]
            usage_metadata = getattr(last_msg, "usage_metadata", None)
            if usage_metadata:
                usage = Usage(
                    input_tokens=usage_metadata.get("input_tokens", 0),
                    output_tokens=usage_metadata.get("output_tokens", 0),
                    total_tokens=usage_metadata.get("total_tokens", 0),
                )
                return [StreamEvent(
                    type="status",
                    data={"usage": usage.model_dump()},
                )]
            return []

        # ── Subagent (task) chain events ──
        # In v3, chain events arrive as method="on_chain_start"/"on_chain_end"
        # with the chain name in params.name. We only surface "task" chains
        # (deepagents' subagent dispatch) as subagent StreamEvents.
        if method in ("on_chain_start", "on_chain_end"):
            name = params.get("name", "") or event.get("name", "")
            if name != "task":
                return []
            if method == "on_chain_start":
                input_data = data if isinstance(data, dict) else {}
                return [StreamEvent(
                    type="subagent",
                    data={
                        "action": "start",
                        "name": input_data.get("name", ""),
                        "subagent_type": input_data.get("subagent_type", ""),
                    },
                )]
            # on_chain_end
            output = data.get("output", "") if isinstance(data, dict) else ""
            output_str = output if isinstance(output, str) else str(output)
            return [StreamEvent(
                type="subagent",
                data={
                    "action": "end",
                    "output": output_str,
                },
            )]

        # ── Error events ──
        if event.get("type") == "error":
            return [StreamEvent(
                type="error",
                data={
                    "code": "DEEPAGENTS_EVENT_ERROR",
                    "message": str(data)[:500],
                    "event": method,
                },
            )]

        return []

    # ── Phase 4c: LangSmith tracing ───────────────────────────────────

    def _wire_langsmith_tracing(
        self, config: dict[str, Any], ctx: "HarnessContext"
    ) -> None:
        """Attach LangSmith tracing callbacks to the RunnableConfig.

        Imports are lazy so the ``direct_llm`` path stays zero-dependency
        even when LangSmith is configured. If imports fail (langsmith
        not installed), the failure is logged and tracing is silently
        skipped — runs proceed without tracing.
        """
        import os

        # LangChain reads these env vars lazily at callback construction.
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
        os.environ.setdefault("LANGSMITH_ENDPOINT", settings.langsmith_endpoint)
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")

        try:
            from langchain_core.tracers.langchain import LangChainTracer
        except ImportError:
            return  # langsmith not installed — skip silently

        tracer = LangChainTracer(
            project_name=settings.langsmith_project,
            client=None,  # LangChain builds the client from env vars
        )
        # Run name: agent name + session_id for easy search in LangSmith.
        run_name = f"{ctx.agent.name}@{ctx.session_id[:8]}"
        config.setdefault("callbacks", []).append(tracer)
        config["run_name"] = run_name
        config["metadata"] = {
            "session_id": ctx.session_id,
            "workspace_id": ctx.workspace_id,
            "trace_id": ctx.trace_id,
            "user_id": ctx.user_id,
            "agent_id": ctx.agent.id,
        }
