"""ChatSession + ChatMessage REST endpoints.

P1-1: persistent chat sessions with workspace-scoped visibility.

Visibility rules for listing / detail:
- ``tenant_admin`` short-circuits (sees everything in every workspace).
- ``workspace_owner`` / ``workspace_admin`` see all sessions in their workspace.
- ``member`` sees sessions where ``owner_id == self`` OR ``visibility != 'private'``
  OR sessions shared with them via ``ChatSessionShare`` (P3-5).

Mutation rules (PATCH / DELETE):
- The session ``owner_id`` may always patch/delete their own session.
- ``workspace_admin`` / ``workspace_owner`` (and ``tenant_admin``) may patch or
  delete any session in their workspace.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.rbac import get_workspace_member_role
from src.infra.db.models import ChatMessage, ChatSession, ChatSessionShare, User
from src.infra.db.session import get_db

router = APIRouter()


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------
class CreateSessionRequest(BaseModel):
    title: str | None = None
    visibility: str | None = None
    agent_name: str | None = None


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    visibility: str | None = None


class CreateShareRequest(BaseModel):
    user_id: str


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------
def _serialize_session(cs: ChatSession) -> dict:
    return {
        "id": cs.id,
        "workspace_id": cs.workspace_id,
        "owner_id": cs.owner_id,
        "title": cs.title,
        "visibility": cs.visibility,
        "agent_name": cs.agent_name,
        "archived": bool(cs.archived),
        "created_at": cs.created_at.isoformat() if cs.created_at else None,
        "updated_at": cs.updated_at.isoformat() if cs.updated_at else None,
    }


def _serialize_message(msg: ChatMessage) -> dict:
    return {
        "id": msg.id,
        "session_id": msg.session_id,
        "role": msg.role,
        "content": msg.content,
        "tokens": msg.tokens,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


def _serialize_share(
    share: ChatSessionShare,
    user_email: str | None = None,
    user_name: str | None = None,
) -> dict:
    # P3-5 前端集成：附带被分享用户的 email/name，避免前端再发一次
    # members 请求交叉引用。
    return {
        "session_id": share.session_id,
        "user_id": share.user_id,
        "user_email": user_email,
        "user_name": user_name,
        "shared_by": share.shared_by,
        "shared_at": share.shared_at.isoformat() if share.shared_at else None,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _can_see_session(
    cs: ChatSession,
    user_id: str,
    role: str | None,
    tenant_role: str,
    shared_session_ids: set[str] | None = None,
) -> bool:
    """Apply the visibility matrix to a single session row.

    P3-5: a private session is also visible if it has been shared with the
    current user (``ChatSessionShare.user_id == user_id``). Callers pass the
    set of session_ids shared with the current user via ``shared_session_ids``.
    """
    if tenant_role == "tenant_admin":
        return True
    if role in ("workspace_admin",):
        return True
    if cs.owner_id == user_id:
        return True
    if shared_session_ids and cs.id in shared_session_ids:
        return True
    return cs.visibility != "private"


def _can_mutate(cs: ChatSession, user_id: str, role: str | None, tenant_role: str) -> bool:
    if tenant_role == "tenant_admin":
        return True
    if role in ("workspace_admin",):
        return True
    return cs.owner_id == user_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post("/api/v1/workspaces/{workspace_id}/sessions")
async def create_session(
    workspace_id: str,
    body: CreateSessionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )

    role = await get_workspace_member_role(workspace_id, user, db)
    if role is None:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Not a member of this workspace",
                }
            },
        )

    user_id = user.get("sub") or user.get("id", "")
    cs = ChatSession(
        workspace_id=workspace_id,
        owner_id=user_id,
        title=body.title or "New Chat",
        visibility=body.visibility or "private",
        agent_name=body.agent_name,
    )
    db.add(cs)
    await db.commit()
    await db.refresh(cs)
    return JSONResponse(status_code=201, content=_serialize_session(cs))


@router.get("/api/v1/workspaces/{workspace_id}/sessions")
async def list_sessions(
    workspace_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )

    role = await get_workspace_member_role(workspace_id, user, db)
    if role is None:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Not a member of this workspace",
                }
            },
        )

    user_id = user.get("sub") or user.get("id", "")
    tenant_role = user.get("role", "member")

    # P3-5: pre-fetch the set of session_ids shared with the current user so
    # _can_see_session can include shared private sessions in one pass.
    shared_ids: set[str] = set()
    if tenant_role != "tenant_admin" and role not in ("workspace_admin",):
        share_rows = await db.execute(
            select(ChatSessionShare.session_id).where(
                ChatSessionShare.user_id == user_id
            )
        )
        shared_ids = {r[0] for r in share_rows.all()}

    result = await db.execute(
        select(ChatSession)
        .where(
            ChatSession.workspace_id == workspace_id,
            ChatSession.archived == 0,
        )
        .order_by(ChatSession.updated_at.desc())
    )
    rows = result.scalars().all()
    visible = [
        _serialize_session(cs)
        for cs in rows
        if _can_see_session(cs, user_id, role, tenant_role, shared_ids)
    ]
    return visible


@router.get("/api/v1/workspaces/{workspace_id}/sessions/{session_id}")
async def get_session(
    workspace_id: str,
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )

    role = await get_workspace_member_role(workspace_id, user, db)
    if role is None:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Not a member of this workspace",
                }
            },
        )

    cs = await db.get(ChatSession, session_id)
    if not cs or cs.workspace_id != workspace_id or cs.archived:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Session not found"}},
        )

    user_id = user.get("sub") or user.get("id", "")
    tenant_role = user.get("role", "member")

    # P3-5: check if this specific session is shared with the current user.
    shared_ids: set[str] = set()
    if tenant_role != "tenant_admin" and role not in ("workspace_admin",) and cs.owner_id != user_id:
        share_row = await db.get(ChatSessionShare, (session_id, user_id))
        if share_row is not None:
            shared_ids.add(session_id)

    if not _can_see_session(cs, user_id, role, tenant_role, shared_ids):
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "You cannot view this session",
                }
            },
        )

    msg_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    messages = [_serialize_message(m) for m in msg_result.scalars().all()]
    return {"session": _serialize_session(cs), "messages": messages}


@router.patch("/api/v1/workspaces/{workspace_id}/sessions/{session_id}")
async def update_session(
    workspace_id: str,
    session_id: str,
    body: UpdateSessionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )

    role = await get_workspace_member_role(workspace_id, user, db)
    if role is None:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Not a member of this workspace",
                }
            },
        )

    cs = await db.get(ChatSession, session_id)
    if not cs or cs.workspace_id != workspace_id or cs.archived:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Session not found"}},
        )

    user_id = user.get("sub") or user.get("id", "")
    tenant_role = user.get("role", "member")
    if not _can_mutate(cs, user_id, role, tenant_role):
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Only the owner or a workspace admin may modify this session",
                }
            },
        )

    if body.title is not None:
        cs.title = body.title
    if body.visibility is not None:
        cs.visibility = body.visibility
    await db.commit()
    await db.refresh(cs)
    return _serialize_session(cs)


@router.delete("/api/v1/workspaces/{workspace_id}/sessions/{session_id}")
async def delete_session(
    workspace_id: str,
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )

    role = await get_workspace_member_role(workspace_id, user, db)
    if role is None:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Not a member of this workspace",
                }
            },
        )

    cs = await db.get(ChatSession, session_id)
    if not cs or cs.workspace_id != workspace_id:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Session not found"}},
        )

    user_id = user.get("sub") or user.get("id", "")
    tenant_role = user.get("role", "member")
    if not _can_mutate(cs, user_id, role, tenant_role):
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Only the owner or a workspace admin may delete this session",
                }
            },
        )

    cs.archived = 1
    await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# P3-5: Session sharing (per-user visibility grants)
# ---------------------------------------------------------------------------
# These endpoints are workspace-agnostic in the URL (no workspace_id path
# param) — the workspace is resolved from the session row. Permission to
# share is granted to the session owner OR workspace_admin/owner OR
# tenant_admin (the same _can_mutate matrix).
@router.post("/api/v1/sessions/{session_id}/shares")
async def create_share(
    session_id: str,
    body: CreateShareRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Share a session with a specific workspace member.

    The caller must be the session owner or a workspace_admin/owner (or
    tenant_admin). The target user must be a member of the session's
    workspace (otherwise 400). Re-sharing with the same user is idempotent:
    the existing share row is returned WITHOUT bumping ``shared_at``.
    """
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )

    cs = await db.get(ChatSession, session_id)
    if not cs or cs.archived:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Session not found"}},
        )

    role = await get_workspace_member_role(cs.workspace_id, user, db)
    if role is None:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Not a member of this workspace",
                }
            },
        )

    user_id = user.get("sub") or user.get("id", "")
    tenant_role = user.get("role", "member")
    if not _can_mutate(cs, user_id, role, tenant_role):
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Only the owner or a workspace admin may share this session",
                }
            },
        )

    # Target user must be a member of the session's workspace. Query
    # WorkspaceMember directly (we don't want get_workspace_member_role's
    # tenant_admin short-circuit here — we're checking the TARGET user, not
    # the caller).
    from src.infra.db.models import WorkspaceMember

    target_user = await db.get(User, body.user_id)
    if not target_user:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "BAD_REQUEST",
                    "message": "Target user does not exist",
                }
            },
        )
    target_membership = await db.get(WorkspaceMember, (cs.workspace_id, body.user_id))
    if not target_membership:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "BAD_REQUEST",
                    "message": "Target user is not a member of this workspace",
                }
            },
        )

    # Idempotent: if a share row already exists, return it without bumping
    # shared_at (composite PK prevents a duplicate INSERT).
    existing = await db.get(ChatSessionShare, (session_id, body.user_id))
    if existing is not None:
        return JSONResponse(
            status_code=201,
            content=_serialize_share(
                existing,
                user_email=target_user.email,
                user_name=target_user.name,
            ),
        )

    share = ChatSessionShare(
        session_id=session_id,
        user_id=body.user_id,
        shared_by=user_id,
    )
    db.add(share)
    await db.commit()
    await db.refresh(share)
    return JSONResponse(
        status_code=201,
        content=_serialize_share(
            share,
            user_email=target_user.email,
            user_name=target_user.name,
        ),
    )


