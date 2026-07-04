import os

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session


@pytest.fixture(autouse=True)
def _set_env():
    os.environ["LLM_API_KEY"] = "test-key"
    yield
    os.environ.pop("LLM_API_KEY", None)


def _token(role: str = "member"):
    return create_jwt({
        "id": "test-user",
        "sub": "test-user",
        "tenant_id": "test-tenant",
        "email": "test@test.com",
        "role": role,
    })


async def _seed_chat_membership() -> None:
    """P0-4: /chat now requires config.workspace_id and a WorkspaceMember row.

    Seeds tenant + workspace + user + membership so test-user can chat in
    ws-test-chat. Idempotent — safe to call from multiple tests.
    """
    from src.infra.db.models import Tenant, User, Workspace, WorkspaceMember
    from src.infra.db.session import get_db

    async for session in get_db():
        if not await session.get(Tenant, "test-tenant"):
            session.add(Tenant(id="test-tenant", name="T", domain="test.test"))
            await session.flush()
        if not await session.get(Workspace, "ws-test-chat"):
            session.add(Workspace(id="ws-test-chat", tenant_id="test-tenant", name="WS Chat"))
            await session.flush()
        if not await session.get(User, "test-user"):
            session.add(
                User(
                    id="test-user",
                    tenant_id="test-tenant",
                    email="test@test.com",
                    name="Test",
                    role="member",
                )
            )
            await session.flush()
        if not await session.get(WorkspaceMember, ("ws-test-chat", "test-user")):
            session.add(
                WorkspaceMember(
                    workspace_id="ws-test-chat",
                    user_id="test-user",
                    role="member",
                )
            )
        await session.commit()
        break


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


@pytest.mark.asyncio
async def test_health_endpoint(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_chat_streaming(app, httpx_mock):
    httpx_mock.add_response(
        url="https://api.deepseek.com/v1/chat/completions",
        content=b'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n\ndata: [DONE]\n',
        headers={"Content-Type": "text/event-stream"},
    )

    # P0-4: /chat requires config.workspace_id and a WorkspaceMember row.
    await _seed_chat_membership()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "config": {"workspace_id": "ws-test-chat"},
            },
            headers={"Authorization": f"Bearer {_token()}"},
        ) as resp:
            assert resp.status_code == 200
            chunks = []
            async for chunk in resp.aiter_lines():
                if chunk.startswith("data: "):
                    chunks.append(chunk)
            assert len(chunks) > 0


@pytest.mark.asyncio
async def test_chat_invalid_json(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat",
            content="not json",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_token()}",
            },
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_chat_empty_messages(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat",
            json={"messages": []},
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_chat_requires_auth(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_viewer_forbidden(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/chat",
            json={"messages": [{"role": "user", "content": "Hi"}]},
            headers={"Authorization": f"Bearer {_token('viewer')}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_chat_creates_request_log(app, httpx_mock):
    httpx_mock.add_response(
        url="https://api.deepseek.com/v1/chat/completions",
        content=b'data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":null}]}\n\ndata: [DONE]\n',
        headers={"Content-Type": "text/event-stream"},
    )

    # P0-4: /chat requires config.workspace_id and a WorkspaceMember row.
    await _seed_chat_membership()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/api/v1/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "config": {"workspace_id": "ws-test-chat"},
            },
            headers={"Authorization": f"Bearer {_token()}"},
        ) as resp:
            assert resp.status_code == 200
            async for _ in resp.aiter_lines():
                pass

    async with async_session() as session:
        result = await session.execute(
            text("SELECT COUNT(*) as cnt FROM request_logs")
        )
        assert result.one().cnt >= 1
