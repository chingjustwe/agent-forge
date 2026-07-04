"""P3-1: 活跃 workspace 排序。

Switcher 中最近用的 workspace 排在前面；默认 workspace 始终在第一位
（即使不活跃）。chat 请求成功后更新 WorkspaceMember.last_active_at。
"""
import uuid as _uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import (
    Tenant,
    User,
    Workspace,
    WorkspaceMember,
)


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


def _token(user_id: str, tenant_id: str, role: str = "member") -> str:
    return create_jwt({
        "id": user_id,
        "sub": user_id,
        "tenant_id": tenant_id,
        "email": f"{user_id}@test.com",
        "role": role,
    })


async def _seed_user_in_workspaces(
    tenant_id: str,
    user_id: str,
    memberships: list[tuple[str, str, datetime | None]],  # (ws_id, ws_name, last_active_at)
    tenant_role: str = "member",
) -> str:
    """Seed tenant + user + multiple workspace memberships. Returns JWT."""
    async with async_session() as session:
        if not await session.get(Tenant, tenant_id):
            session.add(Tenant(id=tenant_id, name="T", domain=f"{tenant_id}.test"))
            await session.flush()
        if not await session.get(User, user_id):
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=f"{user_id}@test.com",
                    name=user_id,
                    role=tenant_role,
                )
            )
            await session.flush()
        for ws_id, ws_name, last_active in memberships:
            if not await session.get(Workspace, ws_id):
                session.add(
                    Workspace(id=ws_id, tenant_id=tenant_id, name=ws_name)
                )
                await session.flush()
            if not await session.get(WorkspaceMember, (ws_id, user_id)):
                session.add(
                    WorkspaceMember(
                        workspace_id=ws_id,
                        user_id=user_id,
                        role="member",
                        last_active_at=last_active,
                    )
                )
        await session.commit()
    return _token(user_id, tenant_id, role=tenant_role)


def _ids(body: list[dict]) -> list[str]:
    return [w["id"] for w in body]


