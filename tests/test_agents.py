"""Tests for P2-2: workspace-scoped agent configurations.

Covers model definition, create/list/detail/update/delete routes, RBAC
(member read / admin write), framework validation, cross-workspace
isolation, and the admin.py agent_count fix (real value after creating
an agent).
"""
import uuid as _uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import (
    AgentConfig,
    AuditLog,
    Tenant,
    User,
    Workspace,
    WorkspaceMember,
)


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------
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
    tenant_role: str = "member",
    email: str | None = None,
) -> str:
    """Seed tenant + workspace + user + WorkspaceMember. Returns JWT."""
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


async def _create_agent_via_api(
    app,
    token: str,
    ws_id: str,
    name: str = "Helper Agent",
    framework: str = "direct_llm",
    config: dict | None = None,
):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.post(
            f"/api/v1/workspaces/{ws_id}/agents",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": name, "framework": framework, "config": config or {}},
        )


# ---------------------------------------------------------------------------
# 1. Model definition
# ---------------------------------------------------------------------------
class TestAgentConfigModel:
    def test_tablename(self):
        assert AgentConfig.__tablename__ == "agent_configs"

    def test_fields_exist(self):
        cols = {c.name for c in AgentConfig.__table__.columns}
        assert {
            "id",
            "workspace_id",
            "name",
            "framework",
            "config",
            "created_by",
            "created_at",
            "updated_at",
        } <= cols

    def test_workspace_id_is_indexed(self):
        col = AgentConfig.__table__.columns["workspace_id"]
        assert col.index is True

    def test_config_defaults_to_empty_dict(self):
        # SQLAlchemy applies ``default=dict`` at insert time, not at
        # instantiation. The column-level default is the ``dict`` callable
        # (see ``test_default_config_when_omitted`` for the end-to-end
        # behavior: omitting ``config`` from the create payload yields
        # ``{}`` in the response).
        col_default = AgentConfig.__table__.columns["config"].default
        assert col_default is not None
        assert getattr(col_default.arg, "__name__", None) == "dict"


