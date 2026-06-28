import pytest
from httpx import AsyncClient, ASGITransport

from src.gateway.auth.jwt import create_jwt
from src.infra.telemetry.collector import TelemetryCollector


def _token(role: str = "member", ws_ids: list | None = None):
    return create_jwt({
        "id": "test-user",
        "tenant_id": "test-tenant",
        "email": "test@test.com",
        "role": role,
        "workspace_ids": ws_ids or [],
    })


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


@pytest.mark.asyncio
async def test_summary_requires_auth(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/workspaces/ws1/observability/summary")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_summary_requires_member(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws1/observability/summary",
            headers={"Authorization": f"Bearer {_token('viewer')}"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_summary_returns_data(app):
    collector = TelemetryCollector()
    await collector.record_request(ws_id="ws1", model="m1", duration_ms=50, tokens={"input": 10, "output": 5})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws1/observability/summary",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total_requests" in data
        assert "avg_latency_ms" in data
        assert "total_tokens" in data


@pytest.mark.asyncio
async def test_requests_list(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws1/observability/requests",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_request_detail_not_found(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws1/observability/requests/nonexistent",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_token_daily(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws1/observability/tokens/daily",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_latency(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws1/observability/latency",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "p50_ms" in data


@pytest.mark.asyncio
async def test_errors(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws1/observability/errors",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
