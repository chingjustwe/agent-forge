import pytest
from httpx import AsyncClient, ASGITransport

from src.gateway.auth.jwt import create_jwt


async def _seed_membership(
    ws_id: str, user_id: str, role: str, tenant_id: str = "t-settings"
) -> str:
    """Create workspace+user+WorkspaceMember and return a JWT."""
    from tests.conftest import setup_workspace_with_member
    from src.infra.db.session import get_db

    async for session in get_db():
        token = await setup_workspace_with_member(
            session,
            ws_id=ws_id,
            tenant_id=tenant_id,
            user_id=user_id,
            user_role=role,
            tenant_role=role,
            email=f"{user_id}@test.com",
            name=user_id,
        )
        break
    return token


def _no_membership_token(role: str = "viewer") -> str:
    return create_jwt({
        "id": "test-user-nm",
        "sub": "test-user-nm",
        "tenant_id": "test-tenant",
        "email": "nm@test.com",
        "role": role,
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
    token = await _seed_membership("ws-otel-1", "otel-user-1", "workspace_admin")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws-otel-1/settings/otel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["endpoint"] == ""


@pytest.mark.asyncio
async def test_get_otel_requires_admin(app):
    """A plain member cannot read otel settings either."""
    token = await _seed_membership("ws-otel-1b", "otel-user-1b", "member")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws-otel-1b/settings/otel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_otel_requires_admin(app):
    """A plain member cannot update otel settings."""
    token = await _seed_membership("ws-otel-2", "otel-user-2", "member")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/workspaces/ws-otel-2/settings/otel",
            json={"enabled": True, "endpoint": "http://otel:4318", "headers": {}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_otel_settings(app):
    token = await _seed_membership("ws-otel-3", "otel-user-3", "workspace_admin")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/workspaces/ws-otel-3/settings/otel",
            json={"enabled": True, "endpoint": "http://otel:4318", "headers": {"X-Auth": "key"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["otel"]["enabled"] is True
        assert data["otel"]["endpoint"] == "http://otel:4318"


@pytest.mark.asyncio
async def test_get_otel_after_update(app):
    token = await _seed_membership("ws-otel-3", "otel-user-3", "workspace_admin")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws-otel-3/settings/otel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
