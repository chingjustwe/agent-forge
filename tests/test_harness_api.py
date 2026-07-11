"""Tests for P3a harness API routes: tools, MCP, skills, memory, guardrails.

Mirrors the fixture pattern from ``test_agents.py``: module-level ``app``
fixture, ``_token`` / ``_seed`` helpers, ``ASGITransport`` + ``AsyncClient``.

Two autouse fixtures prepare the environment:
- ``setup_db``: creates the ``memory_records`` table + FTS5 virtual table
  (the SQLiteMemoryStore talks to raw SQL, not the ORM metadata).
- ``setup_harness``: resets the HarnessRegistry singleton and re-creates it
  so every test starts with the builtin tools / guardrails / fresh MCP /
  memory / skills subsystems.

Resource names (custom tools, MCP servers, guardrails, memory content)
carry a UUID suffix so tests remain isolated even though the module-level
``ToolRegistry`` singleton persists across tests.
"""
import uuid as _uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import AuditLog, Tenant, User, Workspace, WorkspaceMember


# ── Constants ───────────────────────────────────────────────────────────
WS_ID = "ws-api-test"
TENANT_ID = "t-api-test"
ADMIN_USER = "u-admin"
MEMBER_USER = "u-member"


# ── Fixtures & helpers ──────────────────────────────────────────────────
@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


def _token(user_id: str, tenant_id: str, role: str = "member", email: str | None = None):
    return create_jwt({
        "id": user_id,
        "sub": user_id,
        "tenant_id": tenant_id,
        "email": email or f"{user_id}@test.com",
        "role": role,
    })


async def _seed(
    ws_id: str,
    tenant_id: str,
    user_id: str,
    ws_role: str = "workspace_admin",
    tenant_role: str | None = None,
    email: str | None = None,
) -> str:
    """Seed tenant + workspace + user + WorkspaceMember. Returns JWT."""
    if tenant_role is None:
        tenant_role = ws_role
    async with async_session() as session:
        if not await session.get(Tenant, tenant_id):
            session.add(Tenant(id=tenant_id, name="T", domain=f"{tenant_id}.test"))
            await session.flush()
        if not await session.get(Workspace, ws_id):
            session.add(Workspace(id=ws_id, tenant_id=tenant_id, name=f"WS {ws_id}"))
            await session.flush()
        if not await session.get(User, user_id):
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=email or f"{user_id}@test.com",
                    name=user_id,
                    role=tenant_role,
                )
            )
            await session.flush()
        if not await session.get(WorkspaceMember, (ws_id, user_id)):
            session.add(
                WorkspaceMember(workspace_id=ws_id, user_id=user_id, role=ws_role)
            )
        await session.commit()
    return _token(user_id, tenant_id, role=tenant_role, email=email)


def _uniq(prefix: str = "t") -> str:
    """Generate a unique resource name with a short UUID suffix."""
    return f"{prefix}-{_uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def setup_db():
    """Create memory_records table for memory API tests."""
    from sqlalchemy import text
    from src.infra.db.engine import engine
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS memory_records ("
            "id VARCHAR(32) NOT NULL PRIMARY KEY,"
            "scope VARCHAR(20) NOT NULL,"
            "scope_id VARCHAR(32) NOT NULL,"
            "key TEXT,"
            "content TEXT NOT NULL,"
            "metadata TEXT NOT NULL DEFAULT '{}',"
            "memory_type TEXT NOT NULL DEFAULT 'episodic',"
            "created_at DATETIME NOT NULL,"
            "expires_at DATETIME"
            ")"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_memory_scope "
            "ON memory_records (scope, scope_id)"
        ))
        await conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_records_fts USING fts5("
            "content, content='memory_records', content_rowid='rowid'"
            ")"
        ))
    yield


@pytest.fixture(autouse=True)
async def setup_harness():
    """Reset and re-create the HarnessRegistry singleton per test."""
    from src.runtime.harness.registry import reset_registry, HarnessRegistry
    reset_registry()
    HarnessRegistry.create()
    yield
    reset_registry()


