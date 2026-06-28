import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.models import Tenant, Workspace, User, AuditLog, Base


@pytest.mark.asyncio
async def test_models_create():
    assert Tenant.__tablename__ == "tenants"
    assert Workspace.__tablename__ == "workspaces"
    assert User.__tablename__ == "users"
    assert AuditLog.__tablename__ == "audit_logs"


@pytest.mark.asyncio
async def test_engine_creates_tables():
    from src.infra.db.engine import engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        )
        tables = {row[0] for row in result}
    assert "tenants" in tables
    assert "workspaces" in tables
    assert "users" in tables
    assert "audit_logs" in tables


@pytest.mark.asyncio
async def test_session_dependency():
    from src.infra.db.session import get_db
    count = 0
    async for session in get_db():
        assert isinstance(session, AsyncSession)
        count += 1
    assert count == 1
