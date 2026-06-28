import pytest
from httpx import AsyncClient, ASGITransport

from src.gateway.auth.jwt import create_jwt


def _token(role: str = "member"):
    return create_jwt({
        "id": "test-user",
        "tenant_id": "test-tenant",
        "email": "test@test.com",
        "role": role,
        "workspace_ids": [],
    })


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


@pytest.mark.asyncio
async def test_get_otel_settings_requires_auth(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/workspaces/ws1/settings/otel")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_otel_returns_defaults(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws1/settings/otel",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["endpoint"] == ""


@pytest.mark.asyncio
async def test_update_otel_requires_admin(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/workspaces/ws1/settings/otel",
            json={"enabled": True, "endpoint": "http://otel:4318", "headers": {}},
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_otel_settings(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/workspaces/ws1/settings/otel",
            json={"enabled": True, "endpoint": "http://otel:4318", "headers": {"X-Auth": "key"}},
            headers={"Authorization": f"Bearer {_token('workspace_admin')}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["otel"]["enabled"] is True
        assert data["otel"]["endpoint"] == "http://otel:4318"


@pytest.mark.asyncio
async def test_get_otel_after_update(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws1/settings/otel",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
