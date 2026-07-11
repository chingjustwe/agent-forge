"""Phase 5: tests for agent ↔ MCP server binding.

Covers:
- AgentRegistry roundtrip of the ``mcp_servers`` field.
- Agent API: bind to valid servers (persisted), reject unknown servers (400),
  and clear bindings with an empty list.
- HarnessRuntime: ``_resolve_mcp_tools`` materializes selected servers' tools
  into the platform ToolRegistry and merges them into the agent's allowlist
  (server-level granularity + union with ``agent.tools``).
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import AgentConfig, Tenant, Workspace, WorkspaceMember
from src.runtime.harness.agents import AgentDefinition, AgentRegistry
from src.runtime.harness.registry import HarnessRegistry, get_registry
from src.runtime.harness.runtime import HarnessRuntime
from src.runtime.harness.tool_engine import ToolDefinition, ToolEngine, ToolRegistry
from src.runtime.models import RuntimeConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _seed_workspace(ws_id: str = "ws-mcp", tenant_id: str = "t-mcp") -> str:
    async with async_session() as db:
        if not await db.get(Tenant, tenant_id):
            db.add(Tenant(id=tenant_id, name="T", domain=f"{tenant_id}.test"))
        if not await db.get(Workspace, ws_id):
            db.add(Workspace(id=ws_id, tenant_id=tenant_id, name="WS"))
        await db.commit()
    return ws_id


def _make_token(user_id: str, tenant_id: str) -> str:
    return create_jwt({
        "id": user_id,
        "sub": user_id,
        "tenant_id": tenant_id,
        "email": f"{user_id}@test.com",
        "role": "workspace_admin",
    })


# ---------------------------------------------------------------------------
# 1. AgentRegistry unit
# ---------------------------------------------------------------------------

class TestAgentMCPRegistry:
    @pytest.mark.asyncio
    async def test_register_echoes_mcp_servers(self):
        ws = await _seed_workspace()
        reg = AgentRegistry()
        async with async_session() as db:
            agent = await reg.register(
                db,
                workspace_id=ws,
                name="Bound",
                adapter="deepagents",
                created_by="u1",
                tools=["builtin_a"],
                mcp_servers=["cloud", "local"],
            )
            assert agent.mcp_servers == ["cloud", "local"]

            got = await reg.get(db, agent.id)
            assert got.mcp_servers == ["cloud", "local"]

            # Update: clear bindings with empty list.
            updated = await reg.update(db, agent.id, mcp_servers=[])
            assert updated.mcp_servers == []

    @pytest.mark.asyncio
    async def test_default_mcp_servers_empty(self):
        ws = await _seed_workspace()
        reg = AgentRegistry()
        async with async_session() as db:
            agent = await reg.register(
                db, workspace_id=ws, name="Plain", adapter="deepagents", created_by="u1"
            )
            assert agent.mcp_servers == []
            row = await db.get(AgentConfig, agent.id)
            assert row.mcp_servers == []


# ---------------------------------------------------------------------------
# 2. Agent API route
# ---------------------------------------------------------------------------

class TestAgentMCPRoute:
    @pytest.fixture
    def app(self):
        from src.main import create_app

        return create_app()

    async def _register_server(self, ws: str, name: str):
        from src.runtime.harness.mcp import MCPServerConfig

        mcp = get_registry().mcp
        await mcp.register_server(
            MCPServerConfig(
                name=name,
                workspace_id=ws,
                endpoint="http://example.test/sse",
                transport="sse",
            )
        )

    async def _seed(self, ws: str, tenant: str, user: str):
        async with async_session() as db:
            if not await db.get(Tenant, tenant):
                db.add(Tenant(id=tenant, name="T", domain=f"{tenant}.test"))
            if not await db.get(Workspace, ws):
                db.add(Workspace(id=ws, tenant_id=tenant, name="WS"))
            if not await db.get(WorkspaceMember, (ws, user)):
                db.add(WorkspaceMember(workspace_id=ws, user_id=user, role="workspace_admin"))
            await db.commit()
        return _make_token(user, tenant)

    @pytest.mark.asyncio
    async def test_create_with_valid_mcp_servers(self, app):
        ws, tenant, user = "ws-rt1", "t-rt1", "u-rt1"
        tok = await self._seed(ws, tenant, user)
        await self._register_server(ws, "cloud")

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{ws}/agents",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": "bound", "framework": "deepagents", "mcp_servers": ["cloud"]},
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["mcp_servers"] == ["cloud"]

            # Persisted in DB.
            async with async_session() as db:
                row = (
                    await db.execute(
                        select(AgentConfig).where(AgentConfig.workspace_id == ws)
                    )
                ).scalar_one_or_none()
                assert row is not None
                assert row.mcp_servers == ["cloud"]

    @pytest.mark.asyncio
    async def test_create_rejects_unknown_mcp_server(self, app):
        ws, tenant, user = "ws-rt2", "t-rt2", "u-rt2"
        tok = await self._seed(ws, tenant, user)

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{ws}/agents",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": "bad", "framework": "deepagents", "mcp_servers": ["ghost"]},
            )
            assert resp.status_code == 400, resp.text
            assert "ghost" in resp.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_update_clears_mcp_servers(self, app):
        ws, tenant, user = "ws-rt3", "t-rt3", "u-rt3"
        tok = await self._seed(ws, tenant, user)
        await self._register_server(ws, "cloud")

        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            created = await ac.post(
                f"/api/v1/workspaces/{ws}/agents",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": "bound", "framework": "deepagents", "mcp_servers": ["cloud"]},
            )
            assert created.status_code == 201
            agent_id = created.json()["id"]

            resp = await ac.patch(
                f"/api/v1/workspaces/{ws}/agents/{agent_id}",
                headers={"Authorization": f"Bearer {tok}"},
                json={"mcp_servers": []},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["mcp_servers"] == []


# ---------------------------------------------------------------------------
# 3. HarnessRuntime tool materialization
# ---------------------------------------------------------------------------

class _FakeMCP:
    """Minimal MCPManager stub returning canned tools per server."""

    def __init__(self, tools_by_server: dict[str, list[dict]]) -> None:
        self._tools = tools_by_server

    async def list_tools(self, name: str, workspace_id: str) -> list[dict]:
        return self._tools.get(name, [])


class TestRuntimeMCPBinding:
    @pytest.mark.asyncio
    async def test_resolve_mcp_tools_materializes_and_merges(self):
        reg = HarnessRegistry.create()
        reg.mcp = _FakeMCP({
            "cloud": [
                {"name": "mcp_a", "description": "da", "input_schema": {}},
                {"name": "mcp_b", "description": "db", "input_schema": {}},
            ]
        })
        rt = HarnessRuntime(reg)

        agent = AgentDefinition(
            id="a1",
            name="bound",
            workspace_id="ws",
            tools=["builtin_a"],
            mcp_servers=["cloud"],
        )

        extra = await rt._resolve_mcp_tools(agent, "ws")
        assert extra == {"mcp_a", "mcp_b"}

        # Tools materialized into the platform ToolRegistry.
        td_a = reg.tools.get("mcp_a", "ws")
        assert td_a is not None
        assert td_a.source == "mcp"
        assert td_a.mcp_server == "cloud"

        # Allowlist = agent.tools ∪ MCP tools.
        ctx = rt._build_context(
            agent=agent,
            config=RuntimeConfig(agent="a1", workspace_id="ws"),
            session_id="s1",
            user_id="u1",
            trace_id="t1",
            workspace_settings={},
            workspace_root="",
            extra_allowed=extra,
        )
        assert ctx.tool_engine.is_allowed("builtin_a")
        assert ctx.tool_engine.is_allowed("mcp_a")
        assert ctx.tool_engine.is_allowed("mcp_b")
        assert not ctx.tool_engine.is_allowed("not_bound")

    @pytest.mark.asyncio
    async def test_resolve_mcp_tools_skips_missing_servers(self):
        reg = HarnessRegistry.create()
        reg.mcp = _FakeMCP({})  # no servers known
        rt = HarnessRuntime(reg)

        agent = AgentDefinition(
            id="a2", name="x", workspace_id="ws", mcp_servers=["ghost"]
        )
        # Should not raise; missing server just yields nothing.
        extra = await rt._resolve_mcp_tools(agent, "ws")
        assert extra == set()


class TestMCPToolExecution:
    """Regression: ToolEngine._exec_mcp must handle boolean isError correctly."""

    @pytest.mark.asyncio
    async def test_exec_mcp_success_with_isError_false(self):
        """When MCP call_tool returns ``isError=False`` (boolean), the
        ToolResult.error field must be ``None``, not the boolean ``False``.
        The old ``raw.get("error") or (raw.get("isError") and "...")``
        short-circuit produced ``False`` (bool), which Pydantic rejected
        because ``ToolResult.error`` is ``str | None``."""
        # StructuredMock Callable
        class _MockMCP:
            async def call_tool(self, server, tool, args):
                return {"text": "ok", "isError": False}

        from src.runtime.harness.context import HarnessContext

        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="mcp_ok",
            description="",
            input_schema={},
            source="mcp",
            mcp_server="srv",
            workspace_id="ws",
        ))

        engine = ToolEngine(
            registry=reg,
            allowed_tools=["mcp_ok"],
            mcp_manager=_MockMCP(),
        )

        ctx = HarnessContext(
            workspace_id="ws",
            user_id="u",
            session_id="s",
            trace_id="t",
            agent=AgentDefinition(id="a", name="a", workspace_id="ws"),
            tool_engine=engine,
        )

        # This must not raise Pydantic ValidationError.
        result = await engine.execute("mcp_ok", {}, ctx)
        assert result.error is None
        assert result.output == "ok"

    @pytest.mark.asyncio
    async def test_exec_mcp_error_with_isError_true(self):
        """When ``isError=True``, the error message should be set."""
        class _MockMCP:
            async def call_tool(self, server, tool, args):
                return {"text": "", "isError": True}

        from src.runtime.harness.context import HarnessContext

        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="mcp_err",
            description="",
            input_schema={},
            source="mcp",
            mcp_server="srv",
            workspace_id="ws",
        ))

        engine = ToolEngine(
            registry=reg,
            allowed_tools=["mcp_err"],
            mcp_manager=_MockMCP(),
        )

        ctx = HarnessContext(
            workspace_id="ws",
            user_id="u",
            session_id="s",
            trace_id="t",
            agent=AgentDefinition(id="a", name="a", workspace_id="ws"),
            tool_engine=engine,
        )

        result = await engine.execute("mcp_err", {}, ctx)
        assert result.error == "MCP tool returned an error"
        assert result.output == ""
