"""P3a-P1: MCPManager — MCP server connection pool.

Manages MCP (Model Context Protocol) server registrations, lazy
connections, tool discovery, and tool invocation. MCP-discovered tools
are registered into ``ToolRegistry`` with ``source="mcp"`` so they
appear uniformly to ``ToolEngine``.

This module implements the *real* MCP protocol (JSON-RPC over the
configured transport) using the official ``mcp`` SDK client:

- ``sse``    — MCP SSE transport (server exposes an ``/sse`` endpoint that
  streams an ``endpoint`` event with the POST URL for JSON-RPC messages).
- ``http``   — MCP Streamable HTTP transport (a single ``/mcp``-style URL
  speaking JSON-RPC over HTTP).
- ``stdio``  — launches a local subprocess that speaks MCP over stdio.

The earlier P1 implementation assumed a bespoke REST API
(``GET /health``, ``GET /list_tools``, ``POST /call_tool``) which does
NOT match a real MCP server, so every operation against a real server
failed (the symptom was "Health → unreachable" and empty tool lists).
"""
from __future__ import annotations

import httpx
import logging
import os
import shlex
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel
from sqlalchemy import text

from src.infra.db.engine import async_session

logger = logging.getLogger(__name__)


class MCPServerConfig(BaseModel):
    """Configuration for one MCP server."""

    name: str
    workspace_id: str
    endpoint: str  # URL (sse/http) or command (stdio)
    transport: Literal["stdio", "http", "sse"] = "http"
    auth_token: str | None = None
    enabled: bool = True
    created_at: datetime | None = None


