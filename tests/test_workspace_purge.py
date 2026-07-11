"""P3-3: 软删除 workspace 数据保留（手动 purge 接口）。

archived workspace 的数据保留可查询；tenant_admin 可手动 purge 已 archived
的 workspace 及其关联数据。需要二次确认（workspace_name 匹配）。
"""
import uuid as _uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import (
    AgentConfig,
    ApiKey,
    AuditLog,
    ChatMessage,
    ChatSession,
    OTelSettings,
    QuotaUsage,
    RequestLog,
    Tenant,
    User,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
)


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


def _token(user_id: str, tenant_id: str, role: str = "tenant_admin") -> str:
    return create_jwt({
        "id": user_id,
        "sub": user_id,
        "tenant_id": tenant_id,
        "email": f"{user_id}@test.com",
        "role": role,
    })


async def _seed_archived_workspace(
    tenant_id: str,
    ws_id: str,
    ws_name: str,
    user_id: str,
    archived: int = 1,
    tenant_role: str = "tenant_admin",
) -> str:
    """Seed tenant + an (archived) workspace + user (tenant_admin). Returns JWT.

    Also seeds one of each associated row type so purge can be verified to
    delete them: WorkspaceMember, ChatSession, ChatMessage, AgentConfig,
    ApiKey, OTelSettings, QuotaUsage, RequestLog, WorkspaceInvitation.
    """
    async with async_session() as session:
        if not await session.get(Tenant, tenant_id):
            session.add(Tenant(id=tenant_id, name="T", domain=f"{tenant_id}.test"))
            await session.flush()
        if not await session.get(Workspace, ws_id):
            session.add(
                Workspace(
                    id=ws_id,
                    tenant_id=tenant_id,
                    name=ws_name,
                    archived=archived,
                )
            )
            await session.flush()
        if not await session.get(User, user_id):
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=f"{user_id}@test.com",
                    name=user_id,
                    role=tenant_role,
                )
            )
            await session.flush()
        # WorkspaceMember (note: archive_workspace requires members removed,
        # but for purge we test data leftover directly so it's fine to add)
        if not await session.get(WorkspaceMember, (ws_id, user_id)):
            session.add(
                WorkspaceMember(workspace_id=ws_id, user_id=user_id, role="member")
            )
        # ChatSession + ChatMessage
        session.add(
            ChatSession(id=f"cs-{ws_id}", workspace_id=ws_id, owner_id=user_id)
        )
        await session.flush()
        session.add(
            ChatMessage(id=f"cm-{ws_id}", session_id=f"cs-{ws_id}", role="user", content="hi")
        )
        # AgentConfig
        session.add(
            AgentConfig(
                id=f"ac-{ws_id}",
                workspace_id=ws_id,
                name="A",
                framework="deepagents",
                config={},
                created_by=user_id,
            )
        )
        # ApiKey
        session.add(
            ApiKey(
                id=f"ak-{ws_id}",
                workspace_id=ws_id,
                name="K",
                key_prefix="ap_xxxxxx",
                key_hash=f"hash-{ws_id}",
                created_by=user_id,
            )
        )
        # OTelSettings
        session.add(OTelSettings(workspace_id=ws_id, enabled=0))
        # QuotaUsage
        session.add(
            QuotaUsage(workspace_id=ws_id, date="2026-07-04", tokens_used=10, cost=0.0)
        )
        # RequestLog
        session.add(
            RequestLog(
                id=f"rl-{ws_id}",
                trace_id=f"tr-{ws_id}",
                user_id=user_id,
                workspace_id=ws_id,
                tenant_id=tenant_id,
            )
        )
        # WorkspaceInvitation
        session.add(
            WorkspaceInvitation(
                id=f"wi-{ws_id}",
                workspace_id=ws_id,
                token=f"tok-{ws_id}",
                invited_by=user_id,
                expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            )
        )
        await session.commit()
    return _token(user_id, tenant_id, role=tenant_role)


