"""P3a: Tool registry + execution engine.

``ToolRegistry`` holds tool definitions (builtin / custom / mcp).
``ToolEngine`` is constructed per-run with the agent's allowed-tools
whitelist and routes execution to the right backend.

Built-in tools live in ``src/runtime/harness/tools/builtin/`` and are
registered into the registry at startup by ``HarnessRegistry.create()``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext


# ── Exceptions ──────────────────────────────────────────────────────────


class ToolError(Exception):
    """Base error for tool execution failures."""


class ToolPermissionError(ToolError):
    """Agent tried to call a tool not in its whitelist."""


class ToolNotFoundError(ToolError):
    """Tool name not registered."""


class ToolTimeoutError(ToolError):
    """Tool execution exceeded its timeout."""


# ── Pydantic models ─────────────────────────────────────────────────────


class ToolDefinition(BaseModel):
    """Declarative tool definition.

    ``source`` determines how the tool is executed:
    - ``builtin``: dispatch to a Python handler in ``tools/builtin/``
    - ``custom``: run via ``SandboxManager`` (requires_sandbox=True)
    - ``mcp``: forward to an MCP server via ``MCPManager``
    """

    name: str
    description: str
    input_schema: dict
    source: Literal["builtin", "custom", "mcp"] = "builtin"
    handler: str | None = None
    mcp_server: str | None = None
    timeout: int = 60
    workspace_id: str | None = None  # None = global builtin
    requires_sandbox: bool = False


class ToolResult(BaseModel):
    """Standard result envelope returned by every tool execution."""

    name: str
    output: str
    error: str | None = None
    metadata: dict = Field(default_factory=dict)


# Type alias for builtin handler functions.
BuiltinHandler = Callable[[dict, "HarnessContext"], "Any"]


# ── Registry ────────────────────────────────────────────────────────────


class ToolRegistry:
    """Platform-level registry of all available tools.

    Tools are namespaced by ``(workspace_id, name)``. Builtin tools
    have ``workspace_id=None`` and are available to every workspace.
    Workspace-scoped custom tools shadow builtins of the same name.
    """

    def __init__(self) -> None:
        # key: (workspace_id|None, name) -> ToolDefinition
        self._tools: dict[tuple[str | None, str], ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[(tool.workspace_id, tool.name)] = tool

    def unregister(
        self, name: str, workspace_id: str | None = None
    ) -> bool:
        return self._tools.pop((workspace_id, name), None) is not None

    def get(
        self, name: str, workspace_id: str | None = None
    ) -> ToolDefinition | None:
        # Prefer workspace-scoped, fall back to global builtin.
        return self._tools.get(
            (workspace_id, name)
        ) or self._tools.get((None, name))

    def list(
        self, workspace_id: str | None = None
    ) -> list[ToolDefinition]:
        """Return builtin tools + workspace-scoped tools for one workspace."""
        out: list[ToolDefinition] = []
        seen: set[str] = set()
        # Workspace-scoped first (shadow builtins)
        for (ws_id, name), tool in self._tools.items():
            if ws_id == workspace_id:
                out.append(tool)
                seen.add(name)
        # Then builtins not shadowed
        for (ws_id, name), tool in self._tools.items():
            if ws_id is None and name not in seen:
                out.append(tool)
        return out


# ── Per-run engine ──────────────────────────────────────────────────────


class ToolEngine:
    """Per-run tool executor scoped to the agent's allowed-tools list.

    Built by ``HarnessRuntime._build_context`` from the platform
    ``ToolRegistry`` + the agent's ``tools`` whitelist. Adapters call
    ``execute(name, args, ctx)`` — the engine handles permission
    checks, routing, and timeout enforcement.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        allowed_tools: list[str],
        builtin_handlers: dict[str, BuiltinHandler] | None = None,
        mcp_manager: Any | None = None,
        sandbox: Any | None = None,
    ) -> None:
        self._registry = registry
        self._allowed = set(allowed_tools)
        self._builtin_handlers = builtin_handlers or {}
        self._mcp = mcp_manager
        self._sandbox = sandbox

    def is_allowed(self, name: str) -> bool:
        return name in self._allowed

    def available_tools(
        self, workspace_id: str | None = None
    ) -> list[ToolDefinition]:
        """Return tools the agent may call (whitelist ∩ registry)."""
        all_tools = self._registry.list(workspace_id)
        return [t for t in all_tools if t.name in self._allowed]

    async def schemas(
        self, workspace_id: str | None = None
    ) -> list[dict]:
        """JSON Schema list for the LLM tool-use prompt."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self.available_tools(workspace_id)
        ]

    async def execute(
        self,
        name: str,
        args: dict,
        ctx: "HarnessContext",
    ) -> ToolResult:
        """Execute a tool by name. Raises ``ToolError`` on failure."""
        if not self.is_allowed(name):
            raise ToolPermissionError(
                f"Tool {name!r} not allowed for this agent"
            )

        tool = self._registry.get(name, ctx.workspace_id)
        if tool is None:
            raise ToolNotFoundError(f"Tool {name!r} not registered")

        if tool.source == "builtin":
            return await self._exec_builtin(tool, args, ctx)
        if tool.source == "mcp":
            return await self._exec_mcp(tool, args, ctx)
        if tool.source == "custom":
            return await self._exec_custom(tool, args, ctx)
        raise ToolError(f"Unknown tool source: {tool.source!r}")

    # ── Dispatchers ──
    async def _exec_builtin(
        self, tool: ToolDefinition, args: dict, ctx: "HarnessContext"
    ) -> ToolResult:
        handler = self._builtin_handlers.get(tool.handler or tool.name)
        if handler is None:
            raise ToolNotFoundError(
                f"No handler registered for builtin {tool.name!r}"
                f" (handler={tool.handler!r})"
            )
        try:
            result = await handler(args, ctx)
            if isinstance(result, ToolResult):
                return result
            # Handlers may return a plain dict or str for convenience.
            if isinstance(result, dict):
                return ToolResult(
                    name=tool.name,
                    output=str(result.get("output", "")),
                    error=result.get("error"),
                    metadata=result.get("metadata", {}),
                )
            return ToolResult(name=tool.name, output=str(result))
        except Exception as exc:
            return ToolResult(
                name=tool.name, output="", error=f"{type(exc).__name__}: {exc}"
            )

    async def _exec_mcp(
        self, tool: ToolDefinition, args: dict, ctx: "HarnessContext"
    ) -> ToolResult:
        if self._mcp is None or tool.mcp_server is None:
            raise ToolError(
                f"MCP tool {tool.name!r} unavailable (no manager / server)"
            )
        try:
            raw = await self._mcp.call_tool(
                tool.mcp_server, tool.name, args
            )
            return ToolResult(
                name=tool.name, output=str(raw), metadata={"mcp_server": tool.mcp_server}
            )
        except Exception as exc:
            return ToolResult(
                name=tool.name, output="", error=f"MCP error: {exc}"
            )

    async def _exec_custom(
        self, tool: ToolDefinition, args: dict, ctx: "HarnessContext"
    ) -> ToolResult:
        if self._sandbox is None or not tool.requires_sandbox:
            raise ToolError(
                f"Custom tool {tool.name!r} requires sandbox but none configured"
            )
        return await self._sandbox.execute_tool(tool, args, ctx)


# Module-level singleton.
tools = ToolRegistry()