class MCPConnection:
    """Lazy MCP protocol client for a single registered server.

    Each operation (``health_check`` / ``list_tools`` / ``call``) opens its
    own JSON-RPC session, performs the ``initialize`` handshake, issues the
    request, then closes the session. The ``MCPManager`` caches the
    lightweight ``MCPConnection`` wrapper but does not keep long-lived
    sockets open, which keeps the harness cheap and robust across restarts.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config

    @asynccontextmanager
    async def _open_session(self):
        """Yield an initialized ``ClientSession`` for the configured transport."""
        config = self.config
        headers: dict[str, str] = {}
        if config.auth_token:
            headers["Authorization"] = f"Bearer {config.auth_token}"
        transport = config.transport
        # Auto-correct: an endpoint ending in ``/sse`` speaks the MCP SSE
        # protocol, not Streamable HTTP.  This catches the common
        # misconfiguration where the server was registered with the default
        # ``transport="http"`` against an ``/sse`` URL (the symptom is a
        # protocol handshake failure or an ``unexpected keyword argument
        # 'headers'`` error from ``streamable_http_client``).
        if (
            transport == "http"
            and isinstance(config.endpoint, str)
            and config.endpoint.rstrip("/").endswith("/sse")
        ):
            logger.info(
                "MCP %s: endpoint %s ends with '/sse'; auto-switching "
                "transport from 'http' to 'sse'",
                config.name,
                config.endpoint,
            )
            transport = "sse"

        if transport == "sse":
            async with sse_client(config.endpoint, headers=headers) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        elif transport == "http":
            # In mcp>=1.x the StreamableHTTP client no longer accepts
            # ``headers``; custom headers/timeout/auth must be configured on a
            # pre-built ``httpx.AsyncClient`` passed via ``http_client=``.
            if headers:
                async with httpx.AsyncClient(headers=headers) as http_client:
                    async with streamable_http_client(
                        config.endpoint, http_client=http_client
                    ) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            yield session
            else:
                async with streamable_http_client(config.endpoint) as (
                    read,
                    write,
                    _,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session
        elif transport == "stdio":
            parts = shlex.split(config.endpoint)
            if not parts:
                raise ValueError("stdio endpoint must be a command")
            server_params = StdioServerParameters(
                command=parts[0],
                args=parts[1:],
                env=dict(os.environ),
            )
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        else:
            raise ValueError(f"Unsupported MCP transport: {transport!r}")

    async def health_check(self) -> tuple[bool, str | None]:
        """Return ``(ok, error)``.

        ``ok`` is True if the server is reachable and speaks MCP (a
        successful ``initialize`` handshake proves both). ``error`` carries
        the exception message when ``ok`` is False, so callers can surface
        the *reason* instead of a silent "unreachable".
        """
        try:
            async with self._open_session() as _:
                # initialize() already succeeded inside the context manager.
                return True, None
        except Exception as exc:  # noqa: BLE001 - any failure => unreachable
            logger.warning(
                "MCP health_check failed for %s (%s): %s",
                self.config.name,
                self.config.transport,
                exc,
            )
            return False, f"{type(exc).__name__}: {exc}"

    async def list_tools(self) -> list[dict]:
        """Discover tools exposed by the MCP server (real ``tools/list``)."""
        async with self._open_session() as session:
            result = await session.list_tools()
            return [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema or {},
                }
                for tool in result.tools
            ]

    async def call(self, tool: str, args: dict) -> dict:
        """Call a tool on the MCP server (real ``tools/call``).

        Returns a dict with a ``text`` field (joined text content, ideal for
        agent output) plus ``isError`` and the raw structured content.
        """
        async with self._open_session() as session:
            result = await session.call_tool(tool, arguments=args or {})
            text_parts: list[str] = []
            for block in result.content:
                block_type = getattr(block, "type", None)
                if block_type == "text":
                    text_parts.append(block.text)
                elif block_type == "resource":
                    text_parts.append(getattr(block, "uri", str(block)))
                else:
                    text_parts.append(str(block))
            return {
                "text": "\n".join(text_parts),
                "isError": bool(result.isError),
                "structured": [b.model_dump() for b in result.content]
                if not result.isError
                else None,
            }

    async def close(self) -> None:
        """No-op: sessions are opened/closed per operation."""
        return None


class MCPManager:
    """Platform-level MCP server registry + connection pool.

    Server *configs* are persisted to the ``mcp_servers`` table (model
    ``MCPServer``) so registrations survive process restarts — the original
    P1 implementation was in-memory only and lost all servers on restart.
    ``self._servers`` is an in-memory cache of configs (populated at startup
    via ``load_from_db()`` and kept warm by every mutation) so that the
    synchronous ``list_servers`` / ``get_server`` used by the routes stay
    allocation-free. ``MCPConnection`` objects are lazily created and cached
    per ``(workspace_id, name)``.
    """

    def __init__(self) -> None:
        self._servers: dict[tuple[str, str], MCPServerConfig] = {}
        self._connections: dict[tuple[str, str], MCPConnection] = {}
        # Discovered-tool cache (key = (workspace_id, name)) so per-run
        # agent tool resolution (``HarnessRuntime._resolve_mcp_tools``) does
        # not re-hit the network on every chat message. Entries expire after
        # ``tool_cache_ttl`` seconds; invalidated on (un)register.
        self._tool_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
        self.tool_cache_ttl = 30.0

    async def load_from_db(self) -> None:
        """Populate the in-memory cache from the ``mcp_servers`` table.

        Called once at startup (``main.py`` lifespan) so previously
        registered servers are available after a restart.
        """
        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT id, name, workspace_id, endpoint, transport, "
                    "auth_token, enabled, created_at FROM mcp_servers"
                )
            )
            for row in result.fetchall():
                self._servers[(row.workspace_id, row.name)] = self._row_to_config(row)
        logger.info("MCP: loaded %d servers from DB", len(self._servers))

    async def register_server(self, config: MCPServerConfig) -> None:
        # Set created_at in place (preserves object identity for callers that
        # rely on ``get_server() is config``).
        if config.created_at is None:
            config.created_at = datetime.now(timezone.utc)
        key = (config.workspace_id, config.name)
        # Persist to DB. Upsert on (workspace_id, name) so re-registration
        # (incl. updates routed through this method) replaces the row without
        # changing its id or created_at.
        async with async_session() as db:
            await db.execute(
                text(
                    "INSERT INTO mcp_servers "
                    "(id, name, workspace_id, endpoint, transport, "
                    "auth_token, enabled, created_at) "
                    "VALUES (:id, :name, :ws, :endpoint, :transport, "
                    ":auth_token, :enabled, :created_at) "
                    "ON CONFLICT(workspace_id, name) DO UPDATE SET "
                    "endpoint=excluded.endpoint, "
                    "transport=excluded.transport, "
                    "auth_token=excluded.auth_token, "
                    "enabled=excluded.enabled"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "name": config.name,
                    "ws": config.workspace_id,
                    "endpoint": config.endpoint,
                    "transport": config.transport,
                    "auth_token": config.auth_token,
                    "enabled": 1 if config.enabled else 0,
                    "created_at": config.created_at.isoformat(),
                },
            )
            await db.commit()
        # Drop any existing connection so the next call re-connects.
        self._servers[key] = config
        old = self._connections.pop(key, None)
        self._tool_cache.pop(key, None)
        if old is not None:
            await old.close()
        logger.info("MCP server registered: %s", config.name)

    async def unregister_server(self, name: str, workspace_id: str) -> bool:
        key = (workspace_id, name)
        async with async_session() as db:
            result = await db.execute(
                text(
                    "DELETE FROM mcp_servers "
                    "WHERE workspace_id = :ws AND name = :name"
                ),
                {"ws": workspace_id, "name": name},
            )
            await db.commit()
            removed = result.rowcount > 0
        self._servers.pop(key, None)
        old = self._connections.pop(key, None)
        self._tool_cache.pop(key, None)
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

    @staticmethod
    def _row_to_config(row) -> MCPServerConfig:
        created_at = row.created_at
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except (ValueError, TypeError):
                created_at = None
        return MCPServerConfig(
            name=row.name,
            workspace_id=row.workspace_id,
            endpoint=row.endpoint,
            transport=row.transport or "http",
            auth_token=row.auth_token,
            enabled=bool(row.enabled),
            created_at=created_at,
        )

    async def connect(
        self, name: str, workspace_id: str
    ) -> MCPConnection:
        """Get or create a (stateless) connection for the named server."""
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
        """Discover tools from an MCP server (real MCP ``tools/list``).

        Results are cached for ``tool_cache_ttl`` seconds (keyed by
        ``(workspace_id, name)``) so repeated agent runs don't re-hit the
        network on every message. Invalidation happens on (un)register.
        """
        cache_key = (workspace_id, name)
        cached = self._tool_cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and (now - cached[0]) < self.tool_cache_ttl:
            return cached[1]
        conn = await self.connect(name, workspace_id)
        tools = await conn.list_tools()
        self._tool_cache[cache_key] = (now, tools)
        return tools

    async def call_tool(
        self, server: str, tool: str, args: dict
    ) -> dict:
        """Call a tool on an MCP server.

        Note: ``server`` is the server name; workspace_id is resolved from
        the first matching registered config. This matches the
        ``ToolEngine`` call signature which only passes the server name.
        """
        for (ws_id, srv_name), config in self._servers.items():
            if srv_name == server and config.enabled:
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
    ) -> tuple[bool, str | None]:
        config = self._servers.get((workspace_id, name))
        if config is None:
            return False, f"server {name!r} not registered in workspace {workspace_id!r}"
        try:
            conn = await self.connect(name, workspace_id)
            return await conn.health_check()
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    async def close_all(self) -> None:
        """Drop all cached connection wrappers. Called on shutdown."""
        for conn in self._connections.values():
            await conn.close()
        self._connections.clear()
        logger.info("MCP: all connections closed")
