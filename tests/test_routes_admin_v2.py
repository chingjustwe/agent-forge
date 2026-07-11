import re
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import create_app
from src.gateway.auth.jwt import create_jwt

pytestmark = pytest.mark.asyncio


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def tenant_admin_token():
    """tenant_admin short-circuits — no WorkspaceMember row needed."""
    return create_jwt({
        "id": "admin-uuid",
        "sub": "admin-uuid",
        "tenant_id": "tenant-uuid",
        "email": "admin@test.com",
        "role": "tenant_admin",
    })


async def _seed_workspace_admin(
    ws_id: str = "ws-1",
    user_id: str = "owner-uuid",
    role: str = "workspace_admin",
    email: str = "owner@test.com",
) -> str:
    """Seed a workspace + WorkspaceMember(owner) and return a JWT."""
    from tests.conftest import setup_workspace_with_member
    from src.infra.db.session import get_db

    async for session in get_db():
        token = await setup_workspace_with_member(
            session,
            ws_id=ws_id,
            tenant_id="tenant-uuid",
            user_id=user_id,
            user_role=role,
            tenant_role=role,
            email=email,
            name=user_id,
        )
        break
    return token


@pytest.fixture
async def workspace_admin_token():
    return await _seed_workspace_admin()


@pytest.fixture
async def member_token():
    """A plain tenant member with no workspace membership — should be
    forbidden from workspace-level routes."""
    return create_jwt({
        "id": "member-uuid",
        "sub": "member-uuid",
        "tenant_id": "tenant-uuid",
        "email": "member@test.com",
        "role": "member",
    })


