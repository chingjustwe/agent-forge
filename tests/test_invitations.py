"""Tests for P2-1: workspace invitation links.

Covers model definition, create/list/revoke routes, public token preview,
accept flow (success / idempotent / expired / accepted / email-mismatch /
generic link), cross-workspace isolation, and re-invite-invalidates-old.
"""
import uuid as _uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import (
    AuditLog,
    Tenant,
    User,
    Workspace,
    WorkspaceInvitation,
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


async def _seed_workspace_with_admin(
    ws_id: str,
    tenant_id: str,
    admin_id: str,
    admin_role: str = "workspace_admin",
    tenant_role: str | None = None,
    email: str | None = None,
) -> str:
    """Seed tenant + workspace + user + WorkspaceMember(admin). Returns JWT."""
    if tenant_role is None:
        tenant_role = admin_role
    async with async_session() as session:
        if not await session.get(Tenant, tenant_id):
            session.add(Tenant(id=tenant_id, name="T", domain=f"{tenant_id}.test"))
            await session.flush()
        if not await session.get(Workspace, ws_id):
            session.add(Workspace(id=ws_id, tenant_id=tenant_id, name=f"WS {ws_id}"))
            await session.flush()
        if not await session.get(User, admin_id):
            session.add(
                User(
                    id=admin_id,
                    tenant_id=tenant_id,
                    email=email or f"{admin_id}@test.com",
                    name=admin_id,
                    role=tenant_role,
                )
            )
            await session.flush()
        if not await session.get(WorkspaceMember, (ws_id, admin_id)):
            session.add(
                WorkspaceMember(workspace_id=ws_id, user_id=admin_id, role=admin_role)
            )
        await session.commit()
    return _token(admin_id, tenant_id, role=tenant_role, email=email)


async def _seed_user(
    tenant_id: str,
    user_id: str,
    email: str | None = None,
    name: str | None = None,
    tenant_role: str = "member",
) -> str:
    """Seed a standalone User (no workspace membership). Returns JWT."""
    async with async_session() as session:
        if not await session.get(User, user_id):
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=email or f"{user_id}@test.com",
                    name=name or user_id,
                    role=tenant_role,
                )
            )
            await session.commit()
    return _token(user_id, tenant_id, role=tenant_role, email=email)


# ---------------------------------------------------------------------------
# 1. Model definition
# ---------------------------------------------------------------------------
class TestWorkspaceInvitationModel:
    def test_tablename(self):
        assert WorkspaceInvitation.__tablename__ == "workspace_invitations"

    def test_fields_exist(self):
        cols = {c.name for c in WorkspaceInvitation.__table__.columns}
        assert {
            "id",
            "workspace_id",
            "email",
            "role",
            "token",
            "invited_by",
            "expires_at",
            "accepted_at",
            "accepted_by",
            "created_at",
        } <= cols

    def test_email_nullable(self):
        assert WorkspaceInvitation.__table__.columns["email"].nullable is True

    def test_accepted_at_nullable(self):
        assert WorkspaceInvitation.__table__.columns["accepted_at"].nullable is True

    def test_token_unique(self):
        col = WorkspaceInvitation.__table__.columns["token"]
        assert col.unique is True

    def test_default_role_is_member(self):
        col = WorkspaceInvitation.__table__.columns["role"]
        assert col.default is not None
        arg = col.default.arg
        if callable(arg):
            arg = arg(None)
        assert arg == "member"


