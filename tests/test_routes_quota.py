import pytest
from httpx import AsyncClient, ASGITransport

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import Workspace


async def _seed_membership(
    ws_id: str, user_id: str, role: str, tenant_id: str = "t-quota"
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
            email=f"{user_id}@test.com",
            name=user_id,
        )
        break
    return token


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


@pytest.mark.asyncio
async def test_get_quota_requires_auth(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/workspaces/ws1/quota")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_quota_requires_member(app):
    """A 'viewer'-role JWT with no WorkspaceMember row gets 403."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws1/quota",
            headers={"Authorization": f"Bearer {_viewer_token_no_membership()}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_quota_returns_data(app):
    token = await _seed_membership("quota-test-1", "qt-user-1", "member")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/quota-test-1/quota",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "max_tokens_per_day" in data
        assert "usage_today" in data
        assert "tokens_used" in data
        assert "cost_today" in data


@pytest.mark.asyncio
async def test_update_quota_requires_admin(app):
    """A plain member cannot update quota."""
    token = await _seed_membership("quota-req-1", "qr-user-1", "member")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/workspaces/quota-req-1/quota",
            json={"max_tokens_per_day": 500},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_quota(app):
    token = await _seed_membership("quota-update-1", "qu-user-1", "workspace_admin")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/workspaces/quota-update-1/quota",
            json={"max_tokens_per_day": 500},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["quota"]["max_tokens_per_day"] == 500


def _viewer_token_no_membership():
    """A JWT for a user with tenant role 'viewer' and no WorkspaceMember row."""
    return create_jwt({
        "id": "test-user-viewer",
        "sub": "test-user-viewer",
        "tenant_id": "test-tenant",
        "email": "viewer@test.com",
        "role": "viewer",
    })
