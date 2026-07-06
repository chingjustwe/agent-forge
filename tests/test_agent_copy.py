"""P3-2: 跨 workspace 资源复制（agent config）。

tenant_admin 可把 agent config 从一个 workspace 复制到另一个，产生新 ID，
记录 AuditLog。非 tenant_admin 403；跨 workspace 源 404；目标不存在 404。
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


async def _create_agent_via_api(
    app, token: str, ws_id: str, name: str = "Source Agent", config: dict | None = None,
):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.post(
            f"/api/v1/workspaces/{ws_id}/agents",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": name, "framework": "direct_llm", "config": config or {"model": "gpt-4"}},
        )


class TestCopyAgentTo:
    @pytest.mark.asyncio
    async def test_tenant_admin_can_copy(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-cp-{suffix}"
        ws_src = f"ws-src-{suffix}"
        ws_dst = f"ws-dst-{suffix}"
        admin_tok = await _seed(ws_src, tid, f"admin-{suffix}", ws_role="workspace_admin",
                                tenant_role="tenant_admin")
        # Create destination workspace (tenant_admin doesn't need membership)
        async with async_session() as session:
            session.add(Workspace(id=ws_dst, tenant_id=tid, name="Dst"))
            await session.commit()

        created = (await _create_agent_via_api(app, admin_tok, ws_src, name="MyAgent")).json()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{ws_src}/agents/{created['id']}/copy-to/{ws_dst}",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["id"] != created["id"]  # new ID
        assert body["workspace_id"] == ws_dst
        assert body["name"] == "MyAgent"  # same name
        assert body["framework"] == "direct_llm"
        assert body["config"] == {"model": "gpt-4"}
        assert body["created_by"] == f"admin-{suffix}"

    @pytest.mark.asyncio
    async def test_copy_produces_independent_record(self, app):
        """Modifying the source agent must not affect the copy."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-cp-ind-{suffix}"
        ws_src = f"ws-src-{suffix}"
        ws_dst = f"ws-dst-{suffix}"
        admin_tok = await _seed(ws_src, tid, f"admin-{suffix}", ws_role="workspace_admin",
                                tenant_role="tenant_admin")
        async with async_session() as session:
            session.add(Workspace(id=ws_dst, tenant_id=tid, name="Dst"))
            await session.commit()

        created = (await _create_agent_via_api(
            app, admin_tok, ws_src, name="Orig", config={"model": "gpt-4", "temp": 0.7}
        )).json()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            copy_resp = await ac.post(
                f"/api/v1/workspaces/{ws_src}/agents/{created['id']}/copy-to/{ws_dst}",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            assert copy_resp.status_code == 201
            copy_id = copy_resp.json()["id"]

            # Mutate the source agent
            upd = await ac.patch(
                f"/api/v1/workspaces/{ws_src}/agents/{created['id']}",
                headers={"Authorization": f"Bearer {admin_tok}"},
                json={"name": "Renamed", "config": {"model": "claude"}},
            )
            assert upd.status_code == 200

            # Fetch the copy from ws_dst — should be unchanged
            got = await ac.get(
                f"/api/v1/workspaces/{ws_dst}/agents/{copy_id}",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
        assert got.status_code == 200
        body = got.json()
        assert body["name"] == "Orig"  # unchanged
        assert body["config"] == {"model": "gpt-4", "temp": 0.7}

    @pytest.mark.asyncio
    async def test_workspace_admin_forbidden(self, app):
        """workspace_admin (not tenant_admin) cannot copy across workspaces."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-cp-wa-{suffix}"
        ws_src = f"ws-src-{suffix}"
        ws_dst = f"ws-dst-{suffix}"
        # workspace_admin tenant_role can create agents but cannot copy (needs admin:workspaces:write)
        tok = await _seed(ws_src, tid, f"wa-{suffix}", ws_role="workspace_admin",
                          tenant_role="workspace_admin")
        async with async_session() as session:
            session.add(Workspace(id=ws_dst, tenant_id=tid, name="Dst"))
            await session.commit()

        created = (await _create_agent_via_api(app, tok, ws_src)).json()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{ws_src}/agents/{created['id']}/copy-to/{ws_dst}",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_source_cross_workspace_404(self, app):
        """Source agent_id from another workspace must 404 (not leak)."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-cp-xw-{suffix}"
        ws_a = f"ws-a-{suffix}"
        ws_b = f"ws-b-{suffix}"
        admin_tok = await _seed(ws_a, tid, f"admin-{suffix}", ws_role="workspace_admin",
                                tenant_role="tenant_admin")
        async with async_session() as session:
            session.add(Workspace(id=ws_b, tenant_id=tid, name="B"))
            await session.commit()
        created = (await _create_agent_via_api(app, admin_tok, ws_a)).json()

        # Try to copy via ws_b's path (agent lives in ws_a)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{ws_b}/agents/{created['id']}/copy-to/{ws_a}",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_target_workspace_cross_tenant_404(self, app):
        """Target workspace in a different tenant must 404."""
        suffix = _uuid.uuid4().hex[:8]
        tid_a = f"t-a-{suffix}"
        tid_b = f"t-b-{suffix}"
        ws_src = f"ws-src-{suffix}"
        ws_dst = f"ws-dst-{suffix}"  # belongs to tid_b
        admin_tok = await _seed(ws_src, tid_a, f"admin-{suffix}", ws_role="workspace_admin",
                                tenant_role="tenant_admin")
        async with async_session() as session:
            session.add(Tenant(id=tid_b, name="TB", domain=f"{tid_b}.test"))
            session.add(Workspace(id=ws_dst, tenant_id=tid_b, name="Dst"))
            await session.commit()

        created = (await _create_agent_via_api(app, admin_tok, ws_src)).json()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{ws_src}/agents/{created['id']}/copy-to/{ws_dst}",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_copy_writes_audit_log(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-cp-aud-{suffix}"
        ws_src = f"ws-src-{suffix}"
        ws_dst = f"ws-dst-{suffix}"
        admin_tok = await _seed(ws_src, tid, f"admin-{suffix}", ws_role="workspace_admin",
                                tenant_role="tenant_admin")
        async with async_session() as session:
            session.add(Workspace(id=ws_dst, tenant_id=tid, name="Dst"))
            await session.commit()

        created = (await _create_agent_via_api(app, admin_tok, ws_src, name="Audited")).json()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/workspaces/{ws_src}/agents/{created['id']}/copy-to/{ws_dst}",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
        assert resp.status_code == 201
        new_id = resp.json()["id"]

        async with async_session() as session:
            rows = (await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "agent.copy",
                    AuditLog.target_id == new_id,
                )
            )).scalars().all()
        assert len(rows) == 1
        log = rows[0]
        assert log.target_type == "agent"
        assert log.workspace_id == ws_dst  # logged against the destination
        details = log.details
        assert details["source_agent_id"] == created["id"]
        assert details["source_workspace_id"] == ws_src
        assert details["target_workspace_id"] == ws_dst

    @pytest.mark.asyncio
    async def test_copy_preserves_framework_and_config(self, app):
        """Copy must replicate framework + config exactly (deep copy)."""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-cp-fw-{suffix}"
        ws_src = f"ws-src-{suffix}"
        ws_dst = f"ws-dst-{suffix}"
        admin_tok = await _seed(ws_src, tid, f"admin-{suffix}", ws_role="workspace_admin",
                                tenant_role="tenant_admin")
        async with async_session() as session:
            session.add(Workspace(id=ws_dst, tenant_id=tid, name="Dst"))
            await session.commit()

        # Create an agent with adk framework + nested config
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            created = (await ac.post(
                f"/api/v1/workspaces/{ws_src}/agents",
                headers={"Authorization": f"Bearer {admin_tok}"},
                json={
                    "name": "ADK Agent",
                    "framework": "deepagents",
                    "config": {"model": "gpt-4", "tools": ["search", "calc"], "nested": {"a": 1}},
                },
            )).json()

            resp = await ac.post(
                f"/api/v1/workspaces/{ws_src}/agents/{created['id']}/copy-to/{ws_dst}",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["framework"] == "deepagents"
        assert body["config"] == {"model": "gpt-4", "tools": ["search", "calc"], "nested": {"a": 1}}
