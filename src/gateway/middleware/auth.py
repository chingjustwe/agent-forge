from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.gateway.auth.jwt import extract_jwt, PUBLIC_ROUTES


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        route_key = f"{request.method}:{request.url.path}"
        if route_key in PUBLIC_ROUTES:
            return await call_next(request)

        user = extract_jwt(request)
        if user is None:
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "UNAUTHORIZED", "message": "Missing or invalid token"}},
            )

        request.state.user = user
        request.state.tenant_id = user.get("tenant_id", "")
        response = await call_next(request)
        return response
