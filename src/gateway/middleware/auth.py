import hashlib
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware

from src.gateway.auth.jwt import extract_jwt, PUBLIC_ROUTES
from src.infra.db.engine import async_session
from src.infra.db.models import ApiKey, User, Workspace

# P2-1: path prefixes that are publicly accessible (no auth required).
# ``GET:/api/v1/invitations/`` exposes the invitation preview to logged-out
# users so they can decide whether to sign in. The corresponding POST
# ``/accept`` route is NOT matched here and still requires a Bearer token.
PUBLIC_PATH_PREFIXES = (
    "GET:/api/v1/invitations/",
)


def _is_public(method: str, path: str) -> bool:
    route_key = f"{method}:{path}"
    if route_key in PUBLIC_ROUTES:
        return True
    for prefix in PUBLIC_PATH_PREFIXES:
        if route_key.startswith(prefix):
            return True
    return False


def _unauthorized(message: str = "Missing or invalid token") -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"error": {"code": "UNAUTHORIZED", "message": message}},
    )


async def _authenticate_api_key(raw_key: str) -> dict | None:
    """Resolve an ``X-API-Key`` header to a request.state.user dict.

    Returns ``None`` when the key is unknown, revoked, expired, or bound to
    an archived workspace / archived creator. On success the ``last_used_at``
    timestamp is refreshed (failure here does not block the request).
    """
    if not raw_key.startswith("ap_"):
        return None

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    async with async_session() as db:
        result = await db.execute(
            select(ApiKey).where(
                ApiKey.key_hash == key_hash,
                ApiKey.revoked == 0,
            )
        )
        api_key = result.scalar_one_or_none()
        if api_key is None:
            return None

        # Expiry check (SQLite drops tzinfo on stored datetimes).
        if api_key.expires_at is not None:
            expires = api_key.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                return None

        # Refuse if the bound workspace is archived.
        workspace = await db.get(Workspace, api_key.workspace_id)
        if workspace is None or workspace.archived:
            return None

        # Resolve the creator to inherit tenant_id / role.
        creator = await db.get(User, api_key.created_by)
        if creator is None or creator.archived:
            return None

        # Refresh last_used_at; a persistence failure must not break the
        # request (per spec: "失败不影响请求").
        try:
            api_key.last_used_at = datetime.now(timezone.utc)
            await db.commit()
        except Exception:  # pragma: no cover — defensive
            pass

        return {
            "sub": api_key.created_by,
            "tenant_id": creator.tenant_id,
            "email": creator.email,
            "role": creator.role,
            "auth_method": "api_key",
            "api_key_id": api_key.id,
            "api_key_scopes": api_key.scopes or [],
            "workspace_id": api_key.workspace_id,
        }


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _is_public(request.method, request.url.path):
            return await call_next(request)

        # 1) Prefer JWT bearer auth (existing path).
        user = extract_jwt(request)
        if user is not None:
            request.state.user = user
            request.state.tenant_id = user.get("tenant_id", "")
            return await call_next(request)

        # 2) Fall back to X-API-Key header (P2-3).
        api_key_header = request.headers.get("X-API-Key", "")
        if api_key_header:
            user = await _authenticate_api_key(api_key_header)
            if user is None:
                return _unauthorized("Invalid, revoked, or expired API key")
            request.state.user = user
            request.state.tenant_id = user.get("tenant_id", "")
            return await call_next(request)

        return _unauthorized()
