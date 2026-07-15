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
from typing import Any

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
def _serialize_session(cs: ChatSession, owner_name: str | None = None) -> dict:
    return {
        "id": cs.id,
        "workspace_id": cs.workspace_id,
        "owner_id": cs.owner_id,
        "owner_name": owner_name or cs.owner_id[:8],
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
        "steps": msg.steps,
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
async def _resolve_owner_names(
    db: AsyncSession, owner_ids: set[str],
) -> dict[str, str]:
    """Batch-resolve owner_id → owner_name from the users table."""
    if not owner_ids:
        return {}
    result = await db.execute(
        select(User.id, User.name).where(User.id.in_(owner_ids))
    )
    return {row[0]: row[1] for row in result.all()}


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
    owner_name = user.get("name", "")
    return JSONResponse(status_code=201, content=_serialize_session(cs, owner_name))


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
        cs for cs in rows
        if _can_see_session(cs, user_id, role, tenant_role, shared_ids)
    ]
    owner_names = await _resolve_owner_names(
        db, {cs.owner_id for cs in visible}
    )
    return [_serialize_session(cs, owner_names.get(cs.owner_id)) for cs in visible]


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
    owner_names = await _resolve_owner_names(db, {cs.owner_id})
    return {"session": _serialize_session(cs, owner_names.get(cs.owner_id)), "messages": messages}


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
    owner_names = await _resolve_owner_names(db, {cs.owner_id})
    return _serialize_session(cs, owner_names.get(cs.owner_id))


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


# ---------------------------------------------------------------------------
# Wave 2: Manual checkpoint restore (deepagents sessions)
# ---------------------------------------------------------------------------

class CheckpointInfo(BaseModel):
    sequence: int
    created_at: str | None = None
    message_count: int = 0
    preview: str = ""


class RestoreResponse(BaseModel):
    session_id: str
    title: str
    restored_from_session_id: str
    restored_from_sequence: int
    mode: str = "fork"


def _checkpoint_preview(messages: list[dict]) -> str:
    """Build a short preview from a checkpoint's messages.

    Each checkpoint stores the FULL cumulative conversation up to that
    turn, so using the *first* user message would make every restore
    point preview identical (the opening prompt). Instead use the *last*
    user message — the prompt that produced this checkpoint's turn — so
    each restore point shows the prompt it actually represents.
    """
    if not messages:
        return ""
    last_user = ""
    last = ""
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if content:
            last = content
        if role == "user" and content:
            last_user = content
    if last_user:
        part = last_user[:40].replace("\n", " ")
        return f"用户：{part}{'…' if len(last_user) > 40 else ''}"
    if last:
        part = last[:40].replace("\n", " ")
        return f"助手：{part}{'…' if len(last) > 40 else ''}"
    return ""