# ---------------------------------------------------------------------------
# 2. Create invitation
# ---------------------------------------------------------------------------
class TestCreateInvitation:
    @pytest.mark.asyncio
    async def test_create_as_workspace_admin(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-ci-{suffix}"
        tid = f"t-ci-{suffix}"
        token = await _seed_workspace_with_admin(ws_id, tid, f"admin-{suffix}")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/workspaces/{ws_id}/invitations",
                json={"email": "invitee@test.com", "role": "member"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["workspace_id"] == ws_id
            assert body["email"] == "invitee@test.com"
            assert body["role"] == "member"
            assert body["token"]
            assert body["accepted_at"] is None
            assert body["is_accepted"] is False
            assert body["is_expired"] is False

    @pytest.mark.asyncio
    async def test_create_as_workspace_admin(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-co-{suffix}"
        tid = f"t-co-{suffix}"
        token = await _seed_workspace_with_admin(
            ws_id, tid, f"owner-{suffix}", admin_role="workspace_admin"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/workspaces/{ws_id}/invitations",
                json={"role": "member"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 201
            assert resp.json()["email"] is None  # generic link

    @pytest.mark.asyncio
    async def test_create_as_member_forbidden(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-cm-{suffix}"
        tid = f"t-cm-{suffix}"
        token = await _seed_workspace_with_admin(
            ws_id, tid, f"mem-{suffix}", admin_role="member"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/workspaces/{ws_id}/invitations",
                json={"role": "member"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_default_expires_7_days(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-cd-{suffix}"
        tid = f"t-cd-{suffix}"
        token = await _seed_workspace_with_admin(ws_id, tid, f"admin-{suffix}")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/workspaces/{ws_id}/invitations",
                json={"role": "member"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 201
            expires_at = resp.json()["expires_at"]
            # SQLite drops tzinfo on round-trip; reattach UTC if missing.
            expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            delta = expires_dt - datetime.now(timezone.utc)
            assert timedelta(days=6, hours=23) < delta < timedelta(days=7, minutes=5)

    @pytest.mark.asyncio
    async def test_create_invalid_role_rejected(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-cr-{suffix}"
        tid = f"t-cr-{suffix}"
        token = await _seed_workspace_with_admin(ws_id, tid, f"admin-{suffix}")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/workspaces/{ws_id}/invitations",
                json={"role": "tenant_admin"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_writes_audit_log(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-ca-{suffix}"
        tid = f"t-ca-{suffix}"
        admin_id = f"admin-{suffix}"
        token = await _seed_workspace_with_admin(ws_id, tid, admin_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/workspaces/{ws_id}/invitations",
                json={"email": "audit-target@test.com", "role": "member"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 201
            inv_id = resp.json()["id"]

        async with async_session() as session:
            rows = (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "invitation.create")
                )
            ).scalars().all()
            assert any(r.target_id == inv_id for r in rows)


# ---------------------------------------------------------------------------
# 3. List invitations
# ---------------------------------------------------------------------------
class TestListInvitations:
    @pytest.mark.asyncio
    async def test_list_as_admin(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-li-{suffix}"
        tid = f"t-li-{suffix}"
        token = await _seed_workspace_with_admin(ws_id, tid, f"admin-{suffix}")

        # Seed two invitations directly in the DB.
        async with async_session() as session:
            session.add_all([
                WorkspaceInvitation(
                    workspace_id=ws_id,
                    email="a@test.com",
                    role="member",
                    token=f"tok-a-{suffix}",
                    invited_by=f"admin-{suffix}",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                ),
                WorkspaceInvitation(
                    workspace_id=ws_id,
                    email="b@test.com",
                    role="workspace_admin",
                    token=f"tok-b-{suffix}",
                    invited_by=f"admin-{suffix}",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                ),
            ])
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/invitations",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            items = resp.json()
            assert len(items) == 2
            emails = {i["email"] for i in items}
            assert emails == {"a@test.com", "b@test.com"}

    @pytest.mark.asyncio
    async def test_list_as_member_forbidden(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-lm-{suffix}"
        tid = f"t-lm-{suffix}"
        token = await _seed_workspace_with_admin(
            ws_id, tid, f"mem-{suffix}", admin_role="member"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/invitations",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_list_cross_workspace_isolation(self, app):
        """Invitations from workspace A must not appear in workspace B's list."""
        suffix = _uuid.uuid4().hex[:8]
        ws_a = f"ws-lA-{suffix}"
        ws_b = f"ws-lB-{suffix}"
        tid = f"t-lx-{suffix}"
        token = await _seed_workspace_with_admin(ws_a, tid, f"admin-{suffix}")

        async with async_session() as session:
            session.add(Workspace(id=ws_b, tenant_id=tid, name="WS B"))
            session.add(
                WorkspaceInvitation(
                    workspace_id=ws_a,
                    email="in-a@test.com",
                    role="member",
                    token=f"tok-a-{suffix}",
                    invited_by=f"admin-{suffix}",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                )
            )
            session.add(
                WorkspaceInvitation(
                    workspace_id=ws_b,
                    email="in-b@test.com",
                    role="member",
                    token=f"tok-b-{suffix}",
                    invited_by=f"admin-{suffix}",
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                )
            )
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_a}/invitations",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            items = resp.json()
            assert len(items) == 1
            assert items[0]["workspace_id"] == ws_a
            assert items[0]["email"] == "in-a@test.com"


# ---------------------------------------------------------------------------
# 4. Revoke invitation
# ---------------------------------------------------------------------------
class TestRevokeInvitation:
    @pytest.mark.asyncio
    async def test_revoke_as_admin(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-rv-{suffix}"
        tid = f"t-rv-{suffix}"
        admin_id = f"admin-{suffix}"
        token = await _seed_workspace_with_admin(ws_id, tid, admin_id)

        async with async_session() as session:
            inv = WorkspaceInvitation(
                workspace_id=ws_id,
                email="x@test.com",
                role="member",
                token=f"tok-{suffix}",
                invited_by=admin_id,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            session.add(inv)
            await session.commit()
            await session.refresh(inv)
            inv_id = inv.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/workspaces/{ws_id}/invitations/{inv_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 204

        async with async_session() as session:
            assert await session.get(WorkspaceInvitation, inv_id) is None

    @pytest.mark.asyncio
    async def test_revoke_member_forbidden(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-rm-{suffix}"
        tid = f"t-rm-{suffix}"
        admin_id = f"admin-{suffix}"
        member_id = f"mem-{suffix}"
        # Admin creates ws; member is added as plain member.
        await _seed_workspace_with_admin(ws_id, tid, admin_id)
        member_token = _token(member_id, tid)
        async with async_session() as session:
            session.add(
                User(
                    id=member_id,
                    tenant_id=tid,
                    email=f"{member_id}@test.com",
                    name=member_id,
                    role="member",
                )
            )
            session.add(
                WorkspaceMember(workspace_id=ws_id, user_id=member_id, role="member")
            )
            inv = WorkspaceInvitation(
                workspace_id=ws_id,
                email="x@test.com",
                role="member",
                token=f"tok-{suffix}",
                invited_by=admin_id,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            session.add(inv)
            await session.commit()
            await session.refresh(inv)
            inv_id = inv.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/workspaces/{ws_id}/invitations/{inv_id}",
                headers={"Authorization": f"Bearer {member_token}"},
            )
            assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 5. Public token preview
# ---------------------------------------------------------------------------
class TestGetInvitationByToken:
    @pytest.mark.asyncio
    async def test_public_preview_no_auth(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-gp-{suffix}"
        tid = f"t-gp-{suffix}"
        admin_id = f"admin-{suffix}"
        await _seed_workspace_with_admin(ws_id, tid, admin_id)

        async with async_session() as session:
            inv = WorkspaceInvitation(
                workspace_id=ws_id,
                email="invitee@test.com",
                role="member",
                token=f"tok-public-{suffix}",
                invited_by=admin_id,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            session.add(inv)
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/invitations/tok-public-{suffix}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["workspace_id"] == ws_id
            assert body["workspace_name"] == f"WS {ws_id}"
            assert body["email"] == "invitee@test.com"
            assert body["role"] == "member"
            # Public preview must not echo the token or inviter id.
            assert "token" not in body
            assert "invited_by" not in body

    @pytest.mark.asyncio
    async def test_preview_not_found(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/invitations/no-such-token")
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 6. Accept invitation
# ---------------------------------------------------------------------------
class TestAcceptInvitation:
    @pytest.mark.asyncio
    async def test_accept_success_creates_member(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-ac-{suffix}"
        tid = f"t-ac-{suffix}"
        admin_id = f"admin-{suffix}"
        await _seed_workspace_with_admin(ws_id, tid, admin_id)

        invitee_id = f"invitee-{suffix}"
        invitee_email = f"{invitee_id}@test.com"
        invitee_token = await _seed_user(tid, invitee_id, email=invitee_email)

        async with async_session() as session:
            inv = WorkspaceInvitation(
                workspace_id=ws_id,
                email=invitee_email,
                role="member",
                token=f"tok-acc-{suffix}",
                invited_by=admin_id,
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            )
            session.add(inv)
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/invitations/tok-acc-{suffix}/accept",
                headers={"Authorization": f"Bearer {invitee_token}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["workspace_id"] == ws_id
            assert body["role"] == "member"
            assert body["already_member"] is False

        # Verify WorkspaceMember row created and invitation marked accepted.
        async with async_session() as session:
            member = await session.get(WorkspaceMember, (ws_id, invitee_id))
            assert member is not None
            assert member.role == "member"
            inv = (
                await session.execute(
                    select(WorkspaceInvitation).where(
                        WorkspaceInvitation.token == f"tok-acc-{suffix}"
                    )
                )
            ).scalar_one()
            assert inv.accepted_at is not None
            assert inv.accepted_by == invitee_id

    @pytest.mark.asyncio
    async def test_accept_already_member_idempotent(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-ai-{suffix}"
        tid = f"t-ai-{suffix}"
        admin_id = f"admin-{suffix}"
        await _seed_workspace_with_admin(ws_id, tid, admin_id)

        # Invitee is already a member with role workspace_admin.
        invitee_id = f"invitee-{suffix}"
        invitee_email = f"{invitee_id}@test.com"
        invitee_token = await _seed_user(tid, invitee_id, email=invitee_email)
        async with async_session() as session:
            session.add(
                WorkspaceMember(
                    workspace_id=ws_id, user_id=invitee_id, role="workspace_admin"
                )
            )
            session.add(
                WorkspaceInvitation(
                    workspace_id=ws_id,
                    email=invitee_email,
                    role="member",  # invitation offers member, but existing role wins
                    token=f"tok-ai-{suffix}",
                    invited_by=admin_id,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                )
            )
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/invitations/tok-ai-{suffix}/accept",
                headers={"Authorization": f"Bearer {invitee_token}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["already_member"] is True
            # Existing role is preserved (idempotent — no downgrade).
            assert body["role"] == "workspace_admin"

    @pytest.mark.asyncio
    async def test_accept_already_accepted_returns_410(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-aa-{suffix}"
        tid = f"t-aa-{suffix}"
        admin_id = f"admin-{suffix}"
        await _seed_workspace_with_admin(ws_id, tid, admin_id)

        invitee_id = f"invitee-{suffix}"
        invitee_email = f"{invitee_id}@test.com"
        invitee_token = await _seed_user(tid, invitee_id, email=invitee_email)

        async with async_session() as session:
            session.add(
                WorkspaceInvitation(
                    workspace_id=ws_id,
                    email=invitee_email,
                    role="member",
                    token=f"tok-aa-{suffix}",
                    invited_by=admin_id,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                    accepted_at=datetime.now(timezone.utc) - timedelta(hours=1),
                    accepted_by="someone-else",
                )
            )
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/invitations/tok-aa-{suffix}/accept",
                headers={"Authorization": f"Bearer {invitee_token}"},
            )
            assert resp.status_code == 410

    @pytest.mark.asyncio
    async def test_accept_expired_returns_410(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-ae-{suffix}"
        tid = f"t-ae-{suffix}"
        admin_id = f"admin-{suffix}"
        await _seed_workspace_with_admin(ws_id, tid, admin_id)

        invitee_id = f"invitee-{suffix}"
        invitee_email = f"{invitee_id}@test.com"
        invitee_token = await _seed_user(tid, invitee_id, email=invitee_email)

        async with async_session() as session:
            session.add(
                WorkspaceInvitation(
                    workspace_id=ws_id,
                    email=invitee_email,
                    role="member",
                    token=f"tok-ae-{suffix}",
                    invited_by=admin_id,
                    expires_at=datetime.now(timezone.utc) - timedelta(days=1),
                )
            )
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/invitations/tok-ae-{suffix}/accept",
                headers={"Authorization": f"Bearer {invitee_token}"},
            )
            assert resp.status_code == 410
            assert "EXPIRED" in resp.json()["error"]["code"]

    @pytest.mark.asyncio
    async def test_accept_email_mismatch_returns_403(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-am-{suffix}"
        tid = f"t-am-{suffix}"
        admin_id = f"admin-{suffix}"
        await _seed_workspace_with_admin(ws_id, tid, admin_id)

        # Invitation is for alice@test.com.
        async with async_session() as session:
            session.add(
                WorkspaceInvitation(
                    workspace_id=ws_id,
                    email="alice@test.com",
                    role="member",
                    token=f"tok-am-{suffix}",
                    invited_by=admin_id,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                )
            )
            await session.commit()

        # Bob tries to accept.
        bob_token = await _seed_user(tid, f"bob-{suffix}", email="bob@test.com")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/invitations/tok-am-{suffix}/accept",
                headers={"Authorization": f"Bearer {bob_token}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_accept_generic_link_anyone(self, app):
        """email=None means any logged-in user can accept."""
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-ag-{suffix}"
        tid = f"t-ag-{suffix}"
        admin_id = f"admin-{suffix}"
        await _seed_workspace_with_admin(ws_id, tid, admin_id)

        async with async_session() as session:
            session.add(
                WorkspaceInvitation(
                    workspace_id=ws_id,
                    email=None,
                    role="member",
                    token=f"tok-ag-{suffix}",
                    invited_by=admin_id,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                )
            )
            await session.commit()

        carol_token = await _seed_user(tid, f"carol-{suffix}", email="carol@test.com")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/invitations/tok-ag-{suffix}/accept",
                headers={"Authorization": f"Bearer {carol_token}"},
            )
            assert resp.status_code == 200
            assert resp.json()["role"] == "member"

    @pytest.mark.asyncio
    async def test_accept_requires_auth(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/invitations/some-token/accept")
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_accept_not_found(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-nf-{suffix}"
        user_token = await _seed_user(tid, f"u-{suffix}")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/invitations/no-such-token/accept",
                headers={"Authorization": f"Bearer {user_token}"},
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_accept_revoked_returns_404(self, app):
        """After revoke (delete), accepting the old token must 404."""
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-ar-{suffix}"
        tid = f"t-ar-{suffix}"
        admin_id = f"admin-{suffix}"
        await _seed_workspace_with_admin(ws_id, tid, admin_id)

        async with async_session() as session:
            session.add(
                WorkspaceInvitation(
                    workspace_id=ws_id,
                    email=None,
                    role="member",
                    token=f"tok-ar-{suffix}",
                    invited_by=admin_id,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                )
            )
            await session.commit()
            # Simulate revoke by deleting.
            await session.execute(
                select(WorkspaceInvitation).where(
                    WorkspaceInvitation.token == f"tok-ar-{suffix}"
                )
            )
            inv = (
                await session.execute(
                    select(WorkspaceInvitation).where(
                        WorkspaceInvitation.token == f"tok-ar-{suffix}"
                    )
                )
            ).scalar_one()
            await session.delete(inv)
            await session.commit()

        user_token = await _seed_user(tid, f"u-{suffix}")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/invitations/tok-ar-{suffix}/accept",
                headers={"Authorization": f"Bearer {user_token}"},
            )
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 7. Re-invite invalidates old link
# ---------------------------------------------------------------------------
class TestReinviteInvalidatesOld:
    @pytest.mark.asyncio
    async def test_duplicate_email_invalidates_old(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-ri-{suffix}"
        tid = f"t-ri-{suffix}"
        admin_id = f"admin-{suffix}"
        token = await _seed_workspace_with_admin(ws_id, tid, admin_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # First invitation.
            resp1 = await client.post(
                f"/api/v1/workspaces/{ws_id}/invitations",
                json={"email": "dup@test.com", "role": "member"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp1.status_code == 201
            old_token = resp1.json()["token"]

            # Second invitation for the same email — should invalidate the first.
            resp2 = await client.post(
                f"/api/v1/workspaces/{ws_id}/invitations",
                json={"email": "dup@test.com", "role": "workspace_admin"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp2.status_code == 201
            new_token = resp2.json()["token"]
            assert new_token != old_token

        # Old token must no longer be previewable / acceptable.
        async with async_session() as session:
            old_inv = (
                await session.execute(
                    select(WorkspaceInvitation).where(
                        WorkspaceInvitation.token == old_token
                    )
                )
            ).scalar_one_or_none()
            assert old_inv is None
            new_inv = (
                await session.execute(
                    select(WorkspaceInvitation).where(
                        WorkspaceInvitation.token == new_token
                    )
                )
            ).scalar_one()
            assert new_inv.email == "dup@test.com"
            assert new_inv.role == "workspace_admin"

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/invitations/{old_token}")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_duplicate_generic_link_keeps_old(self, app):
        """Generic links (email=None) are NOT invalidated by a new generic link."""
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-rg-{suffix}"
        tid = f"t-rg-{suffix}"
        admin_id = f"admin-{suffix}"
        token = await _seed_workspace_with_admin(ws_id, tid, admin_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp1 = await client.post(
                f"/api/v1/workspaces/{ws_id}/invitations",
                json={"role": "member"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp1.status_code == 201
            old_token = resp1.json()["token"]

            resp2 = await client.post(
                f"/api/v1/workspaces/{ws_id}/invitations",
                json={"role": "member"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp2.status_code == 201
            new_token = resp2.json()["token"]

        # Both generic links should still exist.
        async with async_session() as session:
            for tok in (old_token, new_token):
                inv = (
                    await session.execute(
                        select(WorkspaceInvitation).where(
                            WorkspaceInvitation.token == tok
                        )
                    )
                ).scalar_one_or_none()
                assert inv is not None
