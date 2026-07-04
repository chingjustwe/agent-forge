"""P3-5: Session 分享（指定成员可见）。

session owner 可分享给指定 workspace 成员；被分享的用户在 session 列表中
可见；取消分享后立即不可见。被分享用户只能 view（不能 mutate）。
"""
import uuid as _uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import (
    ChatSession,
    ChatSessionShare,
    Tenant,
    User,
    Workspace,
    WorkspaceMember,
)


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


async def _seed_workspace_with_two_members(
    tenant_id: str,
    ws_id: str,
    owner_id: str,
    member_id: str,
    owner_role: str = "member",
    member_role: str = "member",
) -> tuple[str, str]:
    """Seed tenant + workspace + owner + member. Returns (owner_tok, member_tok)."""
    async with async_session() as session:
        if not await session.get(Tenant, tenant_id):
            session.add(Tenant(id=tenant_id, name="T", domain=f"{tenant_id}.test"))
            await session.flush()
        if not await session.get(Workspace, ws_id):
            session.add(Workspace(id=ws_id, tenant_id=tenant_id, name=f"WS {ws_id}"))
            await session.flush()
        if not await session.get(User, owner_id):
            session.add(User(
                id=owner_id, tenant_id=tenant_id,
                email=f"{owner_id}@test.com", name=owner_id, role="member",
            ))
            await session.flush()
        if not await session.get(User, member_id):
            session.add(User(
                id=member_id, tenant_id=tenant_id,
                email=f"{member_id}@test.com", name=member_id, role="member",
            ))
            await session.flush()
        if not await session.get(WorkspaceMember, (ws_id, owner_id)):
            session.add(WorkspaceMember(workspace_id=ws_id, user_id=owner_id, role=owner_role))
        if not await session.get(WorkspaceMember, (ws_id, member_id)):
            session.add(WorkspaceMember(workspace_id=ws_id, user_id=member_id, role=member_role))
        await session.commit()
    return (
        _token(owner_id, tenant_id, email=f"{owner_id}@test.com"),
        _token(member_id, tenant_id, email=f"{member_id}@test.com"),
    )


async def _create_session(
    app, token: str, ws_id: str, visibility: str = "private", title: str = "Shared",
) -> dict:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/workspaces/{ws_id}/sessions",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": title, "visibility": visibility},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _list_sessions(app, token: str, ws_id: str) -> list[dict]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/v1/workspaces/{ws_id}/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    return resp.json()


