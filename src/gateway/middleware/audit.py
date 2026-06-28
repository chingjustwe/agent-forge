from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from src.infra.db.engine import async_session
from src.infra.db.models import AuditLog

MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SKIP_PATHS = frozenset({
    "/api/v1/health",
    "/api/v1/auth/login",
    "/api/v1/auth/callback",
    "/api/v1/auth/register",
})


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in MUTATION_METHODS and request.url.path not in SKIP_PATHS:
            response = await call_next(request)
            if response.status_code < 400:
                user = getattr(request.state, "user", None)
                tenant_id = getattr(request.state, "tenant_id", "")
                if user:
                    await self._write_log(
                        tenant_id=tenant_id,
                        user_id=user.get("sub", ""),
                        action=f"{request.method.lower()}.{request.url.path}",
                        target_type="api",
                        target_id=request.url.path,
                        ip_address=request.client.host if request.client else "",
                    )
            return response
        return await call_next(request)

    async def _write_log(
        self,
        tenant_id: str,
        user_id: str,
        action: str,
        target_type: str,
        target_id: str,
        ip_address: str,
    ):
        async with async_session() as session:
            session.add(
                AuditLog(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    ip_address=ip_address,
                )
            )
            await session.commit()
