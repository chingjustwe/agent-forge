"""P2-1: Workspace invitation links.

Flow:
- ``workspace_admin``/``workspace_owner`` (or ``tenant_admin``) creates an
  invitation with a random token. Default expiry 7 days. ``email=None``
  means a generic link any logged-in user can accept.
- The invitee opens ``/invitations/{token}`` (public preview) and clicks
  Accept, hitting ``POST /api/v1/invitations/{token}/accept``.
- On accept: validate token + expiry + email match → upsert
  ``WorkspaceMember`` → mark ``accepted_at``/``accepted_by`` → invalidate
  ``/me/workspaces`` cache → write ``AuditLog``.
- Re-inviting the same email+workspace physically deletes the previous
  unaccepted invitation (simplest spec-recommended approach).
"""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.rbac import require_permission
from src.gateway.routes.me import invalidate_workspace_cache
from src.infra.db.models import (
    AuditLog,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMember,
)
from src.infra.db.session import get_db

router = APIRouter()

DEFAULT_EXPIRES_DAYS = 7
ALLOWED_INVITE_ROLES = ("member", "workspace_admin")


class CreateInvitationRequest(BaseModel):
    email: str | None = None
    role: str = "member"
    expires_in_days: int = DEFAULT_EXPIRES_DAYS


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_invitation(inv: WorkspaceInvitation, workspace_name: str | None = None) -> dict:
    return {
        "id": inv.id,
        "workspace_id": inv.workspace_id,
        "workspace_name": workspace_name,
        "email": inv.email,
        "role": inv.role,
        "token": inv.token,
        "invited_by": inv.invited_by,
        "expires_at": inv.expires_at.isoformat() if inv.expires_at else None,
        "accepted_at": inv.accepted_at.isoformat() if inv.accepted_at else None,
        "accepted_by": inv.accepted_by,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "is_expired": inv.expires_at.replace(tzinfo=timezone.utc) < _now_utc()
        if inv.expires_at
        else False,
        "is_accepted": inv.accepted_at is not None,
    }


async def _write_audit(
    db: AsyncSession,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    action: str,
    target_id: str,
    details: dict | None = None,
    ip_address: str = "",
) -> None:
    """Append an AuditLog row for an invitation-related action."""
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            target_type="invitation",
            target_id=target_id,
            details=details or {},
            ip_address=ip_address or "",
        )
    )


# ---------------------------------------------------------------------------
# Workspace-scoped endpoints (require workspace_admin/owner)
# ---------------------------------------------------------------------------
@router.post("/api/v1/workspaces/{workspace_id}/invitations")
async def create_invitation(
    workspace_id: str,
    body: CreateInvitationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(
        require_permission("invitations:write", workspace_id_param="workspace_id")
    ),
):
    """Create a new invitation link for this workspace."""
    if body.role not in ALLOWED_INVITE_ROLES:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "BAD_REQUEST",
                    "message": f"role must be one of {ALLOWED_INVITE_ROLES}",
                }
            },
        )
    if body.expires_in_days < 1 or body.expires_in_days > 365:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "BAD_REQUEST",
                    "message": "expires_in_days must be between 1 and 365",
                }
            },
        )

    user = request.state.user
    inviter_id = user.get("sub") or user.get("id", "")
    tenant_id = user.get("tenant_id", "")

    # Re-inviting the same email+workspace: physically delete previous
    # unaccepted invitations so the old link stops working. Generic links
    # (email=None) are not affected — admins may have multiple outstanding.
    if body.email is not None:
        await db.execute(
            delete(WorkspaceInvitation).where(
                WorkspaceInvitation.workspace_id == workspace_id,
                WorkspaceInvitation.email == body.email,
                WorkspaceInvitation.accepted_at.is_(None),
            )
        )

    token = secrets.token_urlsafe(32)
    expires_at = _now_utc() + timedelta(days=body.expires_in_days)
    invitation = WorkspaceInvitation(
        workspace_id=workspace_id,
        email=body.email,
        role=body.role,
        token=token,
        invited_by=inviter_id,
        expires_at=expires_at,
    )
    db.add(invitation)
    await db.flush()  # populate invitation.id before referencing it in AuditLog
    await _write_audit(
        db,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=inviter_id,
        action="invitation.create",
        target_id=invitation.id,
        details={"email": body.email, "role": body.role},
        ip_address=request.client.host if request.client else "",
    )
    await db.commit()
    await db.refresh(invitation)

    return JSONResponse(
        status_code=201,
        content=_serialize_invitation(invitation),
    )


@router.get("/api/v1/workspaces/{workspace_id}/invitations")
async def list_invitations(
    workspace_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(
        require_permission("invitations:write", workspace_id_param="workspace_id")
    ),
):
    """List all invitations for this workspace (newest first)."""
    result = await db.execute(
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.workspace_id == workspace_id)
        .order_by(WorkspaceInvitation.created_at.desc())
    )
    items = result.scalars().all()
    return [_serialize_invitation(i) for i in items]


