"""Phase 4: DeepAgents adapter — runs agents through ``deepagents.create_deep_agent``.

Wave 2.5: this is now the **sole** adapter (DirectLLM was removed).
It wraps ``deepagents`` (which is itself built on LangGraph) and
bridges our harness subsystems into it:

- Tools: ``LangChainToolShim`` wraps each ``ToolDefinition`` so every
  tool call routes through ``ctx.tool_engine.execute()`` (spec D6).
  The Phase 3a pipeline (whitelist → sandbox → guardrail → hook →
  handler) runs for every deepagents tool call.
- State: ``LangGraphCheckpointShim`` adapts our ``SQLiteCheckpointStore``
  to LangGraph's ``BaseCheckpointSaver`` so crash recovery stays in our
  DB (spec D2). ``ctx.checkpoint`` is the store directly (no scope
  wrapper).
- Subagents: ``SubagentMapper`` (Phase 4b) maps our ``SubagentSpec``
  list to deepagents' SubAgent dicts. Phase 4a passes an empty list.

Imports of ``deepagents`` / ``langchain.*`` are confined to this file
and ``langgraph_bridge.py`` (spec §6.2).
"""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from src.infra.settings import settings
from src.runtime.adapters.base import RunAdapter
from src.runtime.models import StreamEvent, Usage

from langchain.agents.middleware import AgentMiddleware

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext

logger = logging.getLogger(__name__)


# deepagents' built-in filesystem tools (spec D7). The platform ships
# sandboxed equivalents, so these are stripped via ``_ToolExclusionMiddleware``
# to make the agent's tool whitelist actually enforce (``create_deep_agent``'s
# ``tools=`` arg is additive — "it never removes a built-in").
_EXCLUDED_FILESYSTEM_TOOLS = frozenset({
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "execute",
})

# Subset of ``_EXCLUDED_FILESYSTEM_TOOLS`` whose names COLLIDE with platform
# builtin tools (ls/glob/grep). For these we do NOT pass a platform
# ``LangChainToolShim`` — deepagents' version (scoped to ``workspace_root``
# via ``FilesystemPermission``) handles the call — to avoid duplicate tool
# names in the LLM's tool list. They are excluded by the middleware only when
# NOT in the agent's whitelist; when whitelisted, deepagents' version is kept.
_COLLIDING_FILESYSTEM_TOOLS = frozenset({"ls", "glob", "grep"})

# deepagents' other auto-injected built-ins (not filesystem tools):
# - ``write_todos`` (TodoListMiddleware): always excluded — platform ships
#   ``todo_write`` with the same semantics.
# - ``task`` (SubAgentMiddleware): excluded UNLESS the agent has subagents
#   configured (it's the subagent-delegation mechanism, not a user-tool).
_EXCLUDED_TODO_TOOL = "write_todos"
_TASK_TOOL = "task"


# ── System-prompt stripper ───────────────────────────────────────────────
#
# ``FilesystemMiddleware`` and ``TodoListMiddleware`` inject FIXED system
# prompt sections that mention their tools by name (``read_file``,
# ``write_file``, ``edit_file``, ``ls``, ``write_todos``, etc.)
# regardless of whether those tools are in the agent's whitelist.
# ``_ToolExclusionMiddleware`` removes the tool SCHEMAS but cannot remove
# the prompt TEXT — the LLM still "sees" the tool names in the system
# prompt and reports them as available. This middleware runs AFTER those
# middleware (user middleware is inserted later in the stack) and strips
# the offending sections so the system prompt stays consistent with the
# actual tool list.

# Markers that identify a content block or text section as belonging to
# a specific middleware's system prompt.
_FS_MARKER = "## Filesystem Tools"
_TODO_MARKER = "## `write_todos`"
_EXECUTE_MARKER = "## Execute Tool `execute`"

# Regex patterns for stripping sections from string content. Each pattern
# matches from the section header to the next ``## `` header or end of text.
_SECTION_RE = re.compile(
    r'(?:^|\n)(## [^\n]+\n.*?)(?=\n## |\Z)',
    re.DOTALL,
)


