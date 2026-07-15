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
    async def test_chat_with_session_writes_messages(self, app, monkeypatch):
        # Stub the adapter to avoid real LLM calls.
        from src.runtime.models import StreamEvent
        from src.runtime.adapters.deepagents import DeepAgentsAdapter

        async def _fake_run(self, messages, ctx):
            yield StreamEvent(type="text", data={"content": "Hi there"})
            yield StreamEvent(
                type="status",
                data={"usage": {"input_tokens": 1, "output_tokens": 1}},
            )

        monkeypatch.setattr(DeepAgentsAdapter, "run", _fake_run)

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
    async def test_chat_without_session_still_works(self, app, monkeypatch):
        from src.runtime.models import StreamEvent
        from src.runtime.adapters.deepagents import DeepAgentsAdapter

        async def _fake_run(self, messages, ctx):
            yield StreamEvent(type="text", data={"content": "Hi"})
            yield StreamEvent(
                type="status",
                data={"usage": {"input_tokens": 1, "output_tokens": 1}},
            )

        monkeypatch.setattr(DeepAgentsAdapter, "run", _fake_run)

        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-chat2-{suffix}"
        tid = f"t-chat2-{suffix}"
        owner_id = f"u-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, owner_id)

        transport = ASGITransport(app=app)
        new_session_id = None
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
                events = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        events.append(line[len("data: "):])

        # Lazy session creation: exactly one ChatSession is created server-side
        # on the first message, and a `session.created` SSE event is emitted
        # before any text events.
        import json as _json
        first = _json.loads(events[0])
        assert first["type"] == "session.created"
        new_session_id = first["data"]["session_id"]
        assert isinstance(new_session_id, str) and len(new_session_id) > 0

        async with async_session() as session:
            from sqlalchemy import select
            session_rows = (
                await session.execute(
                    select(ChatSession).where(ChatSession.workspace_id == ws_id)
                )
            ).scalars().all()
            assert len(session_rows) == 1
            assert session_rows[0].id == new_session_id
            assert session_rows[0].owner_id == owner_id


# ---------------------------------------------------------------------------
# GET /api/v1/workspaces/{ws_id}/sessions/{session_id}/checkpoints — Wave 2
# ---------------------------------------------------------------------------
class TestListCheckpoints:
    @pytest.mark.asyncio
    async def test_filters_to_turn_boundary_checkpoints(self, app):
        """Wave 2 fix (Bug 2): LangGraph writes one checkpoint per graph
        node, so a single user turn produces several internal rows. The
        list endpoint must collapse them to one restore point per
        distinct (monotonically increasing) message count."""
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-cp-{suffix}"
        tid = f"t-cp-{suffix}"
        owner_id = f"owner-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            cs = ChatSession(
                workspace_id=ws_id, owner_id=owner_id, visibility="private"
            )
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            cs_id = cs.id

        # The ``checkpoints`` table is a raw-SQL migration table (M13), not
        # part of Base.metadata, so create it explicitly for the test.
        from sqlalchemy import text

        from src.infra.db.engine import engine

        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS checkpoints ("
                    "session_id VARCHAR(32) NOT NULL,"
                    "sequence INTEGER NOT NULL,"
                    "messages TEXT NOT NULL,"
                    "tool_state TEXT NOT NULL,"
                    "agent_id VARCHAR(32) NOT NULL,"
                    "metadata TEXT NOT NULL DEFAULT '{}',"
                    "created_at DATETIME NOT NULL,"
                    "PRIMARY KEY (session_id, sequence)"
                    ")"
                )
            )

        # Seed 5 LangGraph-internal checkpoints for a single user turn.
        # All carry the same one user message, so they must collapse into
        # ONE restore point (the last, most complete one) — not 5.
        from src.runtime.harness.checkpoint import Checkpoint, SQLiteCheckpointStore

        store = SQLiteCheckpointStore()
        seeded = [
            [],
            [{"role": "user", "content": "hi"}],
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "assistant", "content": "done"},
            ],
        ]
        for i, msgs in enumerate(seeded, start=1):
            await store.save(
                Checkpoint(session_id=cs_id, sequence=i, messages=msgs)
            )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/sessions/{cs_id}/checkpoints",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            # One restore point for the empty pre-turn state (seq 1) and one
            # for the completed single-user-message turn (last = seq 5).
            assert [c["message_count"] for c in body] == [0, 3]
            assert [c["sequence"] for c in body] == [1, 5]

    @pytest.mark.asyncio
    async def test_one_restore_point_per_user_turn(self, app):
        """Bug 2 regression: two prompts (each producing ~5 LangGraph
        internal checkpoints) must collapse to one restore point per
        turn, not ~10 rows. Mirrors the reported '2 messages -> 10
        checkpoints' symptom."""
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-cp3-{suffix}"
        tid = f"t-cp3-{suffix}"
        owner_id = f"owner-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            cs = ChatSession(
                workspace_id=ws_id, owner_id=owner_id, visibility="private"
            )
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            cs_id = cs.id

        from sqlalchemy import text

        from src.infra.db.engine import engine

        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS checkpoints ("
                    "session_id VARCHAR(32) NOT NULL,"
                    "sequence INTEGER NOT NULL,"
                    "messages TEXT NOT NULL,"
                    "tool_state TEXT NOT NULL,"
                    "agent_id VARCHAR(32) NOT NULL,"
                    "metadata TEXT NOT NULL DEFAULT '{}',"
                    "created_at DATETIME NOT NULL,"
                    "PRIMARY KEY (session_id, sequence)"
                    ")"
                )
            )

        # Two turns. Turn 1 = user msg #1 (+ assistant). Turn 2 = user msg
        # #2 (+ assistant). 5 raw checkpoints per turn -> 10 total.
        from src.runtime.harness.checkpoint import Checkpoint, SQLiteCheckpointStore

        store = SQLiteCheckpointStore()
        m1 = [{"role": "user", "content": "q1"}]
        m1b = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
        m2 = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
              {"role": "user", "content": "q2"}]
        m2b = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
               {"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"}]
        # 5 rows per turn (am, a, end, bm, b) with shared user-count.
        turn1 = [m1, m1, m1b, m1b, m1b]
        turn2 = [m2, m2, m2b, m2b, m2b]
        seq = 0
        for msgs in turn1 + turn2:
            seq += 1
            await store.save(Checkpoint(session_id=cs_id, sequence=seq, messages=msgs))

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/sessions/{cs_id}/checkpoints",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            # turn1 (1 user msg, 2 msgs total) + turn2 (2 user msgs, 4 msgs
            # total). Each turn collapses to its most-complete checkpoint, so
            # 10 raw rows -> 2 restore points. (Real LangGraph flow has no
            # 0-message checkpoint, so no empty restore point appears here.)
            assert [c["message_count"] for c in body] == [2, 4]
            assert [c["sequence"] for c in body] == [5, 10]

    @pytest.mark.asyncio
    async def test_requires_membership(self, app):
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-cp2-{suffix}"
        tid = f"t-cp2-{suffix}"
        await _seed_workspace_with_owner(ws_id, tid, f"owner-{suffix}")
        outsider = _token(f"out-{suffix}", tid)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/workspaces/{ws_id}/sessions/nope/checkpoints",
                headers={"Authorization": f"Bearer {outsider}"},
            )
            assert resp.status_code == 403