class TestPurgeWorkspace:
    @pytest.mark.asyncio
    async def test_purge_requires_archived(self, app):
        """未 archive 的 workspace purge 返回 409。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-pa-{suffix}"
        ws = f"ws-pa-{suffix}"
        tok = await _seed_archived_workspace(
            tid, ws, "MyWS", f"u-{suffix}", archived=0,
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.request(
                "DELETE",
                f"/api/v1/admin/workspaces/{ws}/purge",
                headers={"Authorization": f"Bearer {tok}"},
                json={"purge_confirm": "MyWS"},
            )
        assert resp.status_code == 409
        assert "archive" in resp.json()["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_tenant_admin_can_purge_archived(self, app):
        """tenant_admin 可 purge 已 archived 的 workspace。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-pb-{suffix}"
        ws = f"ws-pb-{suffix}"
        tok = await _seed_archived_workspace(tid, ws, "PurgeMe", f"u-{suffix}")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.request(
                "DELETE",
                f"/api/v1/admin/workspaces/{ws}/purge",
                headers={"Authorization": f"Bearer {tok}"},
                json={"purge_confirm": "PurgeMe"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["purged"] is True
        assert body["workspace_id"] == ws

    @pytest.mark.asyncio
    async def test_purge_deletes_workspace(self, app):
        """purge 后 workspace 不存在。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-pc-{suffix}"
        ws = f"ws-pc-{suffix}"
        tok = await _seed_archived_workspace(tid, ws, "Gone", f"u-{suffix}")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.request(
                "DELETE",
                f"/api/v1/admin/workspaces/{ws}/purge",
                headers={"Authorization": f"Bearer {tok}"},
                json={"purge_confirm": "Gone"},
            )
            assert resp.status_code == 200
        async with async_session() as session:
            assert await session.get(Workspace, ws) is None

    @pytest.mark.asyncio
    async def test_purge_deletes_associated_data(self, app):
        """purge 后关联数据（sessions/messages/agents/members）都删除。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-pd-{suffix}"
        ws = f"ws-pd-{suffix}"
        uid = f"u-{suffix}"
        tok = await _seed_archived_workspace(tid, ws, "Cascade", uid)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.request(
                "DELETE",
                f"/api/v1/admin/workspaces/{ws}/purge",
                headers={"Authorization": f"Bearer {tok}"},
                json={"purge_confirm": "Cascade"},
            )
            assert resp.status_code == 200
        async with async_session() as session:
            # Workspace gone
            assert await session.get(Workspace, ws) is None
            # WorkspaceMember gone
            assert await session.get(WorkspaceMember, (ws, uid)) is None
            # ChatSession + ChatMessage gone
            assert await session.get(ChatSession, f"cs-{ws}") is None
            assert await session.get(ChatMessage, f"cm-{ws}") is None
            # AgentConfig gone
            assert await session.get(AgentConfig, f"ac-{ws}") is None
            # ApiKey gone
            assert await session.get(ApiKey, f"ak-{ws}") is None
            # OTelSettings gone
            assert await session.get(OTelSettings, ws) is None
            # QuotaUsage gone
            assert await session.get(QuotaUsage, (ws, "2026-07-04")) is None
            # RequestLog gone
            assert await session.get(RequestLog, f"rl-{ws}") is None
            # WorkspaceInvitation gone
            assert await session.get(WorkspaceInvitation, f"wi-{ws}") is None

    @pytest.mark.asyncio
    async def test_purge_missing_confirmation_returns_400(self, app):
        """缺少确认 body 返回 400。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-pe-{suffix}"
        ws = f"ws-pe-{suffix}"
        tok = await _seed_archived_workspace(tid, ws, "NoBody", f"u-{suffix}")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # No body at all
            resp = await ac.request(
                "DELETE",
                f"/api/v1/admin/workspaces/{ws}/purge",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_purge_wrong_confirmation_returns_400(self, app):
        """确认 name 不匹配返回 400。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-pf-{suffix}"
        ws = f"ws-pf-{suffix}"
        tok = await _seed_archived_workspace(tid, ws, "RightName", f"u-{suffix}")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.request(
                "DELETE",
                f"/api/v1/admin/workspaces/{ws}/purge",
                headers={"Authorization": f"Bearer {tok}"},
                json={"purge_confirm": "WrongName"},
            )
        assert resp.status_code == 400
        # Workspace still exists (not purged)
        async with async_session() as session:
            assert await session.get(Workspace, ws) is not None

    @pytest.mark.asyncio
    async def test_purge_non_tenant_admin_forbidden(self, app):
        """非 tenant_admin 403。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-pg-{suffix}"
        ws = f"ws-pg-{suffix}"
        # Seed as tenant_admin first to set up data
        await _seed_archived_workspace(tid, ws, "Member", f"admin-{suffix}")
        # Add a workspace_admin (tenant_role=member) user
        async with async_session() as session:
            session.add(
                User(
                    id=f"wa-{suffix}",
                    tenant_id=tid,
                    email=f"wa-{suffix}@test.com",
                    name=f"wa-{suffix}",
                    role="member",
                )
            )
            await session.commit()
        tok = _token(f"wa-{suffix}", tid, role="member")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.request(
                "DELETE",
                f"/api/v1/admin/workspaces/{ws}/purge",
                headers={"Authorization": f"Bearer {tok}"},
                json={"purge_confirm": "Member"},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_purge_writes_audit_log(self, app):
        """purge 后写 workspace.purge AuditLog。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-ph-{suffix}"
        ws = f"ws-ph-{suffix}"
        tok = await _seed_archived_workspace(tid, ws, "Audited", f"u-{suffix}")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.request(
                "DELETE",
                f"/api/v1/admin/workspaces/{ws}/purge",
                headers={"Authorization": f"Bearer {tok}"},
                json={"purge_confirm": "Audited"},
            )
            assert resp.status_code == 200
        # AuditLog is written before the workspace row is deleted, so it
        # survives (it's in the audit_logs table, scoped by tenant_id).
        async with async_session() as session:
            from sqlalchemy import select
            rows = (await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "workspace.purge",
                    AuditLog.target_id == ws,
                )
            )).scalars().all()
        assert len(rows) == 1
        assert rows[0].target_type == "workspace"
        assert rows[0].tenant_id == tid