@router.delete("/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}")
async def revoke_invitation(
    workspace_id: str,
    invitation_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(
        require_permission("invitations:write", workspace_id_param="workspace_id")
    ),
):
    """Revoke (physically delete) an invitation."""
    invitation = await db.get(WorkspaceInvitation, invitation_id)
    if not invitation or invitation.workspace_id != workspace_id:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Invitation not found"}},
        )

    user = request.state.user
    await _write_audit(
        db,
        tenant_id=user.get("tenant_id", ""),
        workspace_id=workspace_id,
        user_id=user.get("sub") or user.get("id", ""),
        action="invitation.revoke",
        target_id=invitation.id,
        details={"email": invitation.email, "role": invitation.role},
        ip_address=request.client.host if request.client else "",
    )
    await db.delete(invitation)
    await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Public token endpoints
# ---------------------------------------------------------------------------
@router.get("/api/v1/invitations/{token}")
async def get_invitation_by_token(token: str, db: AsyncSession = Depends(get_db)):
    """Public preview of an invitation (no auth required).

    Used by logged-out users to see what they're being invited to before
    deciding to log in / register.
    """
    result = await db.execute(
        select(WorkspaceInvitation).where(WorkspaceInvitation.token == token)
    )
    invitation = result.scalar_one_or_none()
    if not invitation:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Invitation not found"}},
        )

    workspace = await db.get(Workspace, invitation.workspace_id)
    workspace_name = workspace.name if workspace else None

    body = _serialize_invitation(invitation, workspace_name=workspace_name)
    # Don't leak the token back in the public preview response (the caller
    # already has it). Strip invited_by id too — expose email instead via a
    # follow-up if needed. For now keep the payload minimal but informative.
    body.pop("token", None)
    body.pop("invited_by", None)
    return body


@router.post("/api/v1/invitations/{token}/accept")
async def accept_invitation(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Accept an invitation. Requires an authenticated user.

    - Invitation must exist (404 otherwise).
    - ``accepted_at`` must be None (410 Gone if already accepted).
    - ``expires_at`` must be in the future (410 Gone if expired).
    - If ``email`` is set, the authenticated user's email must match (403).
    - If the user is already a member, return 200 with the existing role
      (idempotent) and mark the invitation accepted.
    - Otherwise create a ``WorkspaceMember`` row, mark the invitation
      accepted, invalidate the /me/workspaces cache, and audit-log.
    """
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "UNAUTHORIZED", "message": "Not authenticated"}},
        )

    result = await db.execute(
        select(WorkspaceInvitation).where(WorkspaceInvitation.token == token)
    )
    invitation = result.scalar_one_or_none()
    if not invitation:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "NOT_FOUND", "message": "Invitation not found"}},
        )

    if invitation.accepted_at is not None:
        return JSONResponse(
            status_code=410,
            content={
                "error": {
                    "code": "GONE",
                    "message": "This invitation has already been accepted",
                }
            },
        )

    # SQLite drops tzinfo on stored datetimes; normalize both sides to naive
    # UTC for the comparison.
    now_naive = _now_utc().replace(tzinfo=None)
    expires_naive = invitation.expires_at.replace(tzinfo=None)
    if expires_naive < now_naive:
        return JSONResponse(
            status_code=410,
            content={
                "error": {"code": "EXPIRED", "message": "This invitation has expired"}
            },
        )

    user_id = user.get("sub") or user.get("id", "")
    user_email = user.get("email", "")

    if invitation.email is not None and invitation.email != user_email:
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "This invitation is for a different email address",
                }
            },
        )

    # Idempotent: if already a member, return existing role.
    existing_member = await db.get(WorkspaceMember, (invitation.workspace_id, user_id))
    if existing_member:
        role = existing_member.role
    else:
        role = invitation.role
        db.add(
            WorkspaceMember(
                workspace_id=invitation.workspace_id,
                user_id=user_id,
                role=invitation.role,
            )
        )

    invitation.accepted_at = _now_utc()
    invitation.accepted_by = user_id
    await _write_audit(
        db,
        tenant_id=user.get("tenant_id", ""),
        workspace_id=invitation.workspace_id,
        user_id=user_id,
        action="invitation.accept",
        target_id=invitation.id,
        details={"email": invitation.email, "role": invitation.role},
        ip_address=request.client.host if request.client else "",
    )
    await db.commit()
    invalidate_workspace_cache(user_id)

    return JSONResponse(
        status_code=200,
        content={
            "workspace_id": invitation.workspace_id,
            "role": role,
            "already_member": existing_member is not None,
        },
    )
