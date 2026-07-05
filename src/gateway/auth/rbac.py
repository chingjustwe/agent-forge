"""FastAPI dependencies for tenant-level and workspace-level RBAC.

P0-2 splits permission checks between:
- ``TenantRole`` (``member`` / ``tenant_admin``) — kept on ``User.role``.
- ``WorkspaceRole`` (``viewer`` / ``member`` / ``workspace_admin``) —
  stored per-workspace on ``WorkspaceMember.role``.

``tenant_admin`` short-circuits every workspace-level check (treated as
``workspace_admin`` for any workspace).

Usage:
    # Old API (deprecated, kept for backward compat):
    _ctx = Depends(require_workspace_role("workspace_id", "workspace_admin"))

    # New API (preferred, reads from permissions.yaml):
    _ctx = Depends(require_permission("agents:write", workspace_id_param="workspace_id"))
"""
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.permissions import has_permission
from src.infra.db.models import WorkspaceMember
from src.infra.db.session import get_db


def _get_user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_tenant_role(min_role: str):
    """FastAPI dependency for tenant-level role check (tenant_admin/member).

    ``tenant_admin`` short-circuits all tenant-level requirements.

    Deprecated: prefer ``require_permission()``.
    """
    async def _dep(request: Request):
        user = _get_user(request)
        user_role = user.get("role")
        if user_role == "tenant_admin":
            return user
        if user_role != min_role:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user
    return _dep


async def get_workspace_member_role(
    workspace_id: str, user: dict, db: AsyncSession
) -> str | None:
    """Query ``WorkspaceMember.role`` for the given (workspace_id, user_id).

    Returns ``None`` if the user is not a member of this workspace.
    ``tenant_admin`` short-circuits to ``workspace_admin``.
    """
    if user.get("role") == "tenant_admin":
        return "workspace_admin"
    user_id = user.get("sub") or user.get("id", "")
    result = await db.execute(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    row = result.first()
    return row[0] if row else None


def require_workspace_role(workspace_id_param: str, *allowed_roles: str):
    """FastAPI dependency factory for workspace-level role checks.

    ``workspace_id_param`` is the name of the path parameter carrying the
    workspace id. ``allowed_roles`` are the acceptable ``WorkspaceRole``
    values (e.g. ``"member"``, ``"workspace_admin"``). ``tenant_admin``
    always short-circuits to success.

    Deprecated: prefer ``require_permission()``.
    """
    async def _dep(request: Request, db: AsyncSession = Depends(get_db)):
        user = _get_user(request)
        workspace_id = request.path_params.get(workspace_id_param)
        if not workspace_id:
            raise HTTPException(status_code=400, detail="workspace_id required")
        role = await get_workspace_member_role(workspace_id, user, db)
        if role is None:
            raise HTTPException(
                status_code=403, detail="Not a member of this workspace"
            )
        if user.get("role") == "tenant_admin":
            return {"user": user, "workspace_id": workspace_id, "workspace_role": role}
        if role not in allowed_roles:
            raise HTTPException(
                status_code=403, detail=f"Requires role: {allowed_roles}"
            )
        return {"user": user, "workspace_id": workspace_id, "workspace_role": role}
    return _dep


def require_permission(permission: str, workspace_id_param: str | None = None):
    """FastAPI dependency factory for permission-based access control.

    Reads role→permission mapping from ``permissions.yaml``. This is the
    preferred way to guard routes going forward.

    Args:
        permission: e.g. ``"agents:write"``
        workspace_id_param: if set, also validates the user is a member of
            the workspace identified by this path parameter. The dependency
            return value includes ``workspace_id`` and ``workspace_role``.

    Returns:
        If ``workspace_id_param`` is set: ``{"user": ..., "workspace_id": ...,
        "workspace_role": ...}``. Otherwise: the user dict.
    """
    async def _dep(request: Request, db: AsyncSession = Depends(get_db)):
        user = _get_user(request)
        user_role = user.get("role", "")

        if not has_permission(user_role, permission):
            raise HTTPException(
                status_code=403,
                detail=f"Missing permission: {permission}",
            )

        if workspace_id_param:
            workspace_id = request.path_params.get(workspace_id_param)
            if not workspace_id:
                raise HTTPException(status_code=400, detail="workspace_id required")

            ws_role = await get_workspace_member_role(workspace_id, user, db)
            if ws_role is None:
                raise HTTPException(
                    status_code=403, detail="Not a member of this workspace"
                )

            return {
                "user": user,
                "workspace_id": workspace_id,
                "workspace_role": ws_role,
            }

        return user
    return _dep


async def get_admin_workspace_ids(user: dict, db: AsyncSession) -> list[str] | None:
    """Return workspace IDs the user can administer, or None for tenant_admin.

    - tenant_admin → None (no scoping needed, sees all workspaces)
    - workspace_admin → list of workspace IDs where they have workspace_admin role
    - others → empty list (no admin access)
    """
    if user.get("role") == "tenant_admin":
        return None
    user_id = user.get("sub") or user.get("id", "")
    result = await db.execute(
        select(WorkspaceMember.workspace_id).where(
            WorkspaceMember.user_id == user_id,
            WorkspaceMember.role == "workspace_admin",
        )
    )
    return [r[0] for r in result.all()]
