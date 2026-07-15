import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.models import SsoProvider, UserIdentity, Workspace, WorkspaceMember
from src.infra.db.session import get_db
from src.gateway.auth.permissions import get_role_permissions, get_frontend_tabs, get_api_key_scopes


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
      each reported as ``workspace_admin``.
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
                "slug": w.slug,
                "icon": w.icon,
                "role": "workspace_admin",
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
                "slug": ws.slug,
                "icon": ws.icon,
                "role": role,
                "created_at": ws.created_at.isoformat() if ws.created_at else None,
            }
            for ws, role in rows
        ]

    _workspace_cache[user_id] = (now, body)
    return body


@router.get("/api/v1/permissions")
async def get_my_permissions(request: Request, db: AsyncSession = Depends(get_db)):
    """Return current user's permissions and frontend tab visibility."""
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )

    user_role = user.get("role", "")
    permissions = get_role_permissions(user_role)

    # Filter frontend tabs based on user's permissions
    all_tabs = get_frontend_tabs()
    visible_tabs = {}
    if "*" in permissions:
        # tenant_admin sees all tabs
        visible_tabs = dict(all_tabs)
    else:
        for path, required_perm in all_tabs.items():
            if required_perm is None or required_perm in permissions:
                visible_tabs[path] = required_perm

    return {
        "role": user_role,
        "permissions": permissions,
        "frontend_tabs": visible_tabs,
        "api_key_scopes": get_api_key_scopes(),
    }


@router.get("/api/v1/me/identities")
async def list_my_identities(request: Request, db: AsyncSession = Depends(get_db)):
    """List the current user's linked SSO identities."""
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )
    user_id = user.get("sub") or user.get("id", "")

    result = await db.execute(
        select(UserIdentity, SsoProvider)
        .join(SsoProvider, SsoProvider.id == UserIdentity.provider_id)
        .where(UserIdentity.user_id == user_id)
    )
    rows = result.all()

    return {
        "identities": [
            {
                "id": identity.id,
                "provider_id": identity.provider_id,
                "provider_name": provider.name,
                "provider_type": provider.provider_type,
                "email_at_provider": identity.email_at_provider,
                "created_at": identity.created_at.isoformat() if identity.created_at else None,
            }
            for identity, provider in rows
        ]
    }


@router.delete("/api/v1/me/identities/{identity_id}")
async def delete_my_identity(
    identity_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Unlink an SSO identity from the current user.

    Safety checks:
    - The identity must belong to the current user.
    - If the user is SSO-only (no password), they must keep at least one
      identity to avoid lockout.
    """
    from src.infra.db.models import User

    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )
    user_id = user.get("sub") or user.get("id", "")

    result = await db.execute(
        select(UserIdentity).where(
            UserIdentity.id == identity_id,
            UserIdentity.user_id == user_id,
        )
    )
    identity = result.scalar_one_or_none()
    if not identity:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Identity not found"}},
        )

    # Prevent lockout: SSO-only users must keep at least one identity.
    user_row = await db.get(User, user_id)
    if user_row and not user_row.hashed_password:
        count_result = await db.execute(
            select(UserIdentity).where(UserIdentity.user_id == user_id)
        )
        if len(count_result.scalars().all()) <= 1:
            return JSONResponse(
                status_code=400,
                content={"error": {"code": "BAD_REQUEST", "message": "Cannot unlink your last SSO identity — set a password first to avoid lockout"}},
            )

    await db.delete(identity)
    await db.commit()
    return {"status": "ok"}
