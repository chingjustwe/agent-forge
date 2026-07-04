import os
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_file.name}"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_tables():
    """Create all database tables once per test session."""
    from src.infra.db.models import Base

    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


def pytest_unconfigure(config):
    try:
        os.unlink(_db_file.name)
    except OSError:
        pass


# ─── P0-2 RBAC test helpers ───────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session():
    """Provide a fresh AsyncSession for tests that need to seed DB rows."""
    from src.infra.db.session import get_db
    async for session in get_db():
        yield session


async def setup_workspace_with_member(
    db_session,
    ws_id: str = "ws-1",
    tenant_id: str = "tenant-1",
    user_id: str = "member-1",
    user_role: str = "member",
    tenant_role: str = "member",
    email: str = "member@test.com",
    name: str = "Member",
) -> str:
    """Create tenant+workspace+user+WorkspaceMember rows in the DB.

    Returns a JWT token carrying both ``sub``/``id`` claims so it works
    with both old and new RBAC code paths.
    """
    from src.gateway.auth.jwt import create_jwt
    from src.infra.db.models import Tenant, Workspace, User, WorkspaceMember

    t = await db_session.get(Tenant, tenant_id)
    if not t:
        db_session.add(
            Tenant(id=tenant_id, name="Test Tenant", domain=f"{tenant_id}.test")
        )
        await db_session.flush()

    w = await db_session.get(Workspace, ws_id)
    if not w:
        db_session.add(Workspace(id=ws_id, tenant_id=tenant_id, name=f"WS {ws_id}"))
        await db_session.flush()

    u = await db_session.get(User, user_id)
    if not u:
        db_session.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                email=email,
                name=name,
                role=tenant_role,
            )
        )
        await db_session.flush()

    m = await db_session.get(WorkspaceMember, (ws_id, user_id))
    if not m:
        db_session.add(
            WorkspaceMember(workspace_id=ws_id, user_id=user_id, role=user_role)
        )
    await db_session.commit()

    return create_jwt({
        "id": user_id,
        "sub": user_id,
        "tenant_id": tenant_id,
        "email": email,
        "role": tenant_role,
    })
