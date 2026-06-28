import pytest
from httpx import AsyncClient, ASGITransport

from src.gateway.auth.jwt import create_jwt


def _token(role: str = "tenant_admin"):
    return create_jwt({
        "id": "u-1",
        "tenant_id": "t-1",
        "email": f"{role}@test.com",
        "role": role,
        "workspace_ids": [],
    })


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


@pytest.mark.asyncio
async def test_users_me(app):
    from src.infra.db.models import Tenant, User
    from src.infra.db.engine import async_session
    async with async_session() as session:
        session.add(Tenant(name="T", domain="t.com"))
        await session.flush()
        user = User(tenant_id="t-1", email="me@test.com", name="Me", role="member")
        session.add(user)
        await session.commit()
        uid = user.id

    token = create_jwt({
        "id": uid,
        "tenant_id": "t-1",
        "email": "me@test.com",
        "role": "member",
        "workspace_ids": [],
    })
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_users_me_unauthorized(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/users/me")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_list_users(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_admin_list_users_forbidden(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {_token('member')}"},
        )
        assert resp.status_code == 403
