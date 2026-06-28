import pytest
from httpx import AsyncClient, ASGITransport

from src.gateway.auth.jwt import create_jwt


def _admin_token():
    return create_jwt({
        "id": "admin-1",
        "tenant_id": "tenant-1",
        "email": "admin@test.com",
        "role": "tenant_admin",
        "workspace_ids": [],
    })


def _member_token():
    return create_jwt({
        "id": "member-1",
        "tenant_id": "tenant-1",
        "email": "member@test.com",
        "role": "member",
        "workspace_ids": [],
    })


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


@pytest.mark.asyncio
async def test_list_workspaces_as_admin(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_workspaces_as_member_forbidden(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces",
            headers={"Authorization": f"Bearer {_member_token()}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_workspace(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        from src.infra.db.models import Tenant
        from src.infra.db.engine import async_session
        async with async_session() as session:
            session.add(Tenant(name="Test", domain="test.com"))
            await session.commit()

        resp = await client.post(
            "/api/v1/workspaces",
            json={"name": "Test Workspace"},
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Test Workspace"


@pytest.mark.asyncio
async def test_create_workspace_member_forbidden(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/workspaces",
            json={"name": "Should Fail"},
            headers={"Authorization": f"Bearer {_member_token()}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_workspaces_require_auth(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/workspaces")
        assert resp.status_code == 401

        resp = await client.post("/api/v1/workspaces", json={"name": "x"})
        assert resp.status_code == 401
