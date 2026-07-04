import uuid as _uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import create_app
from src.gateway.auth.jwt import create_jwt

pytestmark = pytest.mark.asyncio


@pytest.fixture
def app():
    return create_app()


async def _seed(
    workspace_id: str,
    user_id: str,
    role: str = "member",
    tenant_id: str | None = None,
) -> tuple[str, str]:
    """Create tenant+workspace+WorkspaceMember and return (token, tenant_id)."""
    from tests.conftest import setup_workspace_with_member
    from src.infra.db.session import get_db

    tid = tenant_id or f"t-{_uuid.uuid4().hex[:8]}"
    async for session in get_db():
        token = await setup_workspace_with_member(
            session,
            ws_id=workspace_id,
            tenant_id=tid,
            user_id=user_id,
            user_role=role,
            email=f"{user_id}@test.com",
            name=user_id,
        )
        break
    return token, tid


async def _ensure_workspace(workspace_id: str, tenant_id: str) -> None:
    from src.infra.db.models import Workspace
    from src.infra.db.session import get_db

    async for session in get_db():
        existing = await session.get(Workspace, workspace_id)
        if not existing:
            session.add(Workspace(id=workspace_id, tenant_id=tenant_id, name=f"WS {workspace_id}"))
            await session.commit()
        break


async def test_workspace_audit_requires_auth(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/workspaces/ws-1/audit")
        assert resp.status_code == 401


async def test_workspace_audit_allows_member(app):
    token, _ = await _seed("ws-aud-1", "member-uuid-1", role="member")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/workspaces/ws-aud-1/audit",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data


async def test_workspace_audit_denies_non_member(app):
    """User is a member of ws-A but tries to access ws-B's audit log."""
    token, tid = await _seed("ws-A", "other-uuid-1", role="member")
    await _ensure_workspace("ws-B", tid)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/workspaces/ws-B/audit",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


async def test_workspace_audit_allows_tenant_admin(app):
    """tenant_admin short-circuits — no WorkspaceMember row needed."""
    token, tid = await _seed("ws-ta-1", "seed-user-1", role="member")

    admin_token = create_jwt({
        "id": "admin-uuid",
        "sub": "admin-uuid",
        "tenant_id": tid,
        "email": "admin@test.com",
        "role": "tenant_admin",
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/workspaces/ws-ta-1/audit",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200


async def test_workspace_audit_with_filters(app):
    token, _ = await _seed("ws-aud-2", "member-uuid-2", role="member")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/workspaces/ws-aud-2/audit?action=test.action&since=2026-01-01T00:00:00&limit=10&offset=0",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


async def test_workspace_audit_pagination(app):
    token, _ = await _seed("ws-aud-3", "member-uuid-3", role="member")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/workspaces/ws-aud-3/audit?limit=5&offset=0",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) <= 5