# ── TestToolsAPI ────────────────────────────────────────────────────────
class TestToolsAPI:
    @pytest.mark.asyncio
    async def test_list_tools_returns_builtin(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/tools",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        names = {t["name"] for t in resp.json()}
        assert "todo_write" in names

    @pytest.mark.asyncio
    async def test_list_tools_requires_auth(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(f"/api/v1/workspaces/{WS_ID}/tools")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_tools_member_can_read(self, app):
        tok = await _seed(WS_ID, TENANT_ID, MEMBER_USER, ws_role="member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/tools",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_create_custom_tool(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        tool_name = _uniq("custom_tool")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/tools",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": tool_name,
                    "description": "A custom tool",
                    "input_schema": {"type": "object", "properties": {}},
                },
            )
            assert resp.status_code == 201
            assert resp.json()["name"] == tool_name
            assert resp.json()["source"] == "custom"
            # Verify it appears in the list
            list_resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/tools",
                headers={"Authorization": f"Bearer {tok}"},
            )
            names = {t["name"] for t in list_resp.json()}
            assert tool_name in names

    @pytest.mark.asyncio
    async def test_create_duplicate_tool_returns_409(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        tool_name = _uniq("dup_tool")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp1 = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/tools",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": tool_name, "description": "first", "input_schema": {}},
            )
            assert resp1.status_code == 201
            resp2 = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/tools",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": tool_name, "description": "second", "input_schema": {}},
            )
            assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_create_tool_requires_admin(self, app):
        tok = await _seed(WS_ID, TENANT_ID, MEMBER_USER, ws_role="member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/tools",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": "should_fail", "description": "", "input_schema": {}},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_custom_tool(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        tool_name = _uniq("del_tool")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                f"/api/v1/workspaces/{WS_ID}/tools",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": tool_name, "description": "", "input_schema": {}},
            )
            resp = await ac.delete(
                f"/api/v1/workspaces/{WS_ID}/tools/{tool_name}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_builtin_tool_forbidden(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/workspaces/{WS_ID}/tools/todo_write",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_missing_tool_returns_404(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/workspaces/{WS_ID}/tools/nonexistent_tool_xyz",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 404


# ── TestMCPAPI ──────────────────────────────────────────────────────────
class TestMCPAPI:
    @pytest.mark.asyncio
    async def test_list_mcp_servers_empty(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_create_mcp_server(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        server_name = _uniq("mcp")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": server_name,
                    "endpoint": "http://localhost:8080",
                    "transport": "http",
                    "enabled": True,
                },
            )
            assert resp.status_code == 201
            assert resp.json()["name"] == server_name
            assert resp.json()["endpoint"] == "http://localhost:8080"
            # Verify it appears in the list
            list_resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers",
                headers={"Authorization": f"Bearer {tok}"},
            )
            names = {s["name"] for s in list_resp.json()}
            assert server_name in names

    @pytest.mark.asyncio
    async def test_create_duplicate_returns_409(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        server_name = _uniq("dup_mcp")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp1 = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": server_name, "endpoint": "http://a", "transport": "http"},
            )
            assert resp1.status_code == 201
            resp2 = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": server_name, "endpoint": "http://b", "transport": "http"},
            )
            assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_update_mcp_server(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        server_name = _uniq("upd_mcp")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": server_name, "endpoint": "http://old:8080", "transport": "http"},
            )
            resp = await ac.put(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers/{server_name}",
                headers={"Authorization": f"Bearer {tok}"},
                json={"endpoint": "http://new:9090"},
            )
        assert resp.status_code == 200
        assert resp.json()["endpoint"] == "http://new:9090"

    @pytest.mark.asyncio
    async def test_delete_mcp_server(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        server_name = _uniq("del_mcp")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": server_name, "endpoint": "http://x", "transport": "http"},
            )
            resp = await ac.delete(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers/{server_name}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_missing_returns_404(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers/nonexistent",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_discover_tools_missing_server_404(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers/nonexistent/tools",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_health_check_missing_server_404(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers/nonexistent/health",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_member_can_read(self, app):
        tok = await _seed(WS_ID, TENANT_ID, MEMBER_USER, ws_role="member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_member_can_write(self, app):
        """member has mcp:write (Wave 1 sidebar reorganization)."""
        tok = await _seed(WS_ID, TENANT_ID, MEMBER_USER, ws_role="member")
        server_name = _uniq("mcp-member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": server_name, "endpoint": "http://x", "transport": "http"},
            )
        assert resp.status_code == 201


# ── TestMCPAudit ────────────────────────────────────────────────────────
class TestMCPAudit:
    @pytest.mark.asyncio
    async def test_create_writes_audit_log(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        server_name = _uniq("mcp-audit-create")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": server_name, "endpoint": "http://x", "transport": "http"},
            )
            assert resp.status_code == 201
        async with async_session() as db:
            rows = (
                await db.execute(
                    select(AuditLog).where(AuditLog.action == "mcp.create")
                )
            ).scalars().all()
            assert any(r.target_id == server_name for r in rows)

    @pytest.mark.asyncio
    async def test_update_writes_audit_log(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        server_name = _uniq("mcp-audit-upd")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": server_name, "endpoint": "http://old", "transport": "http"},
            )
            resp = await ac.put(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers/{server_name}",
                headers={"Authorization": f"Bearer {tok}"},
                json={"endpoint": "http://new"},
            )
            assert resp.status_code == 200
        async with async_session() as db:
            rows = (
                await db.execute(
                    select(AuditLog).where(AuditLog.action == "mcp.update")
                )
            ).scalars().all()
            assert any(r.target_id == server_name for r in rows)

    @pytest.mark.asyncio
    async def test_delete_writes_audit_log(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        server_name = _uniq("mcp-audit-del")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": server_name, "endpoint": "http://x", "transport": "http"},
            )
            resp = await ac.delete(
                f"/api/v1/workspaces/{WS_ID}/mcp/servers/{server_name}",
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert resp.status_code == 204
        async with async_session() as db:
            rows = (
                await db.execute(
                    select(AuditLog).where(AuditLog.action == "mcp.delete")
                )
            ).scalars().all()
            assert any(r.target_id == server_name for r in rows)


# ── TestSkillsAPI ───────────────────────────────────────────────────────
class TestSkillsAPI:
    @pytest.mark.asyncio
    async def test_list_skills_returns_list(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/skills",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_list_skills_requires_auth(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(f"/api/v1/workspaces/{WS_ID}/skills")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_skill_missing_404(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/skills/nonexistent",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_member_can_read(self, app):
        tok = await _seed(WS_ID, TENANT_ID, MEMBER_USER, ws_role="member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/skills",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200


# ── TestMemoryAPI ───────────────────────────────────────────────────────
class TestMemoryAPI:
    @pytest.mark.asyncio
    async def test_create_and_list_memory(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        content = f"unique_content_{_uuid.uuid4().hex[:8]}"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/memory",
                headers={"Authorization": f"Bearer {tok}"},
                json={"content": content, "scope": "workspace"},
            )
            assert resp.status_code == 201
            record_id = resp.json()["id"]
            # List with scope=workspace → should include our record
            list_resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/memory?scope=workspace",
                headers={"Authorization": f"Bearer {tok}"},
            )
            assert list_resp.status_code == 200
            ids = {r["id"] for r in list_resp.json()}
            assert record_id in ids

    @pytest.mark.asyncio
    async def test_create_memory_invalid_scope(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/memory",
                headers={"Authorization": f"Bearer {tok}"},
                json={"content": "test", "scope": "bogus"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_delete_memory(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            create_resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/memory",
                headers={"Authorization": f"Bearer {tok}"},
                json={"content": "to delete", "scope": "workspace"},
            )
            record_id = create_resp.json()["id"]
            resp = await ac.delete(
                f"/api/v1/workspaces/{WS_ID}/memory/{record_id}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_missing_returns_404(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/workspaces/{WS_ID}/memory/nonexistent",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_search_memory(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        unique_term = f"searchable{_uuid.uuid4().hex[:8]}"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                f"/api/v1/workspaces/{WS_ID}/memory",
                headers={"Authorization": f"Bearer {tok}"},
                json={"content": f"hello {unique_term} world", "scope": "workspace"},
            )
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/memory/search",
                headers={"Authorization": f"Bearer {tok}"},
                json={"query": unique_term, "scope": "workspace"},
            )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert len(resp.json()) >= 1

    @pytest.mark.asyncio
    async def test_search_empty_query_422(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/memory/search",
                headers={"Authorization": f"Bearer {tok}"},
                json={"query": "", "scope": "workspace"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_member_can_write(self, app):
        tok = await _seed(WS_ID, TENANT_ID, MEMBER_USER, ws_role="member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/memory",
                headers={"Authorization": f"Bearer {tok}"},
                json={"content": "member memory", "scope": "workspace"},
            )
        assert resp.status_code == 201


# ── TestGuardrailsAPI ───────────────────────────────────────────────────
class TestGuardrailsAPI:
    @pytest.mark.asyncio
    async def test_list_guardrails_returns_builtin(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{WS_ID}/guardrails",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        names = {g["name"] for g in resp.json()}
        assert "content_filter" in names
        assert "pii_redaction" in names

    @pytest.mark.asyncio
    async def test_create_content_filter_guardrail(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        guardrail_name = _uniq("cf")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/guardrails",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "name": guardrail_name,
                    "type": "content_filter",
                    "patterns": ["spam"],
                },
            )
        assert resp.status_code == 201
        assert resp.json()["name"] == guardrail_name
        assert resp.json()["type"] == "content_filter"

    @pytest.mark.asyncio
    async def test_create_duplicate_returns_409(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        guardrail_name = _uniq("dup_gr")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp1 = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/guardrails",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": guardrail_name, "type": "content_filter", "patterns": []},
            )
            assert resp1.status_code == 201
            resp2 = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/guardrails",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": guardrail_name, "type": "pii_redaction"},
            )
            assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_create_unknown_type_422(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/guardrails",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": "bad_type", "type": "bogus"},
            )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_delete_guardrail(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        guardrail_name = _uniq("del_gr")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                f"/api/v1/workspaces/{WS_ID}/guardrails",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": guardrail_name, "type": "content_filter", "patterns": []},
            )
            resp = await ac.delete(
                f"/api/v1/workspaces/{WS_ID}/guardrails/{guardrail_name}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_missing_404(self, app):
        tok = await _seed(WS_ID, TENANT_ID, ADMIN_USER, ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/workspaces/{WS_ID}/guardrails/nonexistent",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_member_cannot_write(self, app):
        tok = await _seed(WS_ID, TENANT_ID, MEMBER_USER, ws_role="member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{WS_ID}/guardrails",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": "should_fail", "type": "content_filter"},
            )
        assert resp.status_code == 403
