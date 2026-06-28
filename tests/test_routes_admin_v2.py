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
        "workspace_ids": [],
    })


@pytest.fixture
def workspace_owner_token():
    return create_jwt({
        "id": "owner-uuid",
        "tenant_id": "tenant-uuid",
        "email": "owner@test.com",
        "role": "workspace_owner",
        "workspace_ids": ["ws-1"],
    })


@pytest.fixture
def member_token():
    return create_jwt({
        "id": "member-uuid",
        "tenant_id": "tenant-uuid",
        "email": "member@test.com",
        "role": "member",
        "workspace_ids": ["ws-1"],
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


async def test_invite_duplicate(app, tenant_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/admin/users/invite",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
            json={"email": "newuser@test.com", "role": "member"},
        )
        assert resp.status_code == 409


async def test_list_workspaces_requires_workspace_owner(app, member_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/admin/workspaces", headers={"Authorization": f"Bearer {member_token}"})
        assert resp.status_code == 403


async def test_list_workspaces_as_owner(app, workspace_owner_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/admin/workspaces", headers={"Authorization": f"Bearer {workspace_owner_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


async def test_update_workspace(app, workspace_owner_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.put(
            "/api/v1/admin/workspaces/nonexistent",
            headers={"Authorization": f"Bearer {workspace_owner_token}"},
            json={"name": "Updated WS"},
        )
        assert resp.status_code == 404


async def test_archive_workspace(app, workspace_owner_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.delete(
            "/api/v1/admin/workspaces/nonexistent",
            headers={"Authorization": f"Bearer {workspace_owner_token}"},
        )
        assert resp.status_code == 404


async def test_usage_as_workspace_owner(app, workspace_owner_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/admin/usage", headers={"Authorization": f"Bearer {workspace_owner_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert "total_requests" in data
        assert "total_tokens" in data
        assert "total_cost" in data
        assert "by_workspace" in data


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


async def test_quota_update(app, workspace_owner_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.put(
            "/api/v1/admin/workspaces/nonexistent/quota",
            headers={"Authorization": f"Bearer {workspace_owner_token}"},
            json={"max_tokens_per_day": 500000},
        )
        assert resp.status_code == 404
