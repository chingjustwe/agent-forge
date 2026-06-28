import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


@pytest.mark.asyncio
async def test_register(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "new@test.com", "password": "secret123", "name": "New User"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "token" in body
        assert body["user"]["email"] == "new@test.com"
        assert body["user"]["name"] == "New User"


@pytest.mark.asyncio
async def test_register_duplicate(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/v1/auth/register",
            json={"email": "dup@test.com", "password": "secret123", "name": "Dup"},
        )
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "dup@test.com", "password": "secret123", "name": "Dup"},
        )
        assert resp.status_code == 409


@pytest.mark.asyncio
async def test_login(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/v1/auth/register",
            json={"email": "login@test.com", "password": "secret123", "name": "Login"},
        )
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "secret123"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "token" in body


@pytest.mark.asyncio
async def test_login_wrong_password(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/v1/auth/register",
            json={"email": "wrongpw@test.com", "password": "correct", "name": "WP"},
        )
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "wrongpw@test.com", "password": "wrong"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/auth/logout")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_oidc_login_redirect(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/login?provider=google")
        assert resp.status_code == 302


@pytest.mark.asyncio
async def test_oidc_callback(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/callback?code=test&state=test")
        assert resp.status_code == 200
