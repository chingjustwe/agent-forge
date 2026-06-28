import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Request
from joserfc import jwt
from joserfc.jwk import OctKey
from joserfc.errors import JoseError

_SECRET = None
_KEY = None


def _get_key() -> OctKey:
    global _SECRET, _KEY
    if _KEY is None:
        _SECRET = uuid.uuid4().hex
        _KEY = OctKey.import_key(_SECRET)
    return _KEY


def hash_secret() -> str:
    return _SECRET or ""


def create_jwt(user: dict, expires_delta: timedelta | None = None) -> str:
    if expires_delta is None:
        expires_delta = timedelta(hours=24)
    now = datetime.now(timezone.utc)
    claims = {
        "sub": user["id"],
        "tenant_id": user["tenant_id"],
        "email": user["email"],
        "role": user["role"],
        "workspace_ids": user.get("workspace_ids", []),
        "exp": int((now + expires_delta).timestamp()),
        "iat": int(now.timestamp()),
    }
    return jwt.encode({"alg": "HS256"}, claims, _get_key())


def decode_jwt(token: str) -> dict | None:
    try:
        decoded = jwt.decode(token, _get_key())
        return decoded.claims
    except JoseError:
        return None


PUBLIC_ROUTES = frozenset({
    "GET:/api/v1/health",
    "GET:/api/v1/auth/login",
    "GET:/api/v1/auth/callback",
    "POST:/api/v1/auth/login",
    "POST:/api/v1/auth/register",
    "POST:/api/v1/auth/logout",
})


def extract_jwt(request: Request) -> dict | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.removeprefix("Bearer ")
    return decode_jwt(token)