class TestSessionShares:
    @pytest.mark.asyncio
    async def test_share_makes_session_visible_to_sharee(self, app):
        """分享后被分享用户能在列表看到该 session。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-sh-{suffix}"
        ws = f"ws-sh-{suffix}"
        owner_id = f"owner-{suffix}"
        member_id = f"member-{suffix}"
        owner_tok, member_tok = await _seed_workspace_with_two_members(
            tid, ws, owner_id, member_id
        )
        session = await _create_session(app, owner_tok, ws, visibility="private")

        # Member should NOT see the private session before sharing
        before = await _list_sessions(app, member_tok, ws)
        assert all(s["id"] != session["id"] for s in before)

        # Owner shares with member
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/sessions/{session['id']}/shares",
                headers={"Authorization": f"Bearer {owner_tok}"},
                json={"user_id": member_id},
            )
        assert resp.status_code == 201, resp.text

        # Member should now see the session
        after = await _list_sessions(app, member_tok, ws)
        ids = [s["id"] for s in after]
        assert session["id"] in ids

    @pytest.mark.asyncio
    async def test_unshare_removes_visibility(self, app):
        """取消分享后被分享用户立即不可见。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-un-{suffix}"
        ws = f"ws-un-{suffix}"
        owner_id = f"owner-{suffix}"
        member_id = f"member-{suffix}"
        owner_tok, member_tok = await _seed_workspace_with_two_members(
            tid, ws, owner_id, member_id
        )
        session = await _create_session(app, owner_tok, ws, visibility="private")

        # Share first
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                f"/api/v1/sessions/{session['id']}/shares",
                headers={"Authorization": f"Bearer {owner_tok}"},
                json={"user_id": member_id},
            )
            # Unshare
            del_resp = await ac.delete(
                f"/api/v1/sessions/{session['id']}/shares/{member_id}",
                headers={"Authorization": f"Bearer {owner_tok}"},
            )
        assert del_resp.status_code == 204

        # Member should no longer see the session
        after = await _list_sessions(app, member_tok, ws)
        assert all(s["id"] != session["id"] for s in after)

    @pytest.mark.asyncio
    async def test_non_owner_cannot_share(self, app):
        """非 owner/admin 不能分享 403。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-no-{suffix}"
        ws = f"ws-no-{suffix}"
        owner_id = f"owner-{suffix}"
        member_id = f"member-{suffix}"
        # member_role is "member" (not admin)
        owner_tok, member_tok = await _seed_workspace_with_two_members(
            tid, ws, owner_id, member_id, member_role="member"
        )
        session = await _create_session(app, owner_tok, ws, visibility="private")

        # Member tries to share the owner's session with themselves → 403
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/sessions/{session['id']}/shares",
                headers={"Authorization": f"Bearer {member_tok}"},
                json={"user_id": member_id},
            )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_share_to_non_workspace_member_400(self, app):
        """分享给非 workspace 成员 400。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-nw-{suffix}"
        ws = f"ws-nw-{suffix}"
        owner_id = f"owner-{suffix}"
        outsider_id = f"outsider-{suffix}"  # not a workspace member
        owner_tok, _ = await _seed_workspace_with_two_members(
            tid, ws, owner_id, f"member-{suffix}"
        )
        # Create the outsider user (same tenant, no workspace membership)
        async with async_session() as session:
            session.add(User(
                id=outsider_id, tenant_id=tid,
                email=f"{outsider_id}@test.com", name=outsider_id, role="member",
            ))
            await session.commit()
        session_data = await _create_session(app, owner_tok, ws, visibility="private")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/sessions/{session_data['id']}/shares",
                headers={"Authorization": f"Bearer {owner_tok}"},
                json={"user_id": outsider_id},
            )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_duplicate_share_is_idempotent(self, app):
        """重复分享幂等（不报错，shared_at 不更新）。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-dup-{suffix}"
        ws = f"ws-dup-{suffix}"
        owner_id = f"owner-{suffix}"
        member_id = f"member-{suffix}"
        owner_tok, _ = await _seed_workspace_with_two_members(
            tid, ws, owner_id, member_id
        )
        session = await _create_session(app, owner_tok, ws, visibility="private")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r1 = await ac.post(
                f"/api/v1/sessions/{session['id']}/shares",
                headers={"Authorization": f"Bearer {owner_tok}"},
                json={"user_id": member_id},
            )
            assert r1.status_code == 201
            first_shared_at = r1.json()["shared_at"]

            # Slight delay to ensure shared_at would differ if it were updated
            import time as _time
            _time.sleep(0.05)

            r2 = await ac.post(
                f"/api/v1/sessions/{session['id']}/shares",
                headers={"Authorization": f"Bearer {owner_tok}"},
                json={"user_id": member_id},
            )
            assert r2.status_code == 201  # idempotent — still 201
            second_shared_at = r2.json()["shared_at"]

        # shared_at must NOT be bumped on the duplicate share
        assert first_shared_at == second_shared_at

        # Only one share row in the DB
        async with async_session() as s:
            rows = (await s.execute(
                select(ChatSessionShare).where(
                    ChatSessionShare.session_id == session["id"],
                    ChatSessionShare.user_id == member_id,
                )
            )).scalars().all()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_list_shares(self, app):
        """列出分享对象。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-ls-{suffix}"
        ws = f"ws-ls-{suffix}"
        owner_id = f"owner-{suffix}"
        m1 = f"m1-{suffix}"
        m2 = f"m2-{suffix}"
        # Seed with two members
        owner_tok, _ = await _seed_workspace_with_two_members(tid, ws, owner_id, m1)
        async with async_session() as session:
            if not await session.get(User, m2):
                session.add(User(id=m2, tenant_id=tid, email=f"{m2}@test.com", name=m2, role="member"))
                await session.flush()
            if not await session.get(WorkspaceMember, (ws, m2)):
                session.add(WorkspaceMember(workspace_id=ws, user_id=m2, role="member"))
            await session.commit()
        session_data = await _create_session(app, owner_tok, ws, visibility="private")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                f"/api/v1/sessions/{session_data['id']}/shares",
                headers={"Authorization": f"Bearer {owner_tok}"},
                json={"user_id": m1},
            )
            await ac.post(
                f"/api/v1/sessions/{session_data['id']}/shares",
                headers={"Authorization": f"Bearer {owner_tok}"},
                json={"user_id": m2},
            )
            # List shares
            list_resp = await ac.get(
                f"/api/v1/sessions/{session_data['id']}/shares",
                headers={"Authorization": f"Bearer {owner_tok}"},
            )
        assert list_resp.status_code == 200
        share_user_ids = [s["user_id"] for s in list_resp.json()]
        assert set(share_user_ids) == {m1, m2}

    @pytest.mark.asyncio
    async def test_sharee_cannot_mutate_session(self, app):
        """被分享用户只能 view，不能 mutate（PATCH/DELETE）除非是 owner/admin。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-vm-{suffix}"
        ws = f"ws-vm-{suffix}"
        owner_id = f"owner-{suffix}"
        member_id = f"member-{suffix}"
        owner_tok, member_tok = await _seed_workspace_with_two_members(
            tid, ws, owner_id, member_id, member_role="member"
        )
        session = await _create_session(app, owner_tok, ws, visibility="private")

        # Owner shares with member
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            await ac.post(
                f"/api/v1/sessions/{session['id']}/shares",
                headers={"Authorization": f"Bearer {owner_tok}"},
                json={"user_id": member_id},
            )

            # Member can view (GET detail)
            get_resp = await ac.get(
                f"/api/v1/workspaces/{ws}/sessions/{session['id']}",
                headers={"Authorization": f"Bearer {member_tok}"},
            )
            assert get_resp.status_code == 200

            # Member CANNOT patch (title change)
            patch_resp = await ac.patch(
                f"/api/v1/workspaces/{ws}/sessions/{session['id']}",
                headers={"Authorization": f"Bearer {member_tok}"},
                json={"title": "Hacked"},
            )
            assert patch_resp.status_code == 403

            # Member CANNOT delete
            del_resp = await ac.delete(
                f"/api/v1/workspaces/{ws}/sessions/{session['id']}",
                headers={"Authorization": f"Bearer {member_tok}"},
            )
            assert del_resp.status_code == 403

    @pytest.mark.asyncio
    async def test_share_to_self_idempotent(self, app):
        """分享给自己幂等（不报错）。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-self-{suffix}"
        ws = f"ws-self-{suffix}"
        owner_id = f"owner-{suffix}"
        owner_tok, _ = await _seed_workspace_with_two_members(
            tid, ws, owner_id, f"member-{suffix}"
        )
        session = await _create_session(app, owner_tok, ws, visibility="private")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Owner shares with themselves → idempotent, no error
            resp = await ac.post(
                f"/api/v1/sessions/{session['id']}/shares",
                headers={"Authorization": f"Bearer {owner_tok}"},
                json={"user_id": owner_id},
            )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_workspace_admin_can_share_others_session(self, app):
        """workspace_admin 可以分享别人的 session（admin 权限）。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-adm-{suffix}"
        ws = f"ws-adm-{suffix}"
        owner_id = f"owner-{suffix}"
        admin_id = f"admin-{suffix}"
        member_id = f"member-{suffix}"
        # Seed owner (member) + admin (workspace_admin) + member
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            session.add(Workspace(id=ws, tenant_id=tid, name=f"WS {ws}"))
            session.add(User(id=owner_id, tenant_id=tid, email=f"{owner_id}@test.com",
                             name=owner_id, role="member"))
            session.add(User(id=admin_id, tenant_id=tid, email=f"{admin_id}@test.com",
                             name=admin_id, role="member"))
            session.add(User(id=member_id, tenant_id=tid, email=f"{member_id}@test.com",
                             name=member_id, role="member"))
            await session.flush()
            session.add(WorkspaceMember(workspace_id=ws, user_id=owner_id, role="member"))
            session.add(WorkspaceMember(workspace_id=ws, user_id=admin_id, role="workspace_admin"))
            session.add(WorkspaceMember(workspace_id=ws, user_id=member_id, role="member"))
            await session.commit()
        owner_tok = _token(owner_id, tid, email=f"{owner_id}@test.com")
        admin_tok = _token(admin_id, tid, email=f"{admin_id}@test.com")

        session_data = await _create_session(app, owner_tok, ws, visibility="private")

        # Admin shares owner's session with member
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/sessions/{session_data['id']}/shares",
                headers={"Authorization": f"Bearer {admin_tok}"},
                json={"user_id": member_id},
            )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_share_nonexistent_session_404(self, app):
        """分享不存在的 session 404。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-nf-{suffix}"
        ws = f"ws-nf-{suffix}"
        owner_tok, _ = await _seed_workspace_with_two_members(
            tid, ws, f"owner-{suffix}", f"member-{suffix}"
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/sessions/nonexistent-session/shares",
                headers={"Authorization": f"Bearer {owner_tok}"},
                json={"user_id": f"member-{suffix}"},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_share_to_nonexistent_user_400(self, app):
        """分享给不存在的用户 400。"""
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-nu-{suffix}"
        ws = f"ws-nu-{suffix}"
        owner_tok, _ = await _seed_workspace_with_two_members(
            tid, ws, f"owner-{suffix}", f"member-{suffix}"
        )
        session = await _create_session(app, owner_tok, ws, visibility="private")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/sessions/{session['id']}/shares",
                headers={"Authorization": f"Bearer {owner_tok}"},
                json={"user_id": "nonexistent-user"},
            )
        assert resp.status_code == 400
