import pytest
from httpx import AsyncClient, ASGITransport

from src.gateway.auth.jwt import create_jwt
from src.infra.telemetry.collector import TelemetryCollector


async def _seed_membership(
    ws_id: str, user_id: str, role: str = "member", tenant_id: str = "t-obs"
) -> str:
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
async def test_summary_requires_auth(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/workspaces/ws1/observability/summary")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_summary_requires_member(app):
    """A viewer with no WorkspaceMember row gets 403."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws1/observability/summary",
            headers={"Authorization": f"Bearer {_no_membership_token('viewer')}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_summary_returns_data(app):
    token = await _seed_membership("ws-obs-1", "obs-user-1", "member")
    collector = TelemetryCollector()
    await collector.record_request(
        ws_id="ws-obs-1", model="m1", duration_ms=50, tokens={"input": 10, "output": 5}
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws-obs-1/observability/summary",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total_requests" in data
        assert "avg_latency_ms" in data
        assert "total_tokens" in data


@pytest.mark.asyncio
async def test_requests_list(app):
    token = await _seed_membership("ws-obs-2", "obs-user-2", "member")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws-obs-2/observability/requests",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_request_detail_not_found(app):
    token = await _seed_membership("ws-obs-3", "obs-user-3", "member")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws-obs-3/observability/requests/nonexistent",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_token_daily(app):
    token = await _seed_membership("ws-obs-4", "obs-user-4", "member")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws-obs-4/observability/tokens/daily",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_latency(app):
    token = await _seed_membership("ws-obs-5", "obs-user-5", "member")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws-obs-5/observability/latency",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "p50_ms" in data


@pytest.mark.asyncio
async def test_errors(app):
    token = await _seed_membership("ws-obs-6", "obs-user-6", "member")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws-obs-6/observability/errors",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_non_member_cannot_view_other_workspace(app):
    """A user with membership in ws-A cannot view observability for ws-B.

    This guards against the pre-P0-2 bug where any JWT with role=member
    could read any workspace's observability data.
    """
    token = await _seed_membership("ws-iso-A", "iso-user-A", "member")
    # Create ws-B without this user
    from src.infra.db.models import Workspace
    from src.infra.db.session import get_db
    async for session in get_db():
        existing = await session.get(Workspace, "ws-iso-B")
        if not existing:
            session.add(Workspace(id="ws-iso-B", tenant_id="t-obs", name="WS B"))
            await session.commit()
        break

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/workspaces/ws-iso-B/observability/summary",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
