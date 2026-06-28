import pytest
from httpx import ASGITransport, AsyncClient

from src.main import create_app
from src.gateway.auth.jwt import create_jwt

pytestmark = pytest.mark.asyncio


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def tenant_admin_token():
    return create_jwt({
        "id": "admin-uuid",
        "tenant_id": "tenant-uuid",
        "email": "admin@test.com",
        "role": "tenant_admin",
        "workspace_ids": ["ws-1", "ws-2"],
    })


@pytest.fixture
def workspace_member_token():
    return create_jwt({
        "id": "member-uuid",
        "tenant_id": "tenant-uuid",
        "email": "member@test.com",
        "role": "member",
        "workspace_ids": ["ws-1"],
    })


@pytest.fixture
def other_workspace_token():
    return create_jwt({
        "id": "other-uuid",
        "tenant_id": "tenant-uuid",
        "email": "other@test.com",
        "role": "member",
        "workspace_ids": ["ws-2"],
    })


async def test_workspace_audit_requires_auth(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/workspaces/ws-1/audit")
        assert resp.status_code == 401


async def test_workspace_audit_allows_member(app, workspace_member_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/workspaces/ws-1/audit",
            headers={"Authorization": f"Bearer {workspace_member_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data


async def test_workspace_audit_denies_non_member(app, other_workspace_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/workspaces/ws-1/audit",
            headers={"Authorization": f"Bearer {other_workspace_token}"},
        )
        assert resp.status_code == 403


async def test_workspace_audit_allows_tenant_admin(app, tenant_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/workspaces/ws-1/audit",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
        )
        assert resp.status_code == 200


async def test_workspace_audit_with_filters(app, workspace_member_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/workspaces/ws-1/audit?action=test.action&since=2026-01-01T00:00:00&limit=10&offset=0",
            headers={"Authorization": f"Bearer {workspace_member_token}"},
        )
        assert resp.status_code == 200


async def test_workspace_audit_pagination(app, workspace_member_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/workspaces/ws-1/audit?limit=5&offset=0",
            headers={"Authorization": f"Bearer {workspace_member_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) <= 5
