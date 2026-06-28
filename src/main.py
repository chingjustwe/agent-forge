from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.gateway.routes.chat import router as chat_router
from src.gateway.routes.auth import router as auth_router
from src.gateway.routes.workspaces import router as workspaces_router
from src.gateway.routes.admin import router as user_router, admin_router
from src.gateway.routes.audit import router as audit_router
from src.gateway.routes.observability import router as observability_router
from src.gateway.routes.quota import router as quota_router
from src.gateway.routes.settings import router as settings_router
from src.gateway.middleware.auth import AuthMiddleware
from src.gateway.middleware.audit import AuditMiddleware
from src.infra.db.engine import engine
from src.infra.db.models import Base
from src.infra.telemetry.collector import TelemetryCollector


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.telemetry = TelemetryCollector()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Platform", lifespan=lifespan)

    app.include_router(chat_router)
    app.include_router(auth_router)
    app.include_router(workspaces_router)
    app.include_router(user_router)
    app.include_router(admin_router)
    app.include_router(audit_router)
    app.include_router(observability_router)
    app.include_router(quota_router)
    app.include_router(settings_router)

    app.add_middleware(AuthMiddleware)
    app.add_middleware(AuditMiddleware)

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:create_app", host="0.0.0.0", port=8000, reload=True)
