"""Tests for the /api/v1/workspaces/{ws_id}/sessions endpoints and chat
session_id persistence.

RED phase: these tests exercise routes that have not been registered yet.
"""
import os
import uuid as _uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import (
    ChatMessage,
    ChatSession,
    Tenant,
    User,
    Workspace,
    WorkspaceMember,
)


@pytest.fixture(autouse=True)
def _set_env():
    os.environ["LLM_API_KEY"] = "test-key"
    yield
    os.environ.pop("LLM_API_KEY", None)


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


def _token(user_id: str, tenant_id: str, role: str = "member", email: str | None = None):
    return create_jwt({
        "id": user_id,
        "sub": user_id,
        "tenant_id": tenant_id,
        "email": email or f"{user_id}@test.com",
        "role": role,
    })


async def _seed_workspace_with_owner(
    ws_id: str,
    tenant_id: str,
    owner_id: str,
    owner_role: str = "workspace_admin",
    tenant_role: str | None = None,
    email: str | None = None,
) -> str:
    """Seed tenant + workspace + user + WorkspaceMember(owner). Returns JWT."""
    if tenant_role is None:
        tenant_role = owner_role
    async with async_session() as session:
        if not await session.get(Tenant, tenant_id):
            session.add(Tenant(id=tenant_id, name="T", domain=f"{tenant_id}.test"))
            await session.flush()
        if not await session.get(Workspace, ws_id):
            session.add(Workspace(id=ws_id, tenant_id=tenant_id, name=f"WS {ws_id}"))
            await session.flush()
        if not await session.get(User, owner_id):
            session.add(
                User(
                    id=owner_id,
                    tenant_id=tenant_id,
                    email=email or f"{owner_id}@test.com",
                    name=owner_id,
                    role=tenant_role,
                )
            )
            await session.flush()
        if not await session.get(WorkspaceMember, (ws_id, owner_id)):
            session.add(
                WorkspaceMember(workspace_id=ws_id, user_id=owner_id, role=owner_role)
            )
        await session.commit()
    return _token(owner_id, tenant_id, role=tenant_role, email=email)


async def _add_workspace_member(
    ws_id: str,
    tenant_id: str,
    user_id: str,
    role: str = "member",
    tenant_role: str = "member",
    email: str | None = None,
) -> str:
    """Add an additional WorkspaceMember; returns JWT for that user."""
    async with async_session() as session:
        if not await session.get(User, user_id):
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=email or f"{user_id}@test.com",
                    name=user_id,
                    role=tenant_role,
                )
            )
            await session.flush()
        if not await session.get(WorkspaceMember, (ws_id, user_id)):
            session.add(WorkspaceMember(workspace_id=ws_id, user_id=user_id, role=role))
        await session.commit()
    return _token(user_id, tenant_id, role=tenant_role, email=email)