async def test_list_tenants_requires_tenant_admin(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/admin/tenants", headers={"Authorization": "Bearer invalid"})
        assert resp.status_code == 401


async def test_list_tenants_as_admin(app, tenant_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/admin/tenants", headers={"Authorization": f"Bearer {tenant_admin_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


async def test_update_tenant(app, tenant_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        list_resp = await ac.get("/api/v1/admin/tenants", headers={"Authorization": f"Bearer {tenant_admin_token}"})
        tenants = list_resp.json()
        assert len(tenants) > 0
        tid = tenants[0]["id"]

        resp = await ac.put(
            f"/api/v1/admin/tenants/{tid}",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
            json={"name": "Updated Corp", "domain": "updated.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Updated Corp"


async def test_update_tenant_not_found(app, tenant_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.put(
            "/api/v1/admin/tenants/nonexistent",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
            json={"name": "Nope"},
        )
        assert resp.status_code == 404


async def test_list_users(app, tenant_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {tenant_admin_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


async def test_list_users_with_search(app, tenant_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/admin/users?search=admin",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
        )
        assert resp.status_code == 200


async def test_list_users_requires_admin(app, member_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {member_token}"})
        assert resp.status_code == 403


async def test_update_user(app, tenant_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        users_resp = await ac.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {tenant_admin_token}"})
        users = users_resp.json()
        assert len(users) > 0
        uid = users[0]["id"]

        resp = await ac.put(
            f"/api/v1/admin/users/{uid}",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
            json={"role": "workspace_admin"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "workspace_admin"


async def test_delete_user(app, tenant_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        users_resp = await ac.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {tenant_admin_token}"})
        users = users_resp.json()
        assert len(users) > 0
        uid = users[0]["id"]

        resp = await ac.delete(
            f"/api/v1/admin/users/{uid}",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
        )
        assert resp.status_code == 204


async def test_invite_user(app, tenant_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/admin/users/invite",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
            json={"email": "newuser@test.com", "role": "member"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "newuser@test.com"
        assert data["role"] == "member"
        assert "temporary_password" not in data


async def test_invite_duplicate(app, tenant_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/admin/users/invite",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
            json={"email": "newuser@test.com", "role": "member"},
        )
        assert resp.status_code == 409


async def test_invite_creates_user_without_password(app, tenant_admin_token):
    """Invited user should be created with hashed_password=NULL."""
    from sqlalchemy import select
    from src.infra.db.engine import async_session
    from src.infra.db.models import InviteToken, User

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.post(
            "/api/v1/admin/users/invite",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
            json={"email": "no-pw@test.com", "role": "workspace_admin"},
        )

    async with async_session() as session:
        result = await session.execute(select(User).where(User.email == "no-pw@test.com"))
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.hashed_password is None
        assert user.role == "workspace_admin"

        result = await session.execute(select(InviteToken).where(InviteToken.user_id == user.id))
        invite = result.scalar_one_or_none()
        assert invite is not None
        assert invite.used_at is None
        assert invite.expires_at is not None


async def test_invite_validation_invalid_token(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/auth/invite?token=bogus-token")
        assert resp.status_code == 404


async def test_accept_invite_invalid_token(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/auth/accept-invite",
            json={"token": "bogus-token", "password": "newpass123", "name": "User"},
        )
        assert resp.status_code == 404


async def test_invite_full_flow(app, tenant_admin_token):
    """
    Full E2E: invite → validate → accept → login with assigned role.
    Mocks send_invite_email to capture the raw token from the invite URL.
    Single AsyncClient session to keep ASGITransport lifespan alive.
    """
    raw_tokens: list[str] = []

    def _capture_token(email: str, invite_url: str, expires_in_days: int = 7) -> None:
        match = re.search(r"token=([^&\s]+)", invite_url)
        if match:
            raw_tokens.append(match.group(1))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1) Invite
        with patch("src.gateway.routes.admin.send_invite_email", side_effect=_capture_token):
            resp = await ac.post(
                "/api/v1/admin/users/invite",
                headers={"Authorization": f"Bearer {tenant_admin_token}"},
                json={"email": "full-flow@test.com", "role": "workspace_admin"},
            )
            assert resp.status_code == 201

        assert len(raw_tokens) == 1
        token = raw_tokens[0]

        # 2) Validate the invite
        resp = await ac.get(f"/api/v1/auth/invite?token={token}")
        assert resp.status_code == 200
        info = resp.json()
        assert info["email"] == "full-flow@test.com"
        assert info["role"] == "workspace_admin"

        # 3) Accept the invite
        resp = await ac.post(
            "/api/v1/auth/accept-invite",
            json={"token": token, "password": "securePass1", "name": "Full Flow User"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "token" in body
        assert body["user"]["email"] == "full-flow@test.com"
        assert body["user"]["name"] == "Full Flow User"
        assert body["user"]["role"] == "workspace_admin"

        # 4) Log in with the new password
        resp = await ac.post(
            "/api/v1/auth/login",
            json={"email": "full-flow@test.com", "password": "securePass1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "token" in body
        assert body["user"]["role"] == "workspace_admin"

        # 5) Reusing the token should fail (marked used_at)
        resp = await ac.get(f"/api/v1/auth/invite?token={token}")
        assert resp.status_code == 410  # GONE — already used

        resp = await ac.post(
            "/api/v1/auth/accept-invite",
            json={"token": token, "password": "anotherPass", "name": "Hacker"},
        )
        assert resp.status_code == 410  # already used


async def test_invite_already_registered_user(app, tenant_admin_token):
    """Inviting an already-registered user (has password set) should be rejected."""
    from src.gateway.auth.password import hash_password
    from src.infra.db.engine import async_session
    from src.infra.db.models import User

    # Create an already-registered user directly
    async with async_session() as session:
        user = User(
            tenant_id="tenant-uuid",
            email="registered@test.com",
            name="Existing",
            role="member",
            hashed_password=hash_password("secret123"),
        )
        session.add(user)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/admin/users/invite",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
            json={"email": "registered@test.com", "role": "member"},
        )
        assert resp.status_code == 409


async def test_invite_expired_token(app, tenant_admin_token):
    """An expired invite token should return 410."""
    from datetime import datetime, timedelta, timezone
    import hashlib
    import secrets
    from src.infra.db.engine import async_session
    from src.infra.db.models import InviteToken, User

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    async with async_session() as session:
        user = User(
            tenant_id="tenant-uuid",
            email="expired-invite@test.com",
            name="Expired",
            role="member",
        )
        session.add(user)
        await session.flush()

        invite = InviteToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
        )
        session.add(invite)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/auth/invite?token={raw_token}")
        assert resp.status_code == 410
        assert resp.json()["error"]["code"] == "EXPIRED"

        resp = await ac.post(
            "/api/v1/auth/accept-invite",
            json={"token": raw_token, "password": "newpass", "name": "Late User"},
        )
        assert resp.status_code == 410
        assert resp.json()["error"]["code"] == "EXPIRED"


async def test_invite_after_delete(app, tenant_admin_token):
    """Deleting a user then re-inviting the same email should succeed (re-activate)."""
    mock_calls: list[str] = []

    def _capture(email: str, invite_url: str, expires_in_days: int = 7) -> None:
        mock_calls.append(email)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1) Create user via invite
        with patch("src.gateway.routes.admin.send_invite_email", side_effect=_capture):
            resp = await ac.post(
                "/api/v1/admin/users/invite",
                headers={"Authorization": f"Bearer {tenant_admin_token}"},
                json={"email": "delete-then-reinvite@test.com", "role": "member"},
            )
            assert resp.status_code == 201
            user_id = resp.json()["id"]

        # 2) Delete (soft-archive) the user
        resp = await ac.delete(
            f"/api/v1/admin/users/{user_id}",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
        )
        assert resp.status_code == 204

        # 3) Re-invite the same email — should succeed (re-activate archived user)
        with patch("src.gateway.routes.admin.send_invite_email", side_effect=_capture):
            resp = await ac.post(
                "/api/v1/admin/users/invite",
                headers={"Authorization": f"Bearer {tenant_admin_token}"},
                json={"email": "delete-then-reinvite@test.com", "role": "workspace_admin"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["email"] == "delete-then-reinvite@test.com"
            assert data["role"] == "workspace_admin"

        # 4) Ensure the user is in pending invitations (not active users yet)
        resp = await ac.get(
            "/api/v1/admin/pending-invitations",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
        )
        pending_emails = [p["email"] for p in resp.json()]
        assert "delete-then-reinvite@test.com" in pending_emails


async def test_list_workspaces_requires_tenant_admin(app, member_token):
    """P0-2: list_workspaces is now tenant_admin-only (was workspace_admin)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/admin/workspaces", headers={"Authorization": f"Bearer {member_token}"})
        assert resp.status_code == 403


async def test_list_workspaces_as_tenant_admin(app, tenant_admin_token):
    """tenant_admin can list all workspaces."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/admin/workspaces", headers={"Authorization": f"Bearer {tenant_admin_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


async def test_update_workspace_not_found(app, workspace_admin_token):
    """Workspace admin of nonexistent ws gets 404 (after RBAC passes via short-circuit...).

    Note: 'nonexistent' workspace has no WorkspaceMember row for this user,
    so RBAC returns 403 — but the test still asserts the request fails.
    Update: P0-2 requires workspace_admin membership. Since 'nonexistent'
    has no membership row, we expect 403 (not 404).
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.put(
            "/api/v1/admin/workspaces/nonexistent",
            headers={"Authorization": f"Bearer {workspace_admin_token}"},
            json={"name": "Updated WS"},
        )
        # 'nonexistent' has no WorkspaceMember row for this user → 403
        assert resp.status_code == 403


async def test_archive_workspace_not_found(app, workspace_admin_token):
    """Same as above — nonexistent workspace yields 403 (no membership)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.delete(
            "/api/v1/admin/workspaces/nonexistent",
            headers={"Authorization": f"Bearer {workspace_admin_token}"},
        )
        assert resp.status_code == 403


async def test_usage_as_tenant_admin(app, tenant_admin_token):
    """P0-2: /admin/usage is now tenant_admin-only (was workspace_admin)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/admin/usage", headers={"Authorization": f"Bearer {tenant_admin_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert "total_requests" in data
        assert "total_tokens" in data
        assert "total_cost" in data
        assert "by_workspace" in data
        # Input/output tokens tracked separately at top level
        assert "input_tokens" in data
        assert "output_tokens" in data


async def test_usage_bare_date_until_includes_full_day(app, tenant_admin_token):
    """Bare date 'until' should include records through end-of-day, not 00:00."""
    from src.infra.telemetry.collector import TelemetryCollector
    from datetime import date

    # Seed a request log with today's timestamp (mid-day)
    collector = TelemetryCollector()
    await collector.record_request(
        user_id="u-bare-date",
        ws_id="ws-bare-date",
        agent="a1",
        model="deepseek-v4-flash",
        status=200,
        duration_ms=10,
        tokens={"input": 10, "output": 20},
        tenant_id="tenant-uuid",
        cost=0.01,
    )

    today = date.today().isoformat()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/v1/admin/usage?since={today}&until={today}",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_requests"] >= 1


async def test_usage_with_workspace_id_param(app, tenant_admin_token):
    """workspace_id param filters results to just that workspace."""
    from src.infra.telemetry.collector import TelemetryCollector

    collector = TelemetryCollector()
    await collector.record_request(
        user_id="u-ws-filter",
        ws_id="ws-filter-target",
        agent="a1",
        model="deepseek-v4-flash",
        status=200,
        duration_ms=10,
        tokens={"input": 10, "output": 20},
        tenant_id="tenant-uuid",
        cost=0.01,
    )
    await collector.record_request(
        user_id="u-ws-filter",
        ws_id="ws-filter-other",
        agent="a1",
        model="deepseek-v4-flash",
        status=200,
        duration_ms=10,
        tokens={"input": 5, "output": 5},
        tenant_id="tenant-uuid",
        cost=0.01,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/admin/usage?workspace_id=ws-filter-target",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Only the target workspace should be in by_workspace
        ws_ids = [ws["workspace_id"] for ws in data["by_workspace"]]
        assert "ws-filter-target" in ws_ids
        assert "ws-filter-other" not in ws_ids


async def test_usage_enriched_with_quota_info(app, tenant_admin_token):
    """by_workspace items should include name, max_tokens_per_day, etc."""
    from src.infra.telemetry.collector import TelemetryCollector

    collector = TelemetryCollector()
    await collector.record_request(
        user_id="u-enrich",
        ws_id="ws-enrich",
        agent="a1",
        model="deepseek-v4-flash",
        status=200,
        duration_ms=10,
        tokens={"input": 10, "output": 20},
        tenant_id="tenant-uuid",
        cost=0.01,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/admin/usage",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Find the enriched workspace
        ws_enrich = next(
            (ws for ws in data["by_workspace"] if ws["workspace_id"] == "ws-enrich"),
            None,
        )
        if ws_enrich:
            assert "name" in ws_enrich
            assert "max_tokens_per_day" in ws_enrich
            assert "max_cost_per_month" in ws_enrich
            assert "tokens_used_today" in ws_enrich
            assert "cost_today" in ws_enrich


async def test_audit_log_requires_tenant_admin(app, member_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/admin/audit", headers={"Authorization": f"Bearer {member_token}"})
        assert resp.status_code == 403


async def test_audit_log_as_admin(app, tenant_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/admin/audit", headers={"Authorization": f"Bearer {tenant_admin_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data


async def test_audit_log_with_filters(app, tenant_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/admin/audit?action=test&limit=10&offset=0",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
        )
        assert resp.status_code == 200


async def test_quota_update_not_member(app, workspace_admin_token):
    """'nonexistent' workspace — admin has no WorkspaceMember row → 403."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.put(
            "/api/v1/admin/workspaces/nonexistent/quota",
            headers={"Authorization": f"Bearer {workspace_admin_token}"},
            json={"max_tokens_per_day": 500000},
        )
        assert resp.status_code == 403


async def test_quota_update_as_workspace_admin(app):
    """P0-2: workspace_admin can update quota for their workspace."""
    token = await _seed_workspace_admin(
        ws_id="ws-quota-admin",
        user_id="qadmin-1",
        role="workspace_admin",
        email="qadmin1@test.com",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.put(
            "/api/v1/admin/workspaces/ws-quota-admin/quota",
            headers={"Authorization": f"Bearer {token}"},
            json={"max_tokens_per_day": 500000},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_tokens_per_day"] == 500000
