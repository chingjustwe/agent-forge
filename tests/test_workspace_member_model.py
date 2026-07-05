"""Tests for WorkspaceMember model and workspace_ids -> workspace_members migration.

RED phase: these tests are expected to FAIL because the ``WorkspaceMember``
model and the backfill logic inside ``_migrate_schema`` have not been
implemented yet. The import of ``WorkspaceMember`` raises ``ImportError``,
which is the expected RED state — do not "fix" by implementing the model.
"""
import os
import tempfile

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.infra.db.engine import async_session
from src.infra.db.models import Base, Tenant, Workspace, User, WorkspaceMember
from src.main import _migrate_schema


# ---------------------------------------------------------------------------
# Helpers for migration tests (sync SQLite, isolated per test)
# ---------------------------------------------------------------------------
def _make_sync_engine():
    """Create a fresh sync SQLite engine backed by a tempfile."""
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.close()
    path = db.name
    return create_engine(f"sqlite:///{path}"), path


def _dispose(engine, path):
    engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


def _create_legacy_tables(engine, include_users=True):
    """Create tenants/workspaces/(users) — but NOT workspace_members.

    Explicit table list keeps this test meaningful even after the
    WorkspaceMember model is registered with Base.metadata.
    """
    tables = [Tenant.__table__, Workspace.__table__]
    if include_users:
        tables.append(User.__table__)
    with engine.begin() as conn:
        Base.metadata.create_all(conn, tables=tables)


def _insert_user(engine, user_id, role, workspace_ids, tenant_id="t-1"):
    """Insert a tenant, the referenced workspaces, and a user row carrying
    the legacy ``workspace_ids`` JSON array."""
    with Session(engine) as session:
        session.add(Tenant(id=tenant_id, name="Tenant", domain=f"{tenant_id}.test"))
        for ws_id in workspace_ids:
            session.add(Workspace(id=ws_id, tenant_id=tenant_id, name=f"WS {ws_id}"))
        session.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                email=f"{user_id}@test.com",
                name=user_id,
                role=role,
                workspace_ids=workspace_ids,
            )
        )
        session.commit()


def _fetch_workspace_members(engine):
    """Return (workspace_id, user_id, role) rows from workspace_members."""
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT workspace_id, user_id, role FROM workspace_members "
            "ORDER BY workspace_id, user_id"
        ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


# ---------------------------------------------------------------------------
# 1. Model definition tests
# ---------------------------------------------------------------------------
class TestWorkspaceMemberModel:
    def test_tablename(self):
        assert WorkspaceMember.__tablename__ == "workspace_members"

    def test_fields_exist(self):
        cols = {c.name for c in WorkspaceMember.__table__.columns}
        assert {
            "workspace_id",
            "user_id",
            "role",
            "joined_at",
            "added_by",
            "last_active_at",
        } <= cols

    def test_composite_primary_key(self):
        pk = {c.name for c in WorkspaceMember.__table__.primary_key.columns}
        assert pk == {"workspace_id", "user_id"}

    def test_workspace_id_foreign_key(self):
        fks = WorkspaceMember.__table__.columns["workspace_id"].foreign_keys
        assert any(fk.target_fullname == "workspaces.id" for fk in fks)

    def test_user_id_foreign_key(self):
        fks = WorkspaceMember.__table__.columns["user_id"].foreign_keys
        assert any(fk.target_fullname == "users.id" for fk in fks)

    def test_added_by_nullable(self):
        assert WorkspaceMember.__table__.columns["added_by"].nullable is True

    def test_last_active_at_nullable(self):
        assert WorkspaceMember.__table__.columns["last_active_at"].nullable is True

    def test_default_role_is_member(self):
        col = WorkspaceMember.__table__.columns["role"]
        assert col.default is not None
        arg = col.default.arg
        if callable(arg):
            arg = arg(None)
        assert arg == "member"


