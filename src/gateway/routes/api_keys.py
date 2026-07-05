"""P2-3: Workspace-scoped API keys.

- ``workspace_admin``/``workspace_owner`` (or ``tenant_admin``) creates a
  key. The plaintext key (``ap_<32 hex>``) is returned exactly once in
  the create response; the DB stores only ``key_hash`` (SHA-256 hex).
- List returns ``key_prefix`` only (never the plaintext key).
- Delete = soft revoke (``revoked=1``); the row is retained for audit.

Cross-workspace isolation: every query filters on ``workspace_id`` AND
``id``, so a key from another workspace is never visible (returns 404).
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.auth.rbac import require_permission
from src.gateway.auth.permissions import get_api_key_scopes
from src.infra.db.models import ApiKey, AuditLog
from src.infra.db.session import get_db

router = APIRouter()

_DEFAULT_SCOPES = ["chat:write"]


class CreateApiKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    scopes: list[str] | None = None
    expires_in_days: int | None = None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _generate_key() -> tuple[str, str, str]:
    """Return (plaintext_key, key_prefix, key_hash).

    - plaintext: ``ap_`` + 32 hex chars (35 chars total)
    - key_prefix: first 8 chars of the plaintext (includes the ``ap_`` marker)
    - key_hash: SHA-256 hex digest of the plaintext
    """
    raw = "ap_" + secrets.token_hex(16)
    prefix = raw[:8]
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return raw, prefix, digest


def _serialize_key_list(k: ApiKey) -> dict:
    """List/detail payload — never includes the plaintext key."""
    return {
        "id": k.id,
        "name": k.name,
        "key_prefix": k.key_prefix,
        "scopes": k.scopes or [],
        "expires_at": k.expires_at.isoformat() if k.expires_at else None,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "revoked": bool(k.revoked),
        "created_at": k.created_at.isoformat() if k.created_at else None,
    }


def _bad_request(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "BAD_REQUEST", "message": message}},
    )


def _not_found(message: str = "API key not found") -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": {"code": "NOT_FOUND", "message": message}},
    )


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
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            target_type="api_key",
            target_id=target_id,
            details=details or {},
            ip_address=ip_address or "",
        )
    )


@router.post("/api/v1/workspaces/{workspace_id}/api-keys")
async def create_api_key(
    workspace_id: str,
    body: CreateApiKeyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(require_permission("api_keys:write", workspace_id_param="workspace_id")),
):
    """Create a new API key for this workspace.

    The plaintext key is returned **only** in this response — store it
    securely, it cannot be retrieved again.
    """
    # Validate scopes
    allowed_scopes = get_api_key_scopes()
    scopes = body.scopes if body.scopes is not None else list(_DEFAULT_SCOPES)
    invalid = [s for s in scopes if s not in allowed_scopes]
    if invalid:
        return _bad_request(f"invalid scopes: {invalid}. allowed: {allowed_scopes}")

    # Validate expiry
    expires_at: datetime | None = None
    if body.expires_in_days is not None:
        if body.expires_in_days < 1 or body.expires_in_days > 365:
            return _bad_request("expires_in_days must be between 1 and 365")
        expires_at = _now_utc() + timedelta(days=body.expires_in_days)

    user = request.state.user
    user_id = user.get("sub") or user.get("id", "")
    tenant_id = user.get("tenant_id", "")

    plaintext, prefix, digest = _generate_key()
    api_key = ApiKey(
        workspace_id=workspace_id,
        name=body.name,
        key_prefix=prefix,
        key_hash=digest,
        scopes=scopes,
        created_by=user_id,
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.flush()  # populate api_key.id before referencing it in AuditLog
    await _write_audit(
        db,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        action="api_key.create",
        target_id=api_key.id,
        details={"name": body.name, "scopes": scopes},
        ip_address=request.client.host if request.client else "",
    )
    await db.commit()
    await db.refresh(api_key)

    # Create response is the ONLY place the plaintext key is returned.
    payload = _serialize_key_list(api_key)
    payload["key"] = plaintext
    return JSONResponse(status_code=201, content=payload)


@router.get("/api/v1/workspaces/{workspace_id}/api-keys")
async def list_api_keys(
    workspace_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(require_permission("api_keys:read", workspace_id_param="workspace_id")),
):
    """List all API keys in this workspace (newest first).

    Never returns the plaintext key — only ``key_prefix`` for display.
    """
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.workspace_id == workspace_id)
        .order_by(ApiKey.created_at.desc())
    )
    items = result.scalars().all()
    return [_serialize_key_list(k) for k in items]


@router.delete("/api/v1/workspaces/{workspace_id}/api-keys/{key_id}")
async def revoke_api_key(
    workspace_id: str,
    key_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _ctx=Depends(require_permission("api_keys:write", workspace_id_param="workspace_id")),
):
    """Revoke (soft-delete) an API key. Cross-workspace lookups return 404."""
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.workspace_id == workspace_id,
        )
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        return _not_found()

    api_key.revoked = 1
    user = request.state.user
    await _write_audit(
        db,
        tenant_id=user.get("tenant_id", ""),
        workspace_id=workspace_id,
        user_id=user.get("sub") or user.get("id", ""),
        action="api_key.revoke",
        target_id=api_key.id,
        details={"name": api_key.name},
        ip_address=request.client.host if request.client else "",
    )
    await db.commit()
    return Response(status_code=204)
