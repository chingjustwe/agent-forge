import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.models import Workspace, WorkspaceMember
from src.infra.db.session import get_db


def _order_by_activity():
    """P3-1: shared ORDER BY clause — is_default DESC, last_active_at DESC
    NULLS LAST, name ASC.

    SQLite doesn't support ``NULLS LAST`` directly, so we emulate it with
    ``last_active_at IS NULL`` (False sorts before True, i.e. non-null first).
    """
    from sqlalchemy import asc, desc
    return [
        desc(Workspace.is_default),
        asc(WorkspaceMember.last_active_at.is_(None)),
        desc(WorkspaceMember.last_active_at),
        asc(Workspace.name),
    ]

router = APIRouter()

# P0-4: in-process cache for /me/workspaces. asyncio is single-threaded so a
# plain dict is safe — no lock needed. Invalidated by add_member / remove_member.
_workspace_cache: dict[str, tuple[float, list]] = {}
_CACHE_TTL_SECONDS = 60.0


def invalidate_workspace_cache(user_id: str) -> None:
    """Drop the cached workspace list for a user.

    Called by workspaces.add_member / workspaces.remove_member after a
    successful membership change so the next /me/workspaces request sees
    fresh data instead of waiting for TTL expiry.
    """
    _workspace_cache.pop(user_id, None)


@router.get("/api/v1/me/workspaces")
async def list_my_workspaces(request: Request, db: AsyncSession = Depends(get_db)):
    """List workspaces the current user is a member of, with their role in each.

    - ``tenant_admin`` sees every non-archived workspace in their tenant,
      each reported as ``workspace_owner``.
    - Other users see only the workspaces where they have a
      ``WorkspaceMember`` row, with their per-workspace role.
    - Archived workspaces are always excluded.
    """
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )
    user_id = user.get("sub") or user.get("id", "")

    # P0-4: serve from cache when fresh enough.
    now = time.monotonic()
    cached = _workspace_cache.get(user_id)
    if cached is not None and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    # tenant_admin sees every workspace in their tenant (treated as owner).
    if user.get("role") == "tenant_admin":
        tenant_id = user.get("tenant_id", "")
        from sqlalchemy import asc, desc
        # P3-1: tenant_admin has no WorkspaceMember row per workspace, so we
        # order only by is_default DESC then name ASC (no last_active_at).
        result = await db.execute(
            select(Workspace)
            .where(
                Workspace.tenant_id == tenant_id,
                Workspace.archived == 0,
            )
            .order_by(
                desc(Workspace.is_default),
                asc(Workspace.name),
            )
        )
        workspaces = result.scalars().all()
        body = [
            {
                "id": w.id,
                "name": w.name,
                "role": "workspace_owner",
                "created_at": w.created_at.isoformat() if w.created_at else None,
            }
            for w in workspaces
        ]
    else:
        # Plain member: join WorkspaceMember to Workspace.
        # P3-1: order by is_default DESC → last_active_at DESC NULLS LAST → name ASC.
        result = await db.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(
                WorkspaceMember.user_id == user_id,
                Workspace.archived == 0,
            )
            .order_by(*_order_by_activity())
        )
        rows = result.all()
        body = [
            {
                "id": ws.id,
                "name": ws.name,
                "role": role,
                "created_at": ws.created_at.isoformat() if ws.created_at else None,
            }
            for ws, role in rows
        ]

    _workspace_cache[user_id] = (now, body)
    return body
