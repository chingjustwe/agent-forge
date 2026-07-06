"""Tests for MCPManager and MCPConnection.

Covers:
- MCPServerConfig defaults
- MCPManager: register/list/unregister/get, workspace isolation,
  connect caching, disabled/unknown error paths, scoped calls,
  health_check on unknown servers, close_all
- MCPConnection: lazy http connect, stdio stub, close idempotency

Tests that would make real HTTP calls (call_tool, list_tools,
health_check on a live server) are avoided — only error paths that
do not require network access are exercised.
"""
import pytest

from src.runtime.harness.mcp import (
    MCPConnection,
    MCPManager,
    MCPServerConfig,
)


def _make_config(name="srv1", workspace_id="ws-1", endpoint="http://localhost:9999", **kwargs):
    return MCPServerConfig(
        name=name,
        workspace_id=workspace_id,
        endpoint=endpoint,
        **kwargs,
    )


# ── MCPServerConfig ─────────────────────────────────────────────────────


class TestMCPServerConfig:
    def test_defaults(self):
        cfg = MCPServerConfig(
            name="s1", workspace_id="ws", endpoint="http://x"
        )
        assert cfg.transport == "http"
        assert cfg.enabled is True
        assert cfg.auth_token is None
        assert cfg.created_at is None


# ── MCPManager ──────────────────────────────────────────────────────────


class TestMCPManager:
    @pytest.mark.asyncio
    async def test_register_and_list_servers(self):
        mgr = MCPManager()
        await mgr.register_server(_make_config(name="s1", workspace_id="ws"))
        await mgr.register_server(_make_config(name="s2", workspace_id="ws"))
        servers = mgr.list_servers("ws")
        names = {s.name for s in servers}
        assert names == {"s1", "s2"}

    @pytest.mark.asyncio
    async def test_register_replaces_existing(self):
        mgr = MCPManager()
        await mgr.register_server(
            _make_config(name="s1", workspace_id="ws", endpoint="http://old")
        )
        await mgr.register_server(
            _make_config(name="s1", workspace_id="ws", endpoint="http://new")
        )
        servers = mgr.list_servers("ws")
        assert len(servers) == 1
        assert servers[0].endpoint == "http://new"
        # The replaced config is the one returned by get_server.
        assert mgr.get_server("s1", "ws").endpoint == "http://new"

    @pytest.mark.asyncio
    async def test_unregister(self):
        mgr = MCPManager()
        await mgr.register_server(_make_config(name="s1", workspace_id="ws"))
        assert await mgr.unregister_server("s1", "ws") is True
        assert mgr.get_server("s1", "ws") is None
        assert mgr.list_servers("ws") == []

    @pytest.mark.asyncio
    async def test_unregister_returns_false_for_unknown(self):
        mgr = MCPManager()
        assert await mgr.unregister_server("nope", "ws") is False

    @pytest.mark.asyncio
    async def test_list_servers_isolated_per_workspace(self):
        mgr = MCPManager()
        await mgr.register_server(_make_config(name="s1", workspace_id="ws-a"))
        await mgr.register_server(_make_config(name="s2", workspace_id="ws-b"))
        a = {s.name for s in mgr.list_servers("ws-a")}
        b = {s.name for s in mgr.list_servers("ws-b")}
        assert a == {"s1"}
        assert b == {"s2"}

    @pytest.mark.asyncio
    async def test_get_server(self):
        mgr = MCPManager()
        cfg = _make_config(name="s1", workspace_id="ws")
        await mgr.register_server(cfg)
        got = mgr.get_server("s1", "ws")
        assert got is cfg

    @pytest.mark.asyncio
    async def test_get_server_returns_none_for_unknown(self):
        mgr = MCPManager()
        assert mgr.get_server("nope", "ws") is None

    @pytest.mark.asyncio
    async def test_connect_creates_connection(self):
        mgr = MCPManager()
        await mgr.register_server(_make_config())
        conn = await mgr.connect("srv1", "ws-1")
        assert isinstance(conn, MCPConnection)
        assert conn.config.name == "srv1"

    @pytest.mark.asyncio
    async def test_connect_caches(self):
        mgr = MCPManager()
        await mgr.register_server(_make_config())
        c1 = await mgr.connect("srv1", "ws-1")
        c2 = await mgr.connect("srv1", "ws-1")
        assert c1 is c2

    @pytest.mark.asyncio
    async def test_connect_unknown_raises_keyerror(self):
        mgr = MCPManager()
        with pytest.raises(KeyError, match="not registered"):
            await mgr.connect("nope", "ws-1")

    @pytest.mark.asyncio
    async def test_connect_disabled_raises_runtimeerror(self):
        mgr = MCPManager()
        await mgr.register_server(_make_config(enabled=False))
        with pytest.raises(RuntimeError, match="disabled"):
            await mgr.connect("srv1", "ws-1")

    @pytest.mark.asyncio
    async def test_call_tool_scoped_unknown_raises(self):
        mgr = MCPManager()
        with pytest.raises(KeyError, match="not registered"):
            await mgr.call_tool_scoped("nope", "ws-1", "tool", {})

    @pytest.mark.asyncio
    async def test_health_check_unknown_returns_false(self):
        mgr = MCPManager()
        assert await mgr.health_check("nope", "ws-1") is False

    @pytest.mark.asyncio
    async def test_close_all(self):
        mgr = MCPManager()
        await mgr.register_server(_make_config(name="s1", workspace_id="ws"))
        await mgr.register_server(_make_config(name="s2", workspace_id="ws"))
        await mgr.connect("s1", "ws")
        await mgr.connect("s2", "ws")
        assert len(mgr._connections) == 2
        await mgr.close_all()
        assert len(mgr._connections) == 0


# ── MCPConnection ───────────────────────────────────────────────────────


class TestMCPConnection:
    @pytest.mark.asyncio
    async def test_ensure_connected_http(self):
        cfg = _make_config(transport="http")
        conn = MCPConnection(cfg)
        assert conn.is_connected is False
        assert conn._client is None
        await conn.ensure_connected()
        assert conn.is_connected is True
        assert conn._client is not None
        await conn.close()

    @pytest.mark.asyncio
    async def test_ensure_connected_stdio_warns(self):
        cfg = _make_config(transport="stdio")
        conn = MCPConnection(cfg)
        await conn.ensure_connected()
        # stdio transport is stubbed — connection stays disconnected.
        assert conn.is_connected is False
        assert conn._client is None

    @pytest.mark.asyncio
    async def test_close_resets(self):
        cfg = _make_config(transport="http")
        conn = MCPConnection(cfg)
        await conn.ensure_connected()
        assert conn.is_connected is True
        assert conn._client is not None
        await conn.close()
        assert conn.is_connected is False
        assert conn._client is None

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        cfg = _make_config(transport="http")
        conn = MCPConnection(cfg)
        await conn.ensure_connected()
        await conn.close()
        # Second close must not raise.
        await conn.close()
        assert conn.is_connected is False
        assert conn._client is None