# ---------------------------------------------------------------------------
# 1. 默认 workspace 始终排第一
# ---------------------------------------------------------------------------
class TestWorkspaceOrdering:
    @pytest.mark.asyncio
    async def test_default_workspace_always_first(self, app):
        """即使默认 workspace 没有 last_active_at，也排在第一位。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-ord-def-{suffix}"
        uid = f"u-ord-def-{suffix}"
        ws_default = f"ws-def-{suffix}"
        ws_recent = f"ws-recent-{suffix}"
        now = datetime.now(timezone.utc)

        tok = await _seed_user_in_workspaces(
            tid, uid,
            [
                (ws_default, "Default WS", None),
                (ws_recent, "Recent WS", now),
            ],
        )
        # Mark ws_default as is_default=1
        async with async_session() as session:
            ws = await session.get(Workspace, ws_default)
            ws.is_default = 1
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/me/workspaces",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        ids = _ids(resp.json())
        assert ids[0] == ws_default
        assert ws_recent in ids[1:]

    @pytest.mark.asyncio
    async def test_most_recently_active_first(self, app):
        """最近活跃的 workspace（last_active_at 较大）排在前面。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-ord-rec-{suffix}"
        uid = f"u-ord-rec-{suffix}"
        ws_old = f"ws-old-{suffix}"
        ws_mid = f"ws-mid-{suffix}"
        ws_new = f"ws-new-{suffix}"
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(hours=10)
        mid_time = now - timedelta(hours=1)

        tok = await _seed_user_in_workspaces(
            tid, uid,
            [
                (ws_old, "Old", old_time),
                (ws_mid, "Mid", mid_time),
                (ws_new, "New", now),
            ],
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/me/workspaces",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        ids = _ids(resp.json())
        # No default workspace → order strictly by last_active_at DESC
        assert ids[0] == ws_new
        assert ids[1] == ws_mid
        assert ids[2] == ws_old

    @pytest.mark.asyncio
    async def test_null_last_active_at_goes_last(self, app):
        """无 last_active_at 的 workspace 排在最后（按 name ASC 兜底）。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-ord-null-{suffix}"
        uid = f"u-ord-null-{suffix}"
        ws_active = f"ws-active-{suffix}"
        ws_null_a = f"ws-null-a-{suffix}"
        ws_null_b = f"ws-null-b-{suffix}"
        now = datetime.now(timezone.utc)

        tok = await _seed_user_in_workspaces(
            tid, uid,
            [
                (ws_active, "Active", now),
                # name="Zeta" — should come after "Alpha" among nulls
                (ws_null_a, "Zeta", None),
                (ws_null_b, "Alpha", None),
            ],
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/me/workspaces",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        ids = _ids(resp.json())
        # active first, then nulls ordered by name ASC (Alpha before Zeta)
        assert ids[0] == ws_active
        assert ids[1] == ws_null_b  # Alpha
        assert ids[2] == ws_null_a  # Zeta

    @pytest.mark.asyncio
    async def test_chat_updates_last_active_at(self, app, monkeypatch):
        """chat 请求成功后，对应 workspace_member 的 last_active_at 应被更新。"""
        import os
        monkeypatch.setenv("LLM_API_KEY", "test-key")

        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-ord-chat-{suffix}"
        uid = f"u-ord-chat-{suffix}"
        ws_id = f"ws-ord-chat-{suffix}"
        old_time = datetime.now(timezone.utc) - timedelta(days=10)

        tok = await _seed_user_in_workspaces(
            tid, uid, [(ws_id, "Chat WS", old_time)],
        )

        # Stub the DirectLLMAdapter to avoid real LLM calls — emit one text
        # event then end the stream.
        from src.runtime.models import StreamEvent

        async def _fake_run(self, ctx, messages, state):
            yield StreamEvent(type="text", data={"content": "hi"})
            yield StreamEvent(
                type="status",
                data={"usage": {"input_tokens": 1, "output_tokens": 1}},
            )

        from src.runtime.adapters.direct_llm import DirectLLMAdapter
        monkeypatch.setattr(DirectLLMAdapter, "run", _fake_run)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/chat",
                headers={"Authorization": f"Bearer {tok}"},
                json={
                    "config": {"workspace_id": ws_id, "model": "stub"},
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
            # Drain the SSE stream so the post-handler runs.
            await resp.aread()

        # Verify last_active_at was bumped.
        async with async_session() as session:
            wm = await session.get(WorkspaceMember, (ws_id, uid))
            assert wm is not None
            assert wm.last_active_at is not None
            # Should be newer than the seeded old_time.
            assert wm.last_active_at.replace(tzinfo=None) > old_time.replace(tzinfo=None)

    @pytest.mark.asyncio
    async def test_default_first_then_recent_then_null(self, app):
        """综合排序：is_default DESC → last_active_at DESC NULLS LAST → name ASC。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-ord-mix-{suffix}"
        uid = f"u-ord-mix-{suffix}"
        ws_default = f"ws-default-{suffix}"  # is_default=1, last_active=None
        ws_recent = f"ws-recent-{suffix}"    # is_default=0, last_active=now
        ws_null = f"ws-null-{suffix}"        # is_default=0, last_active=None
        now = datetime.now(timezone.utc)

        tok = await _seed_user_in_workspaces(
            tid, uid,
            [
                (ws_default, "Default", None),
                (ws_recent, "Recent", now),
                (ws_null, "Null", None),
            ],
        )
        async with async_session() as session:
            ws = await session.get(Workspace, ws_default)
            ws.is_default = 1
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/me/workspaces",
                headers={"Authorization": f"Bearer {tok}"},
            )
        assert resp.status_code == 200
        ids = _ids(resp.json())
        # default first, then recent (has last_active_at), then null
        assert ids == [ws_default, ws_recent, ws_null]