@router.get(
    "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/checkpoints",
    response_model=list[CheckpointInfo],
)
async def list_checkpoints(
    workspace_id: str,
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List checkpoints for a session (deepagents only).

    Returns checkpoints ordered by sequence (oldest first). Each entry
    includes a short preview derived from the checkpoint's messages.
    """
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

    # Load checkpoints via the framework-agnostic store.
    from src.runtime.adapters.langgraph_bridge import LangGraphCheckpointShim
    from src.runtime.harness.checkpoint import SQLiteCheckpointStore

    store = SQLiteCheckpointStore()
    cps = await store.list(session_id)
    shim = LangGraphCheckpointShim(store, session_id, "")

    # Reconstruct the full message list for each checkpoint. The stored
    # ``cp.messages`` column is the authoritative snapshot — if empty,
    # ``reconstruct_checkpoint_messages`` returns ``[]`` (not the full
    # session writes) so empty checkpoints are filtered out below.
    rebuilt: list[tuple[Any, list[dict]]] = []
    for cp in cps:
        messages = await shim.reconstruct_checkpoint_messages(cp)
        rebuilt.append((cp, messages))

    # Filter to one restore point per user turn. LangGraph writes one
    # checkpoint per graph-node execution, so a single user prompt
    # produces several internal rows (start/agent/tools/end) with
    # monotonically increasing message counts. Group checkpoints by the
    # number of USER messages in the reconstructed conversation — each
    # distinct count is one prompt/turn — and keep the LAST (most
    # complete) checkpoint of each group. This collapses the ~5 internal
    # rows per prompt down to a single, meaningful restore point.
    result: list[CheckpointInfo] = []
    seen_user_counts: set[int] = set()
    for cp, messages in reversed(rebuilt):
        user_count = sum(1 for m in messages if m.get("role") == "user")
        if user_count not in seen_user_counts:
            seen_user_counts.add(user_count)
            preview = _checkpoint_preview(messages)
            result.append(CheckpointInfo(
                sequence=cp.sequence,
                created_at=cp.created_at.isoformat() if cp.created_at else None,
                message_count=len(messages),
                preview=preview,
            ))
    # Built newest-first above; return oldest-first.
    result.reverse()
    return result


async def _reseed_checkpoint_sql(
    *,
    db: AsyncSession,
    shim: "LangGraphCheckpointShim",
    session_id: str,
    sequence: int,
    messages: list[dict],
    agent_id: str,
    metadata: dict,
) -> str:
    """Re-seed a checkpoint + per-message writes via raw SQL on the given
    ``db`` session (single transaction, no separate connections).

    Returns the new LangGraph ``checkpoint_id`` (UUID hex) so callers can
    reference it if needed. All inserts use ``INSERT OR REPLACE`` so this
    can overwrite an existing checkpoint at the same sequence (in-place
    restore) or create a fresh one (fork).

    This replaces ``store.save`` + ``shim.aput_writes`` which each opened
    their own ``async_session()`` and committed independently — that
    non-atomic behavior caused garbled history after multiple restores
    (deletions on the request session could roll back while the re-seed
    on separate connections persisted).
    """
    import base64 as _b64
    import json as _json
    import uuid as _uuid
    from datetime import datetime, timezone
    from sqlalchemy import text as _sa_text

    if not messages:
        return ""

    lc_messages = shim._to_langchain_messages(messages)
    new_checkpoint_id = _uuid.uuid4().hex
    envelope = {
        "v": 1,
        "id": new_checkpoint_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel_values": {},
        "channel_versions": {
            "messages": 0,
            "__start__": 1,
        },
        "versions_seen": {},
        "pending_sends": [],
    }
    type_str, payload_bytes = shim.serde.dumps_typed(envelope)
    encoded = {
        "type": type_str,
        "payload_b64": _b64.b64encode(payload_bytes).decode("ascii"),
        "checkpoint_id": new_checkpoint_id,
    }

    # Insert/replace the checkpoint row.
    await db.execute(
        _sa_text(
            "INSERT OR REPLACE INTO checkpoints "
            "(session_id, sequence, messages, tool_state, agent_id, metadata, created_at) "
            "VALUES (:sid, :seq, :msg, :ts, :aid, :meta, :cat)"
        ),
        {
            "sid": session_id,
            "seq": sequence,
            "msg": _json.dumps(messages),
            "ts": _json.dumps({"langgraph_checkpoint": encoded}),
            "aid": agent_id,
            "meta": _json.dumps(metadata),
            "cat": datetime.now(timezone.utc).isoformat(),
        },
    )

    # Seed per-message writes scoped to the new checkpoint_id.
    now_iso = datetime.now(timezone.utc).isoformat()
    for idx, lc_msg in enumerate(lc_messages, start=1):
        w_type, w_payload = shim.serde.dumps_typed([lc_msg])
        w_value = _json.dumps({
            "type": w_type,
            "payload_b64": _b64.b64encode(w_payload).decode("ascii"),
        })
        await db.execute(
            _sa_text(
                "INSERT OR REPLACE INTO checkpoint_writes "
                "(session_id, checkpoint_id, task_id, task_path, channel, value, created_at) "
                "VALUES (:sid, :cid, :tid, :tp, :ch, :val, :cat)"
            ),
            {
                "sid": session_id,
                "cid": new_checkpoint_id,
                "tid": f"r{idx}",
                "tp": "",
                "ch": "messages",
                "val": w_value,
                "cat": now_iso,
            },
        )

    return new_checkpoint_id


async def _restore_in_place(
    *,
    db: AsyncSession,
    store: "SQLiteCheckpointStore",
    shim: "LangGraphCheckpointShim",
    cs: ChatSession,
    session_id: str,
    sequence: int,
    target_cp: "Checkpoint",
    messages: list[dict],
) -> RestoreResponse:
    """Rollback the current session to ``sequence`` in-place.

    Strategy: delete ALL LangGraph state (checkpoints/writes/blobs) and
    re-seed a single fresh checkpoint from the target's messages. This
    avoids the fragile "selectively keep old writes" approach which breaks
    because old writes reference checkpoint_ids that may not survive the
    truncation. The re-seeded checkpoint gives the agent a clean, complete
    conversation state to continue from.

    - Checkpoints with sequence <= N are kept for UI restore history.
      Their ``messages`` snapshots are self-contained and don't depend on
      writes/blobs.
    - All writes/blobs are wiped and re-seeded for the target checkpoint.
    - chat_messages are truncated to match the target checkpoint.

    All operations use the request's ``db`` session (single transaction)
    to guarantee atomicity — previously ``store.save`` and
    ``shim.aput_writes`` used separate connections that committed
    independently, causing garbled history if the main transaction failed.
    """
    from sqlalchemy import text as _sa_text

    # 1. Delete ALL checkpoint_writes and checkpoint_blobs for this session.
    await db.execute(
        _sa_text("DELETE FROM checkpoint_writes WHERE session_id = :sid"),
        {"sid": session_id},
    )
    await db.execute(
        _sa_text("DELETE FROM checkpoint_blobs WHERE session_id = :sid"),
        {"sid": session_id},
    )

    # 2. Delete checkpoint rows with sequence > N (future checkpoints).
    await db.execute(
        _sa_text(
            "DELETE FROM checkpoints WHERE session_id = :sid AND sequence > :seq"
        ),
        {"sid": session_id, "seq": sequence},
    )

    # 3. Truncate chat_messages and re-insert from target checkpoint.
    await db.execute(
        _sa_text("DELETE FROM chat_messages WHERE session_id = :sid"),
        {"sid": session_id},
    )
    for msg in messages:
        db.add(ChatMessage(
            session_id=session_id,
            role=msg.get("role", "user"),
            content=msg.get("content", ""),
            tokens=0,
        ))

    # 4. Re-seed the target checkpoint with a fresh LangGraph envelope
    #    and per-message writes — all via raw SQL on the same db session.
    if messages:
        source_metadata = target_cp.metadata if isinstance(target_cp.metadata, dict) else {}
        new_metadata = {
            "step": source_metadata.get("step", 0),
            "source": "restore_in_place",
            "restored_from_sequence": sequence,
        }
        await _reseed_checkpoint_sql(
            db=db,
            shim=shim,
            session_id=session_id,
            sequence=sequence,
            messages=messages,
            agent_id=cs.agent_name or "",
            metadata=new_metadata,
        )

    await db.commit()

    return RestoreResponse(
        session_id=session_id,
        title=cs.title,
        restored_from_session_id=session_id,
        restored_from_sequence=sequence,
        mode="in_place",
    )


@router.post(
    "/api/v1/workspaces/{workspace_id}/sessions/{session_id}/checkpoints/{sequence}/restore",
    response_model=RestoreResponse,
)
async def restore_checkpoint(
    workspace_id: str,
    session_id: str,
    sequence: int,
    request: Request,
    mode: str = "fork",
    db: AsyncSession = Depends(get_db),
):
    """Restore from a checkpoint in one of two modes:

    - ``mode=fork`` (default): creates a NEW session seeded with the
      checkpoint's messages. The original session is never modified.
      Returns the new session_id so the frontend can navigate to it.

    - ``mode=in_place``: rolls back the CURRENT session to the target
      checkpoint. Checkpoints with sequence > N are deleted (along with
      their orphaned writes/blobs), and chat_messages are truncated to
      match the target checkpoint's message list. Checkpoints with
      sequence <= N are preserved so the user can still restore to
      earlier points. Returns the same session_id.
    """
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

    # Only members+ can restore (viewers cannot).
    user_id = user.get("sub") or user.get("id", "")
    tenant_role = user.get("role", "member")
    if not _can_mutate(cs, user_id, role, tenant_role):
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "You do not have permission to restore from this session",
                }
            },
        )

    # Load the target checkpoint.
    from src.runtime.adapters.langgraph_bridge import LangGraphCheckpointShim
    from src.runtime.harness.checkpoint import SQLiteCheckpointStore

    store = SQLiteCheckpointStore()
    cp = await store.load(session_id, sequence)
    if cp is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Checkpoint not found"}},
        )

    # Reconstruct the full message list at this checkpoint (the stored
    # ``messages`` column may be empty for rows written before the fix).
    shim = LangGraphCheckpointShim(store, session_id, "")
    messages = await shim.reconstruct_checkpoint_messages(cp)

    # ── In-place restore: rollback the current session ──
    if mode == "in_place":
        return await _restore_in_place(
            db=db,
            store=store,
            shim=shim,
            cs=cs,
            session_id=session_id,
            sequence=sequence,
            target_cp=cp,
            messages=messages,
        )

    # Create a new session (branch from parent).
    import uuid as _uuid

    new_session_id = _uuid.uuid4().hex[:32]
    new_title = f"{cs.title} (restored from #{sequence})"
    new_cs = ChatSession(
        id=new_session_id,
        workspace_id=workspace_id,
        owner_id=user_id,
        title=new_title,
        visibility="private",
        agent_name=cs.agent_name,
    )
    db.add(new_cs)

    # Seed the new session with ONLY the target checkpoint's messages
    # (not the full session history). The ``messages`` list comes from
    # ``reconstruct_checkpoint_messages`` which returns the snapshot
    # stored in ``cp.messages`` — the full conversation up to that
    # checkpoint, nothing after.
    for msg in messages:
        db.add(ChatMessage(
            session_id=new_session_id,
            role=msg.get("role", "user"),
            content=msg.get("content", ""),
            tokens=0,
        ))

    # Append a system note about the restore.
    db.add(ChatMessage(
        session_id=new_session_id,
        role="system",
        content=f"已从 checkpoint #{sequence} 恢复（原会话: {session_id}）",
        tokens=0,
    ))

    # Copy ALL prior checkpoint rows (sequence <= target) from the
    # original session to the new session. This preserves the full
    # checkpoint history so the user can see restore points in the
    # forked session — without this, the forked session would only have
    # a single checkpoint and the inline button map (which matches the
    # i-th user message to the i-th checkpoint) would only show a button
    # on the first user bubble.
    #
    # We copy only the ``checkpoints`` table rows (self-contained
    # ``messages`` snapshots). Writes/blobs are NOT copied — the target
    # checkpoint gets fresh writes via ``_reseed_checkpoint_sql`` below,
    # and earlier checkpoints are only needed for UI history (their
    # ``tool_state`` is stale but never used by ``aget_tuple`` because
    # the agent always resumes from the latest checkpoint).
    from sqlalchemy import text as _sa_text
    await db.execute(
        _sa_text(
            "INSERT INTO checkpoints "
            "(session_id, sequence, messages, tool_state, agent_id, metadata, created_at) "
            "SELECT :new_sid, sequence, messages, tool_state, agent_id, metadata, created_at "
            "FROM checkpoints "
            "WHERE session_id = :old_sid AND sequence <= :target_seq"
        ),
        {"new_sid": new_session_id, "old_sid": session_id, "target_seq": sequence},
    )

    # Re-seed the target checkpoint with a fresh LangGraph envelope +
    # per-message writes so the agent can resume from it. This overwrites
    # the copied row at the same sequence (INSERT OR REPLACE) with a
    # clean state that doesn't reference old blob versions.
    if messages:
        source_metadata = cp.metadata if isinstance(cp.metadata, dict) else {}
        new_metadata = {
            "step": source_metadata.get("step", 0),
            "source": "restore",
            "restored_from": session_id,
            "restored_from_sequence": sequence,
        }
        await _reseed_checkpoint_sql(
            db=db,
            shim=shim,
            session_id=new_session_id,
            sequence=sequence,
            messages=messages,
            agent_id=cs.agent_name or "",
            metadata=new_metadata,
        )

    await db.commit()

    return RestoreResponse(
        session_id=new_session_id,
        title=new_title,
        restored_from_session_id=session_id,
        restored_from_sequence=sequence,
    )
