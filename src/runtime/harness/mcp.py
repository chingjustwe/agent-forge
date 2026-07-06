"""P3a-P1: MCPManager — MCP server connection pool.

Manages MCP (Model Context Protocol) server registrations, lazy
connections, tool discovery, and tool invocation. MCP-discovered tools
are registered into ``ToolRegistry`` with ``source="mcp"`` so they
appear uniformly to ``ToolEngine``.

P1 supports HTTP transport via httpx. ``stdio`` transport is stubbed
(not needed for 3a — no stdio MCP servers in scope).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MCPServerConfig(BaseModel):
    """Configuration for one MCP server."""

    name: str
    workspace_id: str
    endpoint: str  # URL or stdio command
    transport: Literal["stdio", "http", "sse"] = "http"
    auth_token: str | None = None
    enabled: bool = True
    created_at: datetime | None = None


class MCPConnection:
    """Lazy-initialized connection to one MCP server."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.is_connected: bool = False
        self._client: httpx.AsyncClient | None = None

    async def ensure_connected(self) -> None:
        if self.is_connected:
            return
        if self.config.transport == "http":
            headers: dict[str, str] = {}
            if self.config.auth_token:
                headers["Authorization"] = f"Bearer {self.config.auth_token}"
            self._client = httpx.AsyncClient(
                base_url=self.config.endpoint,
                headers=headers,
                timeout=30.0,
            )
        elif self.config.transport == "sse":
            # SSE uses the same httpx client but with streaming
            headers: dict[str, str] = {}
            if self.config.auth_token:
                headers["Authorization"] = f"Bearer {self.config.auth_token}"
            self._client = httpx.AsyncClient(
                base_url=self.config.endpoint,
                headers=headers,
                timeout=60.0,
            )
        elif self.config.transport == "stdio":
            # stdio transport would spawn a subprocess; deferred to future phase.
            logger.warning(
                "MCP stdio transport not yet implemented for %r",
                self.config.name,
            )
            return
        self.is_connected = True
        logger.info("MCP connected: %s (%s)", self.config.name, self.config.transport)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self.is_connected = False

    async def call(self, tool: str, args: dict) -> dict:
        """Call a tool on the connected MCP server."""
        await self.ensure_connected()
        if self._client is None:
            raise RuntimeError(f"MCP server {self.config.name!r} not connected")
        resp = await self._client.post(
            "/call_tool",
            json={"name": tool, "arguments": args},
        )
        resp.raise_for_status()
        return resp.json()

    async def list_tools(self) -> list[dict]:
        """Discover tools exposed by the MCP server."""
        await self.ensure_connected()
        if self._client is None:
            return []
        resp = await self._client.get("/list_tools")
        resp.raise_for_status()
        data = resp.json()
        return data.get("tools", []) if isinstance(data, dict) else data

    async def health_check(self) -> bool:
        """Check if the MCP server is reachable."""
        try:
            await self.ensure_connected()
            if self._client is None:
                return False
            resp = await self._client.get("/health")
            return resp.status_code == 200
        except Exception:
            return False


class MCPManager:
    """Platform-level MCP server registry + connection pool.

    Servers are stored in-memory (P1); a future phase will persist to
    the ``mcp_servers`` table. The manager maintains a pool of
    ``MCPConnection`` objects keyed by ``(workspace_id, name)``.
    """

    def __init__(self) -> None:
        self._servers: dict[tuple[str, str], MCPServerConfig] = {}
        self._connections: dict[tuple[str, str], MCPConnection] = {}

    async def register_server(self, config: MCPServerConfig) -> None:
        key = (config.workspace_id, config.name)
        self._servers[key] = config
        # Drop any existing connection so the next call re-connects.
        old = self._connections.pop(key, None)
        if old is not None:
            await old.close()
        logger.info("MCP server registered: %s", config.name)

    async def unregister_server(self, name: str, workspace_id: str) -> bool:
        key = (workspace_id, name)
        removed = self._servers.pop(key, None) is not None
        old = self._connections.pop(key, None)
        if old is not None:
            await old.close()
        return removed

    def list_servers(self, workspace_id: str) -> list[MCPServerConfig]:
        return [
            cfg for (ws_id, _), cfg in self._servers.items()
            if ws_id == workspace_id
        ]

    def get_server(
        self, name: str, workspace_id: str
    ) -> MCPServerConfig | None:
        return self._servers.get((workspace_id, name))

    async def connect(
        self, name: str, workspace_id: str
    ) -> MCPConnection:
        """Get or create a connection for the named server."""
        key = (workspace_id, name)
        if key in self._connections:
            return self._connections[key]
        config = self._servers.get(key)
        if config is None:
            raise KeyError(f"MCP server {name!r} not registered in workspace {workspace_id!r}")
        if not config.enabled:
            raise RuntimeError(f"MCP server {name!r} is disabled")
        conn = MCPConnection(config)
        self._connections[key] = conn
        return conn

    async def list_tools(
        self, name: str, workspace_id: str
    ) -> list[dict]:
        """Discover tools from an MCP server."""
        conn = await self.connect(name, workspace_id)
        return await conn.list_tools()

    async def call_tool(
        self, server: str, tool: str, args: dict
    ) -> dict:
        """Call a tool on an MCP server.

        Note: ``server`` is the server name; workspace_id is resolved
        from the connection's config. This matches the ``ToolEngine``
        call signature which only passes the server name.
        """
        # Find the connection by server name (first match)
        for (ws_id, srv_name), conn in self._connections.items():
            if srv_name == server:
                return await conn.call(tool, args)
        # Try to find the config and connect
        for (ws_id, srv_name), config in self._servers.items():
            if srv_name == server:
                conn = await self.connect(server, ws_id)
                return await conn.call(tool, args)
        raise KeyError(f"MCP server {server!r} not found")

    async def call_tool_scoped(
        self, server: str, workspace_id: str, tool: str, args: dict
    ) -> dict:
        """Call a tool with explicit workspace scope."""
        conn = await self.connect(server, workspace_id)
        return await conn.call(tool, args)

    async def health_check(
        self, name: str, workspace_id: str
    ) -> bool:
        config = self._servers.get((workspace_id, name))
        if config is None:
            return False
        try:
            conn = await self.connect(name, workspace_id)
            return await conn.health_check()
        except Exception:
            return False

    async def close_all(self) -> None:
        """Close all connections. Called by HarnessRegistry.shutdown()."""
        for conn in self._connections.values():
            await conn.close()
        self._connections.clear()
        logger.info("MCP: all connections closed")
