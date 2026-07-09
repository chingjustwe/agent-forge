"""Tests for MCPManager and MCPConnection.

Covers:
- MCPServerConfig defaults
- MCPManager: register/list/unregister/get, workspace isolation,
  connect caching, disabled/unknown error paths, scoped calls,
  health_check on unknown servers, close_all, DB persistence across reload
- MCPConnection: real MCP protocol session handling; unreachable (no live
  server) error paths for health/list/call; safe close
- Registry wiring: ``set_registry`` makes ``get_registry`` return the wired
  instance (regression for "MCP servers disappear after restart").

Tests that would make real HTTP calls against a live MCP server are avoided —
only unreachable endpoints (connection refused) are exercised.
"""
import pytest

from src.runtime.harness.mcp import (
    MCPConnection,
    MCPManager,
    MCPServerConfig,
)
from src.runtime.harness.registry import (
    HarnessRegistry,
    get_registry,
    reset_registry,
    set_registry,
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
        ok, _err = await mgr.health_check("nope", "ws-1")
        assert ok is False

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

    @pytest.mark.asyncio
    async def test_persisted_across_reload(self):
        """Regression: registered servers must survive a manager reload
        (i.e. a process restart) by being written to the DB and re-read by
        ``load_from_db``."""
        mgr = MCPManager()
        await mgr.register_server(
            _make_config(name="persist1", workspace_id="ws-reload", endpoint="http://reload:1")
        )
        # Simulate restart: brand-new manager, no in-memory cache.
        reloaded = MCPManager()
        await reloaded.load_from_db()
        loaded = reloaded.get_server("persist1", "ws-reload")
        assert loaded is not None
        assert loaded.endpoint == "http://reload:1"

    @pytest.mark.asyncio
    async def test_unregister_persisted(self):
        """Regression: removing a server must delete it from the DB, not
        just the in-memory cache."""
        mgr = MCPManager()
        await mgr.register_server(_make_config(name="gone", workspace_id="ws-del"))
        assert await mgr.unregister_server("gone", "ws-del") is True
        # A fresh manager loading from DB must NOT see the removed server.
        reloaded = MCPManager()
        await reloaded.load_from_db()
        assert reloaded.get_server("gone", "ws-del") is None


# ── MCPConnection ───────────────────────────────────────────────────────


class TestMCPConnection:
    @pytest.mark.asyncio
    async def test_health_check_unreachable_returns_false(self):
        # No live server on port 1 → connection refused → unreachable.
        cfg = _make_config(transport="http", endpoint="http://127.0.0.1:1/mcp")
        conn = MCPConnection(cfg)
        ok, _err = await conn.health_check()
        assert ok is False

    @pytest.mark.asyncio
    async def test_list_tools_unreachable_raises(self):
        cfg = _make_config(transport="http", endpoint="http://127.0.0.1:1/mcp")
        conn = MCPConnection(cfg)
        with pytest.raises(Exception):
            await conn.list_tools()

    @pytest.mark.asyncio
    async def test_call_unreachable_raises(self):
        cfg = _make_config(transport="http", endpoint="http://127.0.0.1:1/mcp")
        conn = MCPConnection(cfg)
        with pytest.raises(Exception):
            await conn.call("some_tool", {})

    @pytest.mark.asyncio
    async def test_close_is_safe(self):
        # close() is a no-op now (sessions are per-operation).
        cfg = _make_config(transport="http")
        conn = MCPConnection(cfg)
        await conn.close()  # must not raise


class TestMCPToolCache:
    @pytest.mark.asyncio
    async def test_list_tools_is_cached(self):
        """``list_tools`` must hit the network only once per TTL so that
        per-run agent tool resolution stays cheap across messages."""
        mgr = MCPManager()
        await mgr.register_server(_make_config(name="s1", workspace_id="ws"))

        calls = {"n": 0}

        async def _fake_list(self):
            calls["n"] += 1
            return [{"name": "t1", "description": "", "input_schema": {}}]

        orig = MCPConnection.list_tools
        MCPConnection.list_tools = _fake_list
        try:
            first = await mgr.list_tools("s1", "ws")
            second = await mgr.list_tools("s1", "ws")
        finally:
            MCPConnection.list_tools = orig

        assert calls["n"] == 1
        assert first == second



class TestRegistryWiring:
    def test_set_registry_makes_get_registry_return_same_instance(self):
        """Regression for MCP servers disappearing after restart: the wired
        registry (whose MCPManager was loaded from DB) must be the one
        returned to route handlers via ``get_registry``."""
        reset_registry()
        reg = HarnessRegistry.create()
        set_registry(reg)
        assert get_registry() is reg
        reset_registry()