# ---------------------------------------------------------------------------
# POST /api/v1/workspaces/{ws_id}/sessions — create
# ---------------------------------------------------------------------------
class TestCreateSession:
    @pytest.mark.asyncio
    async def test_create_default_private(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-c-{suffix}"
        tid = f"t-c-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, f"u-{suffix}")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/workspaces/{ws_id}/sessions",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["workspace_id"] == ws_id
            assert body["owner_id"] == f"u-{suffix}"
            assert body["visibility"] == "private"
            assert body["title"] == "New Chat"
            assert body["archived"] is False or body["archived"] == 0

    @pytest.mark.asyncio
    async def test_create_with_title_and_visibility(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-c2-{suffix}"
        tid = f"t-c2-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, f"u-{suffix}")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/workspaces/{ws_id}/sessions",
                json={"title": "Plan", "visibility": "workspace", "agent_name": "direct"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["title"] == "Plan"
            assert body["visibility"] == "workspace"
            assert body["agent_name"] == "direct"

    @pytest.mark.asyncio
    async def test_create_non_member_forbidden(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-c3-{suffix}"
        tid = f"t-c3-{suffix}"
        # Owner creates the workspace.
        await _seed_workspace_with_owner(ws_id, tid, f"owner-{suffix}")
        # A different user (no membership) tries to create a session.
        outsider_token = _token(f"out-{suffix}", tid)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/workspaces/{ws_id}/sessions",
                json={},
                headers={"Authorization": f"Bearer {outsider_token}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_requires_auth(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/workspaces/whatever/sessions",
                json={},
            )
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/workspaces/{ws_id}/sessions — list with visibility
# ---------------------------------------------------------------------------
class TestListSessions:
    @pytest.mark.asyncio
    async def test_owner_sees_own_private(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-l-{suffix}"
        tid = f"t-l-{suffix}"
        owner_id = f"owner-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, owner_id)

        # Seed a private session owned by owner.
        async with async_session() as session:
            session.add(
                ChatSession(
                    workspace_id=ws_id,
                    owner_id=owner_id,
                    title="private",
                    visibility="private",
                )
            )
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/sessions",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            items = resp.json()
            assert any(s["title"] == "private" for s in items)

    @pytest.mark.asyncio
    async def test_other_member_cannot_see_private(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-l2-{suffix}"
        tid = f"t-l2-{suffix}"
        owner_id = f"owner-{suffix}"
        await _seed_workspace_with_owner(ws_id, tid, owner_id)

        # Seed a private session owned by owner.
        async with async_session() as session:
            session.add(
                ChatSession(
                    workspace_id=ws_id,
                    owner_id=owner_id,
                    title="secret",
                    visibility="private",
                )
            )
            await session.commit()

        # Another workspace member.
        member_token = await _add_workspace_member(
            ws_id, tid, f"mem-{suffix}", role="member"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/sessions",
                headers={"Authorization": f"Bearer {member_token}"},
            )
            assert resp.status_code == 200
            items = resp.json()
            assert not any(s["title"] == "secret" for s in items)

    @pytest.mark.asyncio
    async def test_other_member_sees_workspace_visible(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-l3-{suffix}"
        tid = f"t-l3-{suffix}"
        owner_id = f"owner-{suffix}"
        await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            session.add(
                ChatSession(
                    workspace_id=ws_id,
                    owner_id=owner_id,
                    title="shared",
                    visibility="workspace",
                )
            )
            await session.commit()

        member_token = await _add_workspace_member(
            ws_id, tid, f"mem-{suffix}", role="member"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/sessions",
                headers={"Authorization": f"Bearer {member_token}"},
            )
            assert resp.status_code == 200
            items = resp.json()
            assert any(s["title"] == "shared" for s in items)

    @pytest.mark.asyncio
    async def test_workspace_admin_sees_all(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-l4-{suffix}"
        tid = f"t-l4-{suffix}"
        owner_id = f"owner-{suffix}"
        await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            session.add(
                ChatSession(
                    workspace_id=ws_id,
                    owner_id=owner_id,
                    title="admin-sees-this",
                    visibility="private",
                )
            )
            await session.commit()

        admin_token = await _add_workspace_member(
            ws_id, tid, f"admin-{suffix}", role="workspace_admin"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/sessions",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert resp.status_code == 200
            items = resp.json()
            assert any(s["title"] == "admin-sees-this" for s in items)

    @pytest.mark.asyncio
    async def test_tenant_admin_short_circuits(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-l5-{suffix}"
        tid = f"t-l5-{suffix}"
        owner_id = f"owner-{suffix}"
        await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            session.add(
                ChatSession(
                    workspace_id=ws_id,
                    owner_id=owner_id,
                    title="ta-sees-this",
                    visibility="private",
                )
            )
            await session.commit()

        # tenant_admin with no WorkspaceMember row.
        ta_token = _token(f"ta-{suffix}", tid, role="tenant_admin")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/sessions",
                headers={"Authorization": f"Bearer {ta_token}"},
            )
            assert resp.status_code == 200
            items = resp.json()
            assert any(s["title"] == "ta-sees-this" for s in items)

    @pytest.mark.asyncio
    async def test_archived_excluded_from_list(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-l6-{suffix}"
        tid = f"t-l6-{suffix}"
        owner_id = f"owner-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            session.add(
                ChatSession(
                    workspace_id=ws_id,
                    owner_id=owner_id,
                    title="alive",
                    visibility="private",
                )
            )
            session.add(
                ChatSession(
                    workspace_id=ws_id,
                    owner_id=owner_id,
                    title="dead",
                    visibility="private",
                    archived=1,
                )
            )
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/sessions",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            items = resp.json()
            titles = [s["title"] for s in items]
            assert "alive" in titles
            assert "dead" not in titles


# ---------------------------------------------------------------------------
# GET /api/v1/workspaces/{ws_id}/sessions/{session_id} — detail + messages
# ---------------------------------------------------------------------------
class TestGetSession:
    @pytest.mark.asyncio
    async def test_detail_returns_messages(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-g-{suffix}"
        tid = f"t-g-{suffix}"
        owner_id = f"owner-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            cs = ChatSession(
                workspace_id=ws_id,
                owner_id=owner_id,
                title="d",
                visibility="private",
            )
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            session.add(
                ChatMessage(session_id=cs.id, role="user", content="hi", tokens=2)
            )
            session.add(
                ChatMessage(session_id=cs.id, role="assistant", content="hello", tokens=4)
            )
            await session.commit()
            cs_id = cs.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/sessions/{cs_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["session"]["id"] == cs_id
            assert len(body["messages"]) == 2
            # Ordered by created_at ASC.
            assert body["messages"][0]["role"] == "user"
            assert body["messages"][0]["content"] == "hi"
            assert body["messages"][1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_detail_other_member_private_forbidden(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-g2-{suffix}"
        tid = f"t-g2-{suffix}"
        owner_id = f"owner-{suffix}"
        await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            cs = ChatSession(
                workspace_id=ws_id,
                owner_id=owner_id,
                title="d",
                visibility="private",
            )
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            cs_id = cs.id

        member_token = await _add_workspace_member(
            ws_id, tid, f"mem-{suffix}", role="member"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/sessions/{cs_id}",
                headers={"Authorization": f"Bearer {member_token}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_detail_missing_returns_404(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-g3-{suffix}"
        tid = f"t-g3-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, f"owner-{suffix}")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/sessions/does-not-exist",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/v1/workspaces/{ws_id}/sessions/{session_id} — update
# ---------------------------------------------------------------------------
class TestPatchSession:
    @pytest.mark.asyncio
    async def test_owner_can_patch(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-p-{suffix}"
        tid = f"t-p-{suffix}"
        owner_id = f"owner-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            cs = ChatSession(workspace_id=ws_id, owner_id=owner_id)
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            cs_id = cs.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                f"/api/v1/workspaces/{ws_id}/sessions/{cs_id}",
                json={"title": "renamed", "visibility": "workspace"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["title"] == "renamed"
            assert body["visibility"] == "workspace"

    @pytest.mark.asyncio
    async def test_non_owner_member_forbidden(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-p2-{suffix}"
        tid = f"t-p2-{suffix}"
        owner_id = f"owner-{suffix}"
        await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            cs = ChatSession(workspace_id=ws_id, owner_id=owner_id, visibility="workspace")
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            cs_id = cs.id

        member_token = await _add_workspace_member(
            ws_id, tid, f"mem-{suffix}", role="member"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                f"/api/v1/workspaces/{ws_id}/sessions/{cs_id}",
                json={"title": "hacked"},
                headers={"Authorization": f"Bearer {member_token}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_workspace_admin_can_patch(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-p3-{suffix}"
        tid = f"t-p3-{suffix}"
        owner_id = f"owner-{suffix}"
        await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            cs = ChatSession(workspace_id=ws_id, owner_id=owner_id, visibility="private")
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            cs_id = cs.id

        admin_token = await _add_workspace_member(
            ws_id, tid, f"admin-{suffix}", role="workspace_admin"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                f"/api/v1/workspaces/{ws_id}/sessions/{cs_id}",
                json={"title": "admin renamed"},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert resp.status_code == 200
            assert resp.json()["title"] == "admin renamed"


# ---------------------------------------------------------------------------
# DELETE /api/v1/workspaces/{ws_id}/sessions/{session_id} — soft delete
# ---------------------------------------------------------------------------
class TestDeleteSession:
    @pytest.mark.asyncio
    async def test_owner_soft_deletes(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-d-{suffix}"
        tid = f"t-d-{suffix}"
        owner_id = f"owner-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            cs = ChatSession(workspace_id=ws_id, owner_id=owner_id)
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            cs_id = cs.id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/workspaces/{ws_id}/sessions/{cs_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code in (200, 204)

        # Verify archived flag flipped.
        async with async_session() as session:
            row = await session.get(ChatSession, cs_id)
            assert row.archived == 1

    @pytest.mark.asyncio
    async def test_non_owner_cannot_delete(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-d2-{suffix}"
        tid = f"t-d2-{suffix}"
        owner_id = f"owner-{suffix}"
        await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            cs = ChatSession(workspace_id=ws_id, owner_id=owner_id, visibility="workspace")
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            cs_id = cs.id

        member_token = await _add_workspace_member(
            ws_id, tid, f"mem-{suffix}", role="member"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/workspaces/{ws_id}/sessions/{cs_id}",
                headers={"Authorization": f"Bearer {member_token}"},
            )
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_workspace_admin_can_delete(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-d3-{suffix}"
        tid = f"t-d3-{suffix}"
        owner_id = f"owner-{suffix}"
        await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            cs = ChatSession(workspace_id=ws_id, owner_id=owner_id, visibility="private")
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            cs_id = cs.id

        admin_token = await _add_workspace_member(
            ws_id, tid, f"admin-{suffix}", role="workspace_admin"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/api/v1/workspaces/{ws_id}/sessions/{cs_id}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert resp.status_code in (200, 204)


# ---------------------------------------------------------------------------
# /api/v1/chat with session_id — persistence
# ---------------------------------------------------------------------------
class TestChatSessionIdPersistence:
    @pytest.mark.asyncio
    async def test_chat_with_session_writes_messages(self, app, httpx_mock):
        # Mock the LLM upstream so the SSE stream completes.
        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=b'data: {"choices":[{"delta":{"content":"Hi there"},"finish_reason":null}]}\n\ndata: [DONE]\n',
            headers={"Content-Type": "text/event-stream"},
        )

        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-chat-{suffix}"
        tid = f"t-chat-{suffix}"
        owner_id = f"u-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, owner_id)

        # Create a session via the API (exercises POST route).
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post(
                f"/api/v1/workspaces/{ws_id}/sessions",
                json={"title": "Chat sess"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert create_resp.status_code == 201
            session_id = create_resp.json()["id"]

            # Drive the chat endpoint with the session_id.
            async with client.stream(
                "POST",
                "/api/v1/chat",
                json={
                    "messages": [{"role": "user", "content": "Hello"}],
                    "config": {
                        "workspace_id": ws_id,
                        "session_id": session_id,
                    },
                },
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                assert resp.status_code == 200
                async for _ in resp.aiter_bytes():
                    pass

        # Two ChatMessage rows must exist: user + assistant.
        async with async_session() as session:
            from sqlalchemy import select
            msgs = (
                await session.execute(
                    select(ChatMessage).where(ChatMessage.session_id == session_id)
                )
            ).scalars().all()
            roles = [m.role for m in msgs]
            assert "user" in roles
            assert "assistant" in roles
            user_msg = next(m for m in msgs if m.role == "user")
            assert user_msg.content == "Hello"
            asst_msg = next(m for m in msgs if m.role == "assistant")
            assert "Hi there" in asst_msg.content

    @pytest.mark.asyncio
    async def test_chat_without_session_still_works(self, app, httpx_mock):
        httpx_mock.add_response(
            url="https://api.deepseek.com/v1/chat/completions",
            content=b'data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":null}]}\n\ndata: [DONE]\n',
            headers={"Content-Type": "text/event-stream"},
        )

        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-chat2-{suffix}"
        tid = f"t-chat2-{suffix}"
        owner_id = f"u-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, owner_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/api/v1/chat",
                json={
                    "messages": [{"role": "user", "content": "Hello"}],
                    "config": {"workspace_id": ws_id},
                },
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                assert resp.status_code == 200
                async for _ in resp.aiter_bytes():
                    pass

        # No sessions/messages were created.
        async with async_session() as session:
            from sqlalchemy import select
            session_count = (
                await session.execute(
                    select(ChatSession).where(ChatSession.workspace_id == ws_id)
                )
            ).scalars().all()
            assert len(session_count) == 0