class TestRestoreCheckpoint:
    @pytest.mark.asyncio
    async def test_restore_branches_with_real_messages_and_checkpoint(self, app):
        """Bug regression: restoring from a checkpoint must seed the new
        branch session with the REAL conversation (not empty) and also
        write a resumable checkpoint so the agent can continue it."""
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-rcp-{suffix}"
        tid = f"t-rcp-{suffix}"
        owner_id = f"owner-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            cs = ChatSession(workspace_id=ws_id, owner_id=owner_id, visibility="private")
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            cs_id = cs.id

        from sqlalchemy import text

        from src.infra.db.engine import engine
        from src.runtime.harness.checkpoint import Checkpoint, SQLiteCheckpointStore

        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS checkpoints ("
                    "session_id VARCHAR(32) NOT NULL,"
                    "sequence INTEGER NOT NULL,"
                    "messages TEXT NOT NULL,"
                    "tool_state TEXT NOT NULL,"
                    "agent_id VARCHAR(32) NOT NULL,"
                    "metadata TEXT NOT NULL DEFAULT '{}',"
                    "created_at DATETIME NOT NULL,"
                    "PRIMARY KEY (session_id, sequence))"
                )
            )
            # restore_checkpoint now writes per-message rows to
            # checkpoint_writes so the resumed session keeps full history.
            # Drop first to guarantee the M20 schema (checkpoint_id column)
            # regardless of migration drift in the shared test DB.
            await conn.execute(text("DROP TABLE IF EXISTS checkpoint_writes"))
            await conn.execute(
                text(
                    "CREATE TABLE checkpoint_writes ("
                    "session_id VARCHAR(64) NOT NULL,"
                    "checkpoint_id VARCHAR(64) NOT NULL DEFAULT '',"
                    "task_id VARCHAR(64) NOT NULL,"
                    "task_path VARCHAR(256) NOT NULL DEFAULT '',"
                    "channel VARCHAR(256) NOT NULL,"
                    "value TEXT NOT NULL,"
                    "created_at DATETIME NOT NULL,"
                    "PRIMARY KEY (session_id, checkpoint_id, task_id, task_path, channel))"
                )
            )

        msgs = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        store = SQLiteCheckpointStore()
        await store.save(
            Checkpoint(session_id=cs_id, sequence=1, messages=msgs, agent_id="a-1")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/workspaces/{ws_id}/sessions/{cs_id}/checkpoints/1/restore",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            new_sid = body["session_id"]
            assert new_sid != cs_id

        # New branch session seeded with the conversation messages.
        async with async_session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT role, content FROM chat_messages "
                        "WHERE session_id = :sid ORDER BY id"
                    ),
                    {"sid": new_sid},
                )
            ).fetchall()
        contents = [(r.role, r.content) for r in rows]
        assert ("user", "Hi") in contents
        assert ("assistant", "Hello") in contents

        # And it carries a resumable checkpoint row with the same messages.
        new_cps = await store.list(new_sid)
        assert len(new_cps) == 1
        assert new_cps[0].messages == msgs

    @pytest.mark.asyncio
    async def test_restore_in_place_preserves_earlier_checkpoints(self, app):
        """In-place restore (mode=in_place) rolls back the current session
        to the target checkpoint: checkpoints after it are deleted, earlier
        ones are preserved, chat_messages match the target, and the
        session_id stays the same."""
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-rip-{suffix}"
        tid = f"t-rip-{suffix}"
        owner_id = f"owner-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            cs = ChatSession(workspace_id=ws_id, owner_id=owner_id, visibility="private")
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            cs_id = cs.id

        from sqlalchemy import text

        from src.infra.db.engine import engine
        from src.runtime.harness.checkpoint import Checkpoint, SQLiteCheckpointStore

        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS checkpoints ("
                    "session_id VARCHAR(32) NOT NULL,"
                    "sequence INTEGER NOT NULL,"
                    "messages TEXT NOT NULL,"
                    "tool_state TEXT NOT NULL,"
                    "agent_id VARCHAR(32) NOT NULL,"
                    "metadata TEXT NOT NULL DEFAULT '{}',"
                    "created_at DATETIME NOT NULL,"
                    "PRIMARY KEY (session_id, sequence))"
                )
            )
            await conn.execute(text("DROP TABLE IF EXISTS checkpoint_writes"))
            await conn.execute(
                text(
                    "CREATE TABLE checkpoint_writes ("
                    "session_id VARCHAR(64) NOT NULL,"
                    "checkpoint_id VARCHAR(64) NOT NULL DEFAULT '',"
                    "task_id VARCHAR(64) NOT NULL,"
                    "task_path VARCHAR(256) NOT NULL DEFAULT '',"
                    "channel VARCHAR(256) NOT NULL,"
                    "value TEXT NOT NULL,"
                    "created_at DATETIME NOT NULL,"
                    "PRIMARY KEY (session_id, checkpoint_id, task_id, task_path, channel))"
                )
            )
            await conn.execute(text("DROP TABLE IF EXISTS checkpoint_blobs"))
            await conn.execute(
                text(
                    "CREATE TABLE checkpoint_blobs ("
                    "session_id VARCHAR(32), channel VARCHAR(64), "
                    "version VARCHAR(64), type VARCHAR(16) DEFAULT 'json', "
                    "payload TEXT, PRIMARY KEY (session_id, channel, version))"
                )
            )

        store = SQLiteCheckpointStore()

        # Three checkpoints: seq 1, 2, 3 with growing message lists.
        msgs_1 = [{"role": "user", "content": "Hello"}]
        msgs_2 = [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi there"}]
        msgs_3 = msgs_2 + [{"role": "user", "content": "How are you?"}]
        await store.save(Checkpoint(session_id=cs_id, sequence=1, messages=msgs_1, agent_id="a-1"))
        await store.save(Checkpoint(session_id=cs_id, sequence=2, messages=msgs_2, agent_id="a-1"))
        await store.save(Checkpoint(session_id=cs_id, sequence=3, messages=msgs_3, agent_id="a-1"))

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/workspaces/{ws_id}/sessions/{cs_id}/checkpoints/2/restore?mode=in_place",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            # In-place: session_id stays the same.
            assert body["session_id"] == cs_id
            assert body["mode"] == "in_place"

        # Checkpoints after seq 2 are deleted; seq 1 and 2 are preserved.
        remaining_cps = await store.list(cs_id)
        seqs = [c.sequence for c in remaining_cps]
        assert 3 not in seqs, f"seq 3 should be deleted, got {seqs}"
        assert 2 in seqs, f"seq 2 should be preserved, got {seqs}"
        assert 1 in seqs, f"seq 1 should be preserved, got {seqs}"

        # chat_messages match checkpoint 2's messages.
        async with async_session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT role, content FROM chat_messages "
                        "WHERE session_id = :sid ORDER BY id"
                    ),
                    {"sid": cs_id},
                )
            ).fetchall()
        contents = [(r.role, r.content) for r in rows]
        assert ("user", "Hello") in contents
        assert ("assistant", "Hi there") in contents
        assert ("user", "How are you?") not in contents, \
            "Messages after checkpoint 2 should be deleted"

    @pytest.mark.asyncio
    async def test_fork_preserves_prior_checkpoint_history(self, app):
        """Fork (default mode) must copy ALL checkpoints with sequence
        <= target to the new session, not just the target checkpoint.
        Without this, the forked session would only have one checkpoint
        and the inline button map would only show a button on the first
        user bubble."""
        suffix = _uuid.uuid4().hex[:8]
        ws_id = f"ws-fk-{suffix}"
        tid = f"t-fk-{suffix}"
        owner_id = f"owner-{suffix}"
        token = await _seed_workspace_with_owner(ws_id, tid, owner_id)

        async with async_session() as session:
            cs = ChatSession(workspace_id=ws_id, owner_id=owner_id, visibility="private")
            session.add(cs)
            await session.commit()
            await session.refresh(cs)
            cs_id = cs.id

        from sqlalchemy import text

        from src.infra.db.engine import engine
        from src.runtime.harness.checkpoint import Checkpoint, SQLiteCheckpointStore

        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS checkpoints ("
                    "session_id VARCHAR(32) NOT NULL,"
                    "sequence INTEGER NOT NULL,"
                    "messages TEXT NOT NULL,"
                    "tool_state TEXT NOT NULL,"
                    "agent_id VARCHAR(32) NOT NULL,"
                    "metadata TEXT NOT NULL DEFAULT '{}',"
                    "created_at DATETIME NOT NULL,"
                    "PRIMARY KEY (session_id, sequence))"
                )
            )
            await conn.execute(text("DROP TABLE IF EXISTS checkpoint_writes"))
            await conn.execute(
                text(
                    "CREATE TABLE checkpoint_writes ("
                    "session_id VARCHAR(64) NOT NULL,"
                    "checkpoint_id VARCHAR(64) NOT NULL DEFAULT '',"
                    "task_id VARCHAR(64) NOT NULL,"
                    "task_path VARCHAR(256) NOT NULL DEFAULT '',"
                    "channel VARCHAR(256) NOT NULL,"
                    "value TEXT NOT NULL,"
                    "created_at DATETIME NOT NULL,"
                    "PRIMARY KEY (session_id, checkpoint_id, task_id, task_path, channel))"
                )
            )
            await conn.execute(text("DROP TABLE IF EXISTS checkpoint_blobs"))
            await conn.execute(
                text(
                    "CREATE TABLE checkpoint_blobs ("
                    "session_id VARCHAR(32), channel VARCHAR(64), "
                    "version VARCHAR(64), type VARCHAR(16) DEFAULT 'json', "
                    "payload TEXT, PRIMARY KEY (session_id, channel, version))"
                )
            )

        store = SQLiteCheckpointStore()

        # Three checkpoints: seq 1, 2, 3 with growing message lists.
        msgs_1 = [{"role": "user", "content": "Hello"}]
        msgs_2 = msgs_1 + [{"role": "assistant", "content": "Hi there"}]
        msgs_3 = msgs_2 + [{"role": "user", "content": "How are you?"}]
        await store.save(Checkpoint(session_id=cs_id, sequence=1, messages=msgs_1, agent_id="a-1"))
        await store.save(Checkpoint(session_id=cs_id, sequence=2, messages=msgs_2, agent_id="a-1"))
        await store.save(Checkpoint(session_id=cs_id, sequence=3, messages=msgs_3, agent_id="a-1"))

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Fork from checkpoint 2 (default mode=fork).
            resp = await client.post(
                f"/api/v1/workspaces/{ws_id}/sessions/{cs_id}/checkpoints/2/restore",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            new_sid = body["session_id"]
            assert new_sid != cs_id, "Fork must create a new session"
            assert body["mode"] == "fork"

        # The forked session must have checkpoints 1 AND 2 (all <= target).
        new_cps = await store.list(new_sid)
        new_seqs = sorted(c.sequence for c in new_cps)
        assert 1 in new_seqs, f"Fork should preserve checkpoint 1, got {new_seqs}"
        assert 2 in new_seqs, f"Fork should preserve checkpoint 2, got {new_seqs}"
        assert 3 not in new_seqs, f"Fork should NOT include checkpoint 3, got {new_seqs}"

        # The forked session's messages should match checkpoint 2 (not 3).
        async with async_session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT role, content FROM chat_messages "
                        "WHERE session_id = :sid ORDER BY id"
                    ),
                    {"sid": new_sid},
                )
            ).fetchall()
        contents = [(r.role, r.content) for r in rows]
        assert ("user", "Hello") in contents
        assert ("assistant", "Hi there") in contents
        assert ("user", "How are you?") not in contents, \
            "Fork should only include messages up to checkpoint 2"