# ---------------------------------------------------------------------------
# 2. Create agent
# ---------------------------------------------------------------------------
class TestCreateAgent:
    @pytest.mark.asyncio
    async def test_admin_can_create(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-ca-{suffix}"
        ws = f"ws-ca-{suffix}"
        uid = f"admin-{suffix}"
        tok = await _seed(ws, tid, uid, ws_role="workspace_admin")

        resp = await _create_agent_via_api(app, tok, ws, name="My Agent", config={"model": "gpt-4"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"]
        assert body["workspace_id"] == ws
        assert body["name"] == "My Agent"
        assert body["framework"] == "direct_llm"
        assert body["config"] == {"model": "gpt-4"}
        assert body["created_by"] == uid
        assert body["created_at"]

    @pytest.mark.asyncio
    async def test_owner_can_create(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tok = await _seed(f"ws-{suffix}", f"t-{suffix}", f"owner-{suffix}", ws_role="workspace_owner")
        resp = await _create_agent_via_api(app, tok, f"ws-{suffix}")
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_member_forbidden(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tok = await _seed(f"ws-{suffix}", f"t-{suffix}", f"mem-{suffix}", ws_role="member")
        resp = await _create_agent_via_api(app, tok, f"ws-{suffix}")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_non_member_forbidden(self, app):
        suffix = _uuid.uuid4().hex[:8]
        # Seed user in ws-A, then try to create agent in ws-B (not a member).
        tok = await _seed(f"ws-a-{suffix}", f"t-{suffix}", f"u-{suffix}", ws_role="workspace_admin")
        async with async_session() as session:
            session.add(Workspace(id=f"ws-b-{suffix}", tenant_id=f"t-{suffix}", name="WS B"))
            await session.commit()
        resp = await _create_agent_via_api(app, tok, f"ws-b-{suffix}")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_framework_returns_400(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tok = await _seed(f"ws-{suffix}", f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/ws-{suffix}/agents",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": "X", "framework": "invalid_fw", "config": {}},
            )
        assert resp.status_code == 400
        assert "framework" in resp.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_empty_name_rejected(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tok = await _seed(f"ws-{suffix}", f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/ws-{suffix}/agents",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": "", "framework": "direct_llm", "config": {}},
            )
        assert resp.status_code == 422  # Pydantic min_length validation

    @pytest.mark.asyncio
    async def test_default_config_when_omitted(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tok = await _seed(f"ws-{suffix}", f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/ws-{suffix}/agents",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": "No Config", "framework": "adk"},
            )
        assert resp.status_code == 201
        assert resp.json()["config"] == {}

    @pytest.mark.asyncio
    async def test_create_writes_audit_log(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        ws = f"ws-{suffix}"
        tok = await _seed(ws, tid, f"admin-{suffix}", ws_role="workspace_admin")
        resp = await _create_agent_via_api(app, tok, ws, name="Audited")
        assert resp.status_code == 201
        agent_id = resp.json()["id"]
        async with async_session() as session:
            rows = (await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "agent.create",
                    AuditLog.target_id == agent_id,
                )
            )).scalars().all()
        assert len(rows) == 1
        assert rows[0].target_type == "agent"
        assert rows[0].workspace_id == ws


# ---------------------------------------------------------------------------
# 3. List agents
# ---------------------------------------------------------------------------
class TestListAgents:
    @pytest.mark.asyncio
    async def test_member_can_list(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        admin_tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        await _create_agent_via_api(app, admin_tok, ws, name="A1")
        await _create_agent_via_api(app, admin_tok, ws, name="A2")

        member_tok = await _seed(
            ws, f"t-{suffix}", f"mem-{suffix}", ws_role="member",
            email=f"mem-{suffix}@test.com",
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{ws}/agents",
                headers={"Authorization": f"Bearer {member_tok}"},
            )
        assert resp.status_code == 200
        names = {a["name"] for a in resp.json()}
        assert names == {"A1", "A2"}

    @pytest.mark.asyncio
    async def test_list_isolated_per_workspace(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        ws_a = f"ws-a-{suffix}"
        ws_b = f"ws-b-{suffix}"
        tok_a = await _seed(ws_a, tid, f"admin-a-{suffix}", ws_role="workspace_admin")
        tok_b = await _seed(ws_b, tid, f"admin-b-{suffix}", ws_role="workspace_admin",
                            email=f"admin-b-{suffix}@test.com")
        await _create_agent_via_api(app, tok_a, ws_a, name="Agent in A")
        await _create_agent_via_api(app, tok_b, ws_b, name="Agent in B")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp_a = await ac.get(
                f"/api/v1/workspaces/{ws_a}/agents",
                headers={"Authorization": f"Bearer {tok_a}"},
            )
            resp_b = await ac.get(
                f"/api/v1/workspaces/{ws_b}/agents",
                headers={"Authorization": f"Bearer {tok_b}"},
            )
        a_names = {a["name"] for a in resp_a.json()}
        b_names = {a["name"] for a in resp_b.json()}
        assert a_names == {"Agent in A"}
        assert b_names == {"Agent in B"}

    @pytest.mark.asyncio
    async def test_list_empty(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tok = await _seed(f"ws-{suffix}", f"t-{suffix}", f"u-{suffix}", ws_role="member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/ws-{suffix}/agents",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# 4. Get agent detail
# ---------------------------------------------------------------------------
class TestGetAgent:
    @pytest.mark.asyncio
    async def test_get_detail(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_agent_via_api(app, tok, ws, name="Detail")).json()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{ws}/agents/{created['id']}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Detail"

    @pytest.mark.asyncio
    async def test_get_cross_workspace_returns_404(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        ws_a = f"ws-a-{suffix}"
        ws_b = f"ws-b-{suffix}"
        tok_a = await _seed(ws_a, tid, f"admin-a-{suffix}", ws_role="workspace_admin")
        tok_b = await _seed(ws_b, tid, f"admin-b-{suffix}", ws_role="workspace_admin",
                            email=f"admin-b-{suffix}@test.com")
        created = (await _create_agent_via_api(app, tok_a, ws_a)).json()

        # Try to fetch ws_a's agent via ws_b's path — must 404, not leak.
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                f"/api/v1/workspaces/{ws_b}/agents/{created['id']}",
                headers={"Authorization": f"Bearer {tok_b}"},
            )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. Update agent
# ---------------------------------------------------------------------------
class TestUpdateAgent:
    @pytest.mark.asyncio
    async def test_update_name_and_config(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_agent_via_api(app, tok, ws)).json()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.patch(
                f"/api/v1/workspaces/{ws}/agents/{created['id']}",
                headers={"Authorization": f"Bearer {tok}"},
                json={"name": "Renamed", "config": {"model": "claude-3", "temperature": 0.5}},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Renamed"
        assert body["config"]["model"] == "claude-3"
        assert body["framework"] == "direct_llm"  # unchanged

    @pytest.mark.asyncio
    async def test_update_framework(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_agent_via_api(app, tok, ws)).json()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.patch(
                f"/api/v1/workspaces/{ws}/agents/{created['id']}",
                headers={"Authorization": f"Bearer {tok}"},
                json={"framework": "langgraph"},
            )
        assert resp.status_code == 200
        assert resp.json()["framework"] == "langgraph"

    @pytest.mark.asyncio
    async def test_update_invalid_framework_400(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_agent_via_api(app, tok, ws)).json()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.patch(
                f"/api/v1/workspaces/{ws}/agents/{created['id']}",
                headers={"Authorization": f"Bearer {tok}"},
                json={"framework": "bogus"},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_update_member_forbidden(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        admin_tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_agent_via_api(app, admin_tok, ws)).json()
        member_tok = await _seed(
            ws, f"t-{suffix}", f"mem-{suffix}", ws_role="member",
            email=f"mem-{suffix}@test.com",
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.patch(
                f"/api/v1/workspaces/{ws}/agents/{created['id']}",
                headers={"Authorization": f"Bearer {member_tok}"},
                json={"name": "Hacked"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 6. Delete agent
# ---------------------------------------------------------------------------
class TestDeleteAgent:
    @pytest.mark.asyncio
    async def test_admin_can_delete(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_agent_via_api(app, tok, ws)).json()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/workspaces/{ws}/agents/{created['id']}",
                headers={"Authorization": f"Bearer {tok}"},
            )
            # Subsequent get should 404
            after = await ac.get(
                f"/api/v1/workspaces/{ws}/agents/{created['id']}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 204
        assert after.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_member_forbidden(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        admin_tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_agent_via_api(app, admin_tok, ws)).json()
        member_tok = await _seed(
            ws, f"t-{suffix}", f"mem-{suffix}", ws_role="member",
            email=f"mem-{suffix}@test.com",
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/workspaces/{ws}/agents/{created['id']}",
                headers={"Authorization": f"Bearer {member_tok}"},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_cross_workspace_404(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-{suffix}"
        ws_a = f"ws-a-{suffix}"
        ws_b = f"ws-b-{suffix}"
        tok_a = await _seed(ws_a, tid, f"admin-a-{suffix}", ws_role="workspace_admin")
        tok_b = await _seed(ws_b, tid, f"admin-b-{suffix}", ws_role="workspace_admin",
                            email=f"admin-b-{suffix}@test.com")
        created = (await _create_agent_via_api(app, tok_a, ws_a)).json()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.delete(
                f"/api/v1/workspaces/{ws_b}/agents/{created['id']}",
                headers={"Authorization": f"Bearer {tok_b}"},
            )
        assert resp.status_code == 404
        # The agent in ws_a must still exist.
        async with async_session() as session:
            still = await session.get(AgentConfig, created["id"])
        assert still is not None

    @pytest.mark.asyncio
    async def test_delete_writes_audit_log(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws = f"ws-{suffix}"
        tok = await _seed(ws, f"t-{suffix}", f"admin-{suffix}", ws_role="workspace_admin")
        created = (await _create_agent_via_api(app, tok, ws, name="To Delete")).json()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.delete(
                f"/api/v1/workspaces/{ws}/agents/{created['id']}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        async with async_session() as session:
            rows = (await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "agent.delete",
                    AuditLog.target_id == created["id"],
                )
            )).scalars().all()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# 7. Admin agent_count fix
# ---------------------------------------------------------------------------
class TestAdminAgentCount:
    @pytest.mark.asyncio
    async def test_admin_count_reflects_created_agents(self, app):
        """Creating an agent must bump /api/v1/admin/workspaces agent_count."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-acc-{suffix}"
        ws = f"ws-acc-{suffix}"
        admin_tok = await _seed(ws, tid, f"admin-{suffix}", ws_role="workspace_admin",
                                tenant_role="tenant_admin")

        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            before = await ac.get(
                "/api/v1/admin/workspaces",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            assert before.status_code == 200
            by_id = {w["id"]: w for w in before.json()}
            assert by_id[ws]["agent_count"] == 0  # no agents yet

            # Create two agents
            await _create_agent_via_api(app, admin_tok, ws, name="A1")
            await _create_agent_via_api(app, admin_tok, ws, name="A2")

            after = await ac.get(
                "/api/v1/admin/workspaces",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            by_id_after = {w["id"]: w for w in after.json()}
            assert by_id_after[ws]["agent_count"] == 2

    @pytest.mark.asyncio
    async def test_admin_count_isolated_per_workspace(self, app):
        """Agents in ws-B must not inflate ws-A's agent_count."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-aci-{suffix}"
        ws_a = f"ws-a-{suffix}"
        ws_b = f"ws-b-{suffix}"
        admin_tok = await _seed(ws_a, tid, f"admin-a-{suffix}", ws_role="workspace_admin",
                                tenant_role="tenant_admin")
        # ws_b created by admin (tenant_admin short-circuits ownership)
        async with async_session() as session:
            session.add(Workspace(id=ws_b, tenant_id=tid, name="WS B"))
            await session.commit()
        tok_b = _token(f"admin-a-{suffix}", tid, role="tenant_admin")
        await _create_agent_via_api(app, tok_b, ws_b, name="Only in B")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/admin/workspaces",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            by_id = {w["id"]: w for w in resp.json()}
            assert by_id[ws_a]["agent_count"] == 0
            assert by_id[ws_b]["agent_count"] == 1
