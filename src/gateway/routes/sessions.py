"""ChatSession + ChatMessage REST endpoints.

P1-1: persistent chat sessions with workspace-scoped visibility.

Visibility rules for listing / detail:
- ``tenant_admin`` short-circuits (sees everything in every workspace).
- ``workspace_owner`` / ``workspace_admin`` see all sessions in their workspace.
- ``member`` sees sessions where ``owner_id == self`` OR ``visibility != 'private'``.

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
from src.infra.db.models import ChatMessage, ChatSession
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _can_see_session(cs: ChatSession, user_id: str, role: str | None, tenant_role: str) -> bool:
    """Apply the visibility matrix to a single session row."""
    if tenant_role == "tenant_admin":
        return True
    if role in ("workspace_owner", "workspace_admin"):
        return True
    if cs.owner_id == user_id:
        return True
    return cs.visibility != "private"


def _can_mutate(cs: ChatSession, user_id: str, role: str | None, tenant_role: str) -> bool:
    if tenant_role == "tenant_admin":
        return True
    if role in ("workspace_owner", "workspace_admin"):
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
        if _can_see_session(cs, user_id, role, tenant_role)
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
    if not _can_see_session(cs, user_id, role, tenant_role):
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
