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

        Mapping (spec §3.1):
        - ``on_chat_model_stream`` delta  → ``StreamEvent(type="text")``
        - ``on_tool_start``               → ``StreamEvent(type="tool_call", already_executed=True)``
        - ``on_tool_end``                 → ``StreamEvent(type="tool_result")``
        - ``on_chain_start/end`` name="task" → ``StreamEvent(type="subagent")``
        - ``on_chat_model_end`` w/ usage  → ``StreamEvent(type="status")``
        - error event                     → ``StreamEvent(type="error")``
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

        # 6. Compile the deep agent
        deep_agent = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            subagents=subagents or None,
            checkpointer=checkpointer,
            excluded_tools=set(_EXCLUDED_FILESYSTEM_TOOLS),
        )

        # 7. Stream events
        config: dict[str, Any] = {"configurable": {"thread_id": ctx.session_id}}
        input_payload = {"messages": self._normalize_messages(messages)}

        usage_emitted = False
        try:
            async for event in deep_agent.astream_events(
                input_payload, version="v3", config=config
            ):
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
        """Map one deepagents ``astream_events`` v3 event → StreamEvents.

        Returns a list because some raw events yield multiple
        StreamEvents (rare; kept for future extension).
        """
        event_name = event.get("event", "")
        name = event.get("name", "")
        data = event.get("data", {}) or {}

        # Token stream
        if event_name == "on_chat_model_stream":
            chunk = data.get("chunk")
            content = ""
            if chunk is not None:
                # LangChain AIMessageChunk — content is on .content attr.
                content = getattr(chunk, "content", "") or ""
            if not content:
                return []
            return [StreamEvent(type="text", data={"content": content})]

        # Tool call start — the shim already executed the tool by the
        # time on_tool_end fires, so we mark already_executed=True.
        if event_name == "on_tool_start":
            tool_input = data.get("input", {}) or {}
            return [StreamEvent(
                type="tool_call",
                data={
                    "name": name,
                    "args": tool_input,
                    "call_id": event.get("run_id", ""),
                },
                already_executed=True,
            )]

        # Tool call end — yield the result the shim returned.
        if event_name == "on_tool_end":
            output = data.get("output")
            output_str = ""
            error_str: str | None = None
            if isinstance(output, str):
                output_str = output
                if output.startswith("ERROR: "):
                    error_str = output.removeprefix("ERROR: ")
                    output_str = ""
            elif output is not None:
                output_str = getattr(output, "content", str(output))
            return [StreamEvent(
                type="tool_result",
                data={
                    "name": name,
                    "output": output_str,
                    "error": error_str,
                },
                already_executed=True,
            )]

        # Subagent dispatch (the `task` tool)
        if event_name == "on_chain_start" and name == "task":
            inputs = data.get("input", {}) or {}
            return [StreamEvent(
                type="subagent",
                data={
                    "action": "start",
                    "name": inputs.get("name", ""),
                    "subagent_type": inputs.get("subagent_type", ""),
                },
            )]
        if event_name == "on_chain_end" and name == "task":
            output = data.get("output")
            output_str = ""
            if output is not None:
                output_str = getattr(output, "content", str(output))
            return [StreamEvent(
                type="subagent",
                data={
                    "action": "end",
                    "name": name,
                    "output": output_str,
                },
            )]

        # Usage tracking
        if event_name == "on_chat_model_end":
            output = data.get("output")
            usage_metadata = (
                getattr(output, "usage_metadata", None) if output else None
            )
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

        # Errors surfaced by LangGraph
        if "error" in event_name.lower():
            return [StreamEvent(
                type="error",
                data={
                    "code": "DEEPAGENTS_EVENT_ERROR",
                    "message": str(data)[:500],
                    "event": event_name,
                },
            )]

        return []