@router.get("/api/v1/sessions/{session_id}/shares")
async def list_shares(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all users the session is shared with (owner/admin only)."""
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )

    cs = await db.get(ChatSession, session_id)
    if not cs or cs.archived:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Session not found"}},
        )

    role = await get_workspace_member_role(cs.workspace_id, user, db)
    if role is None:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Not a member of this workspace",
                }
            },
        )

    user_id = user.get("sub") or user.get("id", "")
    tenant_role = user.get("role", "member")
    if not _can_mutate(cs, user_id, role, tenant_role):
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Only the owner or a workspace admin may list shares",
                }
            },
        )

    result = await db.execute(
        select(ChatSessionShare, User.email, User.name)
        .join(User, User.id == ChatSessionShare.user_id)
        .where(ChatSessionShare.session_id == session_id)
        .order_by(ChatSessionShare.shared_at.asc())
    )
    return [
        _serialize_share(s, user_email=email, user_name=name)
        for s, email, name in result.all()
    ]


@router.delete("/api/v1/sessions/{session_id}/shares/{user_id}")
async def delete_share(
    session_id: str,
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Revoke a share (owner/admin only). Idempotent — 204 even if no row."""
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )

    cs = await db.get(ChatSession, session_id)
    if not cs or cs.archived:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Session not found"}},
        )

    role = await get_workspace_member_role(cs.workspace_id, user, db)
    if role is None:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Not a member of this workspace",
                }
            },
        )

    caller_id = user.get("sub") or user.get("id", "")
    tenant_role = user.get("role", "member")
    if not _can_mutate(cs, caller_id, role, tenant_role):
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Only the owner or a workspace admin may revoke shares",
                }
            },
        )

    share = await db.get(ChatSessionShare, (session_id, user_id))
    if share is not None:
        await db.delete(share)
        await db.commit()
    return Response(status_code=204)