class _SystemPromptStripperMiddleware(AgentMiddleware):
    """Strip deepagents middleware system prompts for excluded tools.

    Parameters:
        strip_fs: Strip the ``FilesystemMiddleware`` system prompt section.
        strip_todos: Strip the ``TodoListMiddleware`` system prompt section.
        strip_execute: Strip the ``execute`` tool system prompt section.
    """

    # Must inherit ``AgentMiddleware`` (not just duck-type it). langchain's
    # ``create_deep_agent`` factory collects middleware with a custom
    # ``wrap_tool_call``/``awrap_tool_call`` via
    # ``m.__class__.wrap_tool_call is not AgentMiddleware.wrap_tool_call``
    # (factory.py). A duck-typed class without those attributes makes the
    # ``m.__class__.wrap_tool_call`` lookup raise
    # ``AttributeError: type object '...' has no attribute 'wrap_tool_call'``.
    # Inheriting gives us the base no-op implementations, so the identity
    # check passes and our override of ``wrap_model_call`` still handles the
    # actual prompt-stripping.
    #
    # NOTE: do NOT set ``state_schema = None`` here. ``AgentMiddleware``
    # already provides a default (``_DefaultAgentState``); overriding it with
    # ``None`` makes the factory's ``m.state_schema for m in middleware``
    # yield ``None``, which is then passed to ``get_type_hints(None)`` and
    # raises ``TypeError: None does not have annotations``. Inherit the base
    # default instead.
    name = "_SystemPromptStripperMiddleware"

    def __init__(
        self,
        *,
        strip_fs: bool,
        strip_todos: bool,
        strip_execute: bool,
    ) -> None:
        self._strip_fs = strip_fs
        self._strip_todos = strip_todos
        self._strip_execute = strip_execute

    # ── Synchronous ──

    def wrap_model_call(self, request, handler):
        request = self._strip(request)
        return handler(request)

    # ── Asynchronous ──

    async def awrap_model_call(self, request, handler):
        request = self._strip(request)
        return await handler(request)

    # ── Core logic ──

    def _strip(self, request):
        """Modify ``request.system_message`` to remove stripped sections."""
        if not (self._strip_fs or self._strip_todos or self._strip_execute):
            return request

        sm = request.system_message
        if sm is None:
            return request

        content = sm.content
        if isinstance(content, str):
            new_text = self._strip_string(content)
            if new_text != content:
                from langchain_core.messages import SystemMessage
                request = request.override(
                    system_message=SystemMessage(content=new_text)
                )
        elif isinstance(content, list):
            new_blocks = self._strip_blocks(content)
            if new_blocks != content:
                from langchain_core.messages import SystemMessage
                request = request.override(
                    system_message=SystemMessage(content=new_blocks)
                )
        return request

    def _strip_blocks(self, blocks: list) -> list:
        """Filter out content blocks belonging to stripped sections."""
        result = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if self._should_strip_block(text):
                    continue
            result.append(block)
        return result

    def _should_strip_block(self, text: str) -> bool:
        """Check if a text block should be stripped entirely."""
        if self._strip_fs and _FS_MARKER in text:
            return True
        if self._strip_todos and _TODO_MARKER in text:
            return True
        if self._strip_execute and _EXECUTE_MARKER in text:
            return True
        return False

    def _strip_string(self, text: str) -> str:
        """Strip sections from string content by header."""
        # Split into sections by ## headers, filter out stripped ones.
        sections = _SECTION_RE.findall(text)
        kept = []
        for section in sections:
            section_stripped = section.strip()
            if self._should_strip_block(section_stripped):
                continue
            kept.append(section_stripped)
        return "\n\n".join(kept).strip()


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
        model: str = "deepseek-v4-flash",
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

        # Lazy imports keep top-level import cheap.
        from deepagents import create_deep_agent
        from langchain.chat_models import init_chat_model

        from src.runtime.adapters.langgraph_bridge import (
            LangChainToolShim,
            LangGraphCheckpointShim,
        )

        # 1. Resolve agent-scoped settings
        agent = ctx.agent
        model_name = (
            (agent.model if agent and agent.model else self.model)
            or "deepseek-v4-flash"
        )
        max_tokens = agent.max_tokens if agent and agent.max_tokens else 4096
        temperature = agent.temperature if agent is not None else 0.7
        system_prompt = (
            ctx.working_memory.get("system_prompt", "")
            or (agent.system_prompt if agent else "")
        )

        # 2. Build tool shims from the agent's whitelist.
        # Skip colliding tools (ls/glob/grep) — deepagents' versions handle
        # them (scoped by permissions) to avoid duplicate tool names in the
        # LLM's tool list. Non-colliding tools get platform shims so the
        # Phase 3a pipeline (whitelist → sandbox → hooks) runs for each call.
        tools: list[Any] = []
        if ctx.tool_engine is not None:
            available = ctx.tool_engine.available_tools(ctx.workspace_id)
            tools = [
                LangChainToolShim(t, ctx)
                for t in available
                if t.name not in _COLLIDING_FILESYSTEM_TOOLS
            ]

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

        # 4. Build checkpoint shim bound to this session.
        # Wave 2.5: ctx.checkpoint is now the CheckpointStore directly
        # (CheckpointScope wrapper was removed with DirectLLM).
        checkpointer: LangGraphCheckpointShim | None = None
        if ctx.checkpoint is not None:
            checkpointer = LangGraphCheckpointShim(
                store=ctx.checkpoint,
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
        # ``create_deep_agent`` auto-injects its own built-in tools
        # (ls/read_file/write_file/edit_file/glob/grep/execute/write_todos/
        # task) regardless of the ``tools=`` arg — that arg is additive
        # ("it never removes a built-in"). To make the agent's tool
        # whitelist actually enforce, we strip the built-ins via
        # ``_ToolExclusionMiddleware`` (passed through ``middleware=``,
        # which deepagents places after all tool-injecting middleware).
        # ``permissions`` constrain the filesystem tools to
        # ``ctx.workspace_root`` as defense-in-depth.
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

        # 6b. Build the tool-exclusion set.
        # Non-colliding built-ins (read_file/write_file/edit_file/execute/
        # write_todos) are always excluded — platform shims win (spec D7).
        # ``task`` is kept only when subagents are configured.
        # Colliding built-ins (ls/glob/grep) are excluded when NOT in the
        # agent's whitelist; when whitelisted, deepagents' version (scoped
        # by permissions) is the sole provider (no platform shim).
        excluded: set[str] = set(_EXCLUDED_FILESYSTEM_TOOLS)
        excluded.add(_EXCLUDED_TODO_TOOL)
        if not subagents:
            excluded.add(_TASK_TOOL)
        if ctx.tool_engine is not None:
            for t in _COLLIDING_FILESYSTEM_TOOLS:
                if ctx.tool_engine.is_allowed(t):
                    excluded.discard(t)  # keep deepagents' version
                else:
                    excluded.add(t)  # not whitelisted → strip
        else:
            # No tool engine → no whitelist → exclude colliding tools too.
            excluded |= _COLLIDING_FILESYSTEM_TOOLS

        middleware: list[Any] = []
        if excluded:
            try:
                from deepagents.middleware._tool_exclusion import (
                    _ToolExclusionMiddleware,
                )
                middleware.append(
                    _ToolExclusionMiddleware(excluded=frozenset(excluded))
                )
            except ImportError:
                logger.warning(
                    "_ToolExclusionMiddleware unavailable; deepagents "
                    "built-in tools cannot be filtered — agent whitelist "
                    "may not enforce for built-ins."
                )

        # 6c. System-prompt stripper.
        # FilesystemMiddleware and TodoListMiddleware inject FIXED system
        # prompt sections that mention their tools by name, regardless of
        # the agent's whitelist. _ToolExclusionMiddleware removes tool
        # SCHEMAS but not prompt TEXT. This stripper runs AFTER those
        # middleware (user middleware is inserted later in the stack) and
        # strips the prompt sections for excluded tools so the system
        # prompt stays consistent with the actual tool list.
        strip_fs = bool(excluded & _EXCLUDED_FILESYSTEM_TOOLS)
        strip_todos = _EXCLUDED_TODO_TOOL in excluded
        strip_execute = "execute" in excluded
        if strip_fs or strip_todos or strip_execute:
            middleware.append(
                _SystemPromptStripperMiddleware(
                    strip_fs=strip_fs,
                    strip_todos=strip_todos,
                    strip_execute=strip_execute,
                )
            )

        # Diagnostic: log the tool-filter state so whitelist enforcement
        # can be verified at runtime (bug: "无论选啥，测试都是全量注入").
        shim_names = [
            getattr(t, "name", "") for t in tools
            if hasattr(t, "name")
        ]
        logger.info(
            "deepagents tool filter: agent=%r whitelist=%s "
            "shims=%s excluded=%s middleware_attached=%s "
            "prompt_strip=[fs=%s todos=%s exec=%s]",
            getattr(agent, "name", "?"),
            sorted(ctx.tool_engine._allowed) if ctx.tool_engine else None,
            sorted(shim_names),
            sorted(excluded),
            bool(middleware),
            strip_fs, strip_todos, strip_execute,
        )

        deep_agent = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            subagents=subagents or None,
            checkpointer=checkpointer,
            permissions=perms or None,
            middleware=middleware or None,
        )

        # 7. Stream events
        config: dict[str, Any] = {"configurable": {"thread_id": ctx.session_id}}
        normalized = self._normalize_messages(messages)
        # When a checkpointer is present, the graph already has the full
        # conversation history in its state. Passing the full history
        # again causes langgraph to attempt redundant state reconciliation
        # which can fail with a ValueError unpacking tuple mismatch.
        # Instead, only pass the *new* message(s) — the last user message.
        if checkpointer and normalized:
            # Find the last user message in the normalized list.
            last_user = None
            for m in reversed(normalized):
                if m.get("role") == "user":
                    last_user = m
                    break
            input_payload = {"messages": [last_user] if last_user else normalized[-1:]}
        else:
            input_payload = {"messages": normalized}

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
        """Map one LangGraph v3 ``astream_events`` ProtocolEvent → StreamEvents.

        LangGraph v3 ProtocolEvent format:
        ``{"type": "event", "method": str, "params": {"data": Any, ...}}``

        Channels:
        - ``method=messages``: ``params.data`` is a 2-tuple ``(payload, metadata)``.
          Payload events: ``message-start``, ``content-block-start``,
          ``content-block-delta`` (text-delta / reasoning-delta / block-delta),
          ``content-block-finish``, ``message-finish``, ``error``.
        - ``method=tools``: ``params.data`` is a dict.
          Events: ``tool-started``, ``tool-finished``, ``tool-error``.
        - ``method=values``: ``params.data`` is a state snapshot dict.
        - ``method=on_chain_start/end``: subagent lifecycle.

        Legacy ``tool-call`` / ``tool-result`` sub-events on the messages
        channel are also handled for backward compatibility with tests.
        """
        method = event.get("method", "")
        params = event.get("params", {}) or {}
        data = params.get("data")

        # ── method=messages: streaming + tool calls ──
        if method == "messages":
            if not isinstance(data, (list, tuple)) or not data:
                return []
            payload = data[0]
            if not isinstance(payload, dict):
                return []
            sub_event = payload.get("event", "")

            # --- content-block-delta: text / reasoning streaming ---
            if sub_event == "content-block-delta":
                delta = payload.get("delta", {}) or {}
                delta_type = delta.get("type", "")

                if delta_type == "text-delta":
                    text = delta.get("text", "")
                    if text:
                        return [StreamEvent(
                            type="text", data={"content": text}
                        )]
                    return []

                # Reasoning / thinking streaming (e.g. deepseek-v4-pro).
                # v3 spec: delta_type="reasoning-delta", field="reasoning".
                # Also check legacy field names for cross-provider compat.
                if delta_type in ("reasoning-delta", "thinking-delta"):
                    content = (
                        delta.get("reasoning")
                        or delta.get("reasoning_content")
                        or delta.get("thinking")
                        or delta.get("text")
                        or ""
                    )
                    if content:
                        return [StreamEvent(
                            type="reasoning", data={"content": content}
                        )]
                return []

            # --- content-block-finish: tool_call completion ---
            # The LLM finalizes a tool call block with full name/id/args.
            if sub_event == "content-block-finish":
                content = payload.get("content", {}) or {}
                block_type = content.get("type", "")
                if block_type in ("tool_call", "server_tool_call"):
                    return [StreamEvent(
                        type="tool_call",
                        data={
                            "name": content.get("name", ""),
                            "args": content.get("args", {}),
                            "call_id": content.get("id", ""),
                        },
                        already_executed=True,
                    )]
                # reasoning block finished — emit full reasoning content
                if block_type == "reasoning":
                    reasoning_text = content.get("reasoning", "")
                    if reasoning_text:
                        return [StreamEvent(
                            type="reasoning", data={"content": reasoning_text}
                        )]
                return []

            # --- content-block-start: tool_call announcement (optional) ---
            # Fires when the LLM begins a tool call block; args are not yet
            # available. We skip this and emit tool_call on finish instead.
            if sub_event == "content-block-start":
                return []

            # --- message-finish: usage metadata ---
            if sub_event == "message-finish":
                usage_data = payload.get("usage")
                if usage_data and isinstance(usage_data, dict):
                    usage = Usage(
                        input_tokens=usage_data.get("input_tokens", 0),
                        output_tokens=usage_data.get("output_tokens", 0),
                        total_tokens=usage_data.get("total_tokens", 0),
                    )
                    return [StreamEvent(
                        type="status",
                        data={"usage": usage.model_dump()},
                    )]
                return []

            # --- error on messages channel ---
            if sub_event == "error":
                return [StreamEvent(
                    type="error",
                    data={
                        "code": "DEEPAGENTS_EVENT_ERROR",
                        "message": payload.get("message", "")[:500],
                        "event": "messages.error",
                    },
                )]

            # --- Legacy: tool-call / tool-result sub-events (tests) ---
            if sub_event == "tool-call":
                return [StreamEvent(
                    type="tool_call",
                    data={
                        "name": payload.get("name", ""),
                        "args": payload.get("args", {}),
                        "call_id": payload.get("id", ""),
                    },
                    already_executed=True,
                )]

            if sub_event == "tool-result":
                output = payload.get("output", "")
                error_str: str | None = None
                if isinstance(output, str) and output.startswith("ERROR: "):
                    error_str = output.removeprefix("ERROR: ")
                    output = ""
                return [StreamEvent(
                    type="tool_result",
                    data={
                        "name": payload.get("name", ""),
                        "output": output if isinstance(output, str) else str(output),
                        "error": error_str,
                    },
                    already_executed=True,
                )]

            return []

        # ── method=tools: tool execution lifecycle ──
        # params.data is a dict with event, tool_call_id, etc.
        if method == "tools":
            if not isinstance(data, dict):
                return []
            tool_event = data.get("event", "")

            if tool_event == "tool-finished":
                output = data.get("output", "")
                return [StreamEvent(
                    type="tool_result",
                    data={
                        "name": data.get("tool_name", ""),
                        "output": output if isinstance(output, str) else str(output),
                        "error": None,
                        "call_id": data.get("tool_call_id", ""),
                    },
                    already_executed=True,
                )]

            if tool_event == "tool-error":
                return [StreamEvent(
                    type="tool_result",
                    data={
                        "name": data.get("tool_name", ""),
                        "output": "",
                        "error": data.get("message", ""),
                        "call_id": data.get("tool_call_id", ""),
                    },
                    already_executed=True,
                )]

            # tool-started / tool-output-delta — skip (tool_call already
            # emitted from messages channel on content-block-finish).
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

        # ── Error events (top-level type="error") ──
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

        Imports are lazy so this module stays cheap to import
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