# ---------------------------------------------------------------------------
# 2. CRUD tests (async)
# ---------------------------------------------------------------------------
class TestWorkspaceMemberCRUD:
    @pytest.mark.asyncio
    async def test_create_and_read(self):
        async with async_session() as session:
            member = WorkspaceMember(
                workspace_id="ws-crud-1", user_id="u-crud-1", role="member"
            )
            session.add(member)
            await session.commit()
            await session.refresh(member)

            fetched = await session.get(WorkspaceMember, ("ws-crud-1", "u-crud-1"))
            assert fetched is not None
            assert fetched.workspace_id == "ws-crud-1"
            assert fetched.user_id == "u-crud-1"
            assert fetched.role == "member"

    @pytest.mark.asyncio
    async def test_delete(self):
        async with async_session() as session:
            member = WorkspaceMember(
                workspace_id="ws-crud-2", user_id="u-crud-2", role="admin"
            )
            session.add(member)
            await session.commit()

            await session.delete(member)
            await session.commit()

            fetched = await session.get(WorkspaceMember, ("ws-crud-2", "u-crud-2"))
            assert fetched is None

    @pytest.mark.asyncio
    async def test_duplicate_insert_fails(self):
        async with async_session() as session:
            session.add(
                WorkspaceMember(
                    workspace_id="ws-crud-3", user_id="u-crud-3", role="member"
                )
            )
            await session.commit()

            session.add(
                WorkspaceMember(
                    workspace_id="ws-crud-3", user_id="u-crud-3", role="admin"
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_update_role(self):
        async with async_session() as session:
            member = WorkspaceMember(
                workspace_id="ws-crud-4", user_id="u-crud-4", role="member"
            )
            session.add(member)
            await session.commit()

            member.role = "workspace_admin"
            await session.commit()
            await session.refresh(member)

            assert member.role == "workspace_admin"


# ---------------------------------------------------------------------------
# 3. Migration tests (sync SQLite)
# ---------------------------------------------------------------------------
class TestWorkspaceMemberMigration:
    def test_migration_creates_table(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine, include_users=True)
            with engine.connect() as conn:
                assert "workspace_members" not in inspect(conn).get_table_names()

            with engine.begin() as conn:
                _migrate_schema(conn)

            with engine.connect() as conn:
                assert "workspace_members" in inspect(conn).get_table_names()
        finally:
            _dispose(engine, path)

    def test_migration_backfills_from_workspace_ids(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            _insert_user(engine, "u-1", "member", ["ws1", "ws2"])

            with engine.begin() as conn:
                _migrate_schema(conn)

            rows = _fetch_workspace_members(engine)
            assert len(rows) == 2
            assert ("ws1", "u-1", "member") in rows
            assert ("ws2", "u-1", "member") in rows
        finally:
            _dispose(engine, path)

    def test_migration_role_inference_tenant_admin(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            _insert_user(engine, "u-1", "tenant_admin", ["ws1", "ws2"])

            with engine.begin() as conn:
                _migrate_schema(conn)

            rows = _fetch_workspace_members(engine)
            assert len(rows) == 2
            for ws_id, user_id, role in rows:
                assert user_id == "u-1"
                assert role == "workspace_admin"
        finally:
            _dispose(engine, path)

    def test_migration_role_inference_workspace_admin(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            _insert_user(engine, "u-1", "workspace_admin", ["ws1"])

            with engine.begin() as conn:
                _migrate_schema(conn)

            rows = _fetch_workspace_members(engine)
            assert len(rows) == 1
            assert rows[0][2] == "workspace_admin"
        finally:
            _dispose(engine, path)

    def test_migration_role_inference_member(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            _insert_user(engine, "u-1", "member", ["ws1"])

            with engine.begin() as conn:
                _migrate_schema(conn)

            rows = _fetch_workspace_members(engine)
            assert len(rows) == 1
            assert rows[0][2] == "member"
        finally:
            _dispose(engine, path)

    def test_migration_role_inference_viewer(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            _insert_user(engine, "u-1", "viewer", ["ws1"])

            with engine.begin() as conn:
                _migrate_schema(conn)

            rows = _fetch_workspace_members(engine)
            assert len(rows) == 1
            # viewer is downgraded to member in the new model
            assert rows[0][2] == "member"
        finally:
            _dispose(engine, path)

    def test_migration_idempotent(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            _insert_user(engine, "u-1", "member", ["ws1", "ws2"])

            with engine.begin() as conn:
                _migrate_schema(conn)
            with engine.begin() as conn:
                _migrate_schema(conn)  # second run must not duplicate

            rows = _fetch_workspace_members(engine)
            assert len(rows) == 2
            assert ("ws1", "u-1", "member") in rows
            assert ("ws2", "u-1", "member") in rows
        finally:
            _dispose(engine, path)

    def test_migration_empty_workspace_ids(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            _insert_user(engine, "u-1", "member", [])

            with engine.begin() as conn:
                _migrate_schema(conn)

            rows = _fetch_workspace_members(engine)
            assert all(r[1] != "u-1" for r in rows)
        finally:
            _dispose(engine, path)

    def test_migration_no_users_table(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine, include_users=False)

            # Migration must not raise when users table is absent.
            with engine.begin() as conn:
                _migrate_schema(conn)
        finally:
            _dispose(engine, path)
