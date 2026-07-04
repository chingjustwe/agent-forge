"""P1-3: Workspace field completion — slug / description / icon / owner_id / updated_at.

Follows TDD red-green-refactor — these tests were written BEFORE the
implementation changes and initially fail (RED), then pass after the
model + migration + route updates (GREEN).
"""
import os
import tempfile

import pytest
import uuid as _uuid
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, inspect

from src.gateway.auth.jwt import create_jwt
from src.infra.db.engine import async_session
from src.infra.db.models import Base, Tenant, Workspace, User
from src.main import _migrate_schema


pytestmark = pytest.mark.asyncio


def _admin_token(tenant_id: str, user_id: str = "admin-slug") -> str:
    return create_jwt({
        "id": user_id,
        "sub": user_id,
        "tenant_id": tenant_id,
        "email": "admin@slug.test",
        "role": "tenant_admin",
    })


@pytest.fixture
def app():
    from src.main import create_app
    return create_app()


# ---------------------------------------------------------------------------
# 1. slugify unit tests
# ---------------------------------------------------------------------------
class TestSlugify:
    def test_slugify_basic(self):
        from src.utils.slugify import slugify
        assert slugify("Team Alpha") == "team-alpha"

    def test_slugify_special_chars(self):
        from src.utils.slugify import slugify
        assert slugify("Team Alpha!@#") == "team-alpha"

    def test_slugify_empty_falls_back(self):
        from src.utils.slugify import slugify
        assert slugify("") == "workspace"
        assert slugify("!!!") == "workspace"

    def test_slugify_multiple_separators(self):
        from src.utils.slugify import slugify
        assert slugify("Team   Alpha__Beta") == "team-alpha-beta"

    def test_slugify_strips_leading_trailing_dash(self):
        from src.utils.slugify import slugify
        assert slugify("  -Team Alpha-  ") == "team-alpha"


# ---------------------------------------------------------------------------
# 2. Model field tests
# ---------------------------------------------------------------------------
class TestWorkspaceModelFields:
    def test_slug_field_exists(self):
        cols = {c.name for c in Workspace.__table__.columns}
        assert "slug" in cols
        assert Workspace.__table__.columns["slug"].nullable is True

    def test_description_field_exists(self):
        cols = {c.name for c in Workspace.__table__.columns}
        assert "description" in cols
        assert Workspace.__table__.columns["description"].nullable is True

    def test_icon_field_exists(self):
        cols = {c.name for c in Workspace.__table__.columns}
        assert "icon" in cols
        assert Workspace.__table__.columns["icon"].nullable is True

    def test_owner_id_field_exists(self):
        cols = {c.name for c in Workspace.__table__.columns}
        assert "owner_id" in cols
        assert Workspace.__table__.columns["owner_id"].nullable is True

    def test_owner_id_foreign_key(self):
        fks = Workspace.__table__.columns["owner_id"].foreign_keys
        assert any(fk.target_fullname == "users.id" for fk in fks)

    def test_updated_at_field_exists(self):
        cols = {c.name for c in Workspace.__table__.columns}
        assert "updated_at" in cols


# ---------------------------------------------------------------------------
# 3. Migration tests (sync SQLite, isolated per test)
# ---------------------------------------------------------------------------
def _make_sync_engine():
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


def _create_legacy_tables(engine):
    """Create tenants/workspaces/users with the *old* column set (no slug/description/icon/owner_id/updated_at)."""
    tables = [Tenant.__table__, User.__table__]
    with engine.begin() as conn:
        Base.metadata.create_all(conn, tables=tables)
        # Manually create workspaces WITHOUT the new columns to simulate a legacy DB.
        conn.exec_driver_sql(
            "CREATE TABLE workspaces ("
            "id VARCHAR(32) NOT NULL PRIMARY KEY,"
            "tenant_id VARCHAR(32) NOT NULL REFERENCES tenants(id),"
            "name VARCHAR(255) NOT NULL,"
            "created_at DATETIME,"
            "settings JSON,"
            "max_tokens_per_day INTEGER,"
            "max_cost_per_month FLOAT,"
            "archived INTEGER,"
            "is_default INTEGER"
            ")"
        )


class TestMigrationM4:
    def test_migration_m4_adds_columns(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            with engine.connect() as conn:
                cols = {c["name"] for c in inspect(conn).get_columns("workspaces")}
                assert "slug" not in cols
                assert "description" not in cols
                assert "icon" not in cols
                assert "owner_id" not in cols
                assert "updated_at" not in cols

            with engine.begin() as conn:
                _migrate_schema(conn)

            with engine.connect() as conn:
                cols = {c["name"] for c in inspect(conn).get_columns("workspaces")}
                assert "slug" in cols
                assert "description" in cols
                assert "icon" in cols
                assert "owner_id" in cols
                assert "updated_at" in cols
        finally:
            _dispose(engine, path)

    def test_migration_m4_idempotent(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            with engine.begin() as conn:
                _migrate_schema(conn)
            # Running again must not raise.
            with engine.begin() as conn:
                _migrate_schema(conn)

            with engine.connect() as conn:
                cols = {c["name"] for c in inspect(conn).get_columns("workspaces")}
                assert "slug" in cols
                assert "updated_at" in cols
        finally:
            _dispose(engine, path)


class TestMigrationM5:
    def test_migration_m5_backfills_slug_from_name(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "INSERT INTO tenants (id, name, domain, created_at, settings) "
                    "VALUES ('t1', 'T', 't1.test', datetime('now'), '{}')"
                )
                conn.exec_driver_sql(
                    "INSERT INTO workspaces (id, tenant_id, name, created_at, settings, "
                    "max_tokens_per_day, max_cost_per_month, archived, is_default) "
                    "VALUES ('ws1', 't1', 'Team Alpha', datetime('now'), '{}', 1000000, 0.0, 0, 0)"
                )
                conn.exec_driver_sql(
                    "INSERT INTO workspaces (id, tenant_id, name, created_at, settings, "
                    "max_tokens_per_day, max_cost_per_month, archived, is_default) "
                    "VALUES ('ws2', 't1', 'Beta!@#', datetime('now'), '{}', 1000000, 0.0, 0, 0)"
                )

            with engine.begin() as conn:
                _migrate_schema(conn)

            with engine.connect() as conn:
                rows = conn.exec_driver_sql(
                    "SELECT id, slug FROM workspaces ORDER BY id"
                ).fetchall()
                slugs = {row[0]: row[1] for row in rows}
                assert slugs["ws1"] == "team-alpha"
                assert slugs["ws2"] == "beta"
        finally:
            _dispose(engine, path)

    def test_migration_m5_appends_suffix_on_conflict(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "INSERT INTO tenants (id, name, domain, created_at, settings) "
                    "VALUES ('t1', 'T', 't1.test', datetime('now'), '{}')"
                )
                # Mark the first one as default so the seed block does not
                # create an extra "Default" workspace for this tenant.
                for i in range(3):
                    is_default = 1 if i == 0 else 0
                    conn.exec_driver_sql(
                        f"INSERT INTO workspaces (id, tenant_id, name, created_at, settings, "
                        f"max_tokens_per_day, max_cost_per_month, archived, is_default) "
                        f"VALUES ('ws{i}', 't1', 'Team Alpha', datetime('now'), '{{}}', 1000000, 0.0, 0, {is_default})"
                    )

            with engine.begin() as conn:
                _migrate_schema(conn)

            with engine.connect() as conn:
                rows = conn.exec_driver_sql(
                    "SELECT id, slug FROM workspaces ORDER BY id"
                ).fetchall()
                slugs = sorted(row[1] for row in rows)
                assert slugs == ["team-alpha", "team-alpha-2", "team-alpha-3"]
        finally:
            _dispose(engine, path)

    def test_migration_m5_does_not_overwrite_existing_slug(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "INSERT INTO tenants (id, name, domain, created_at, settings) "
                    "VALUES ('t1', 'T', 't1.test', datetime('now'), '{}')"
                )
                conn.exec_driver_sql(
                    "INSERT INTO workspaces (id, tenant_id, name, created_at, settings, "
                    "max_tokens_per_day, max_cost_per_month, archived, is_default) "
                    "VALUES ('ws1', 't1', 'Team Alpha', datetime('now'), '{}', 1000000, 0.0, 0, 0)"
                )
                # Pre-set a slug so M5 should skip it.
                # M4 adds the column first; then we set the slug manually.
                _migrate_schema(conn)
                conn.exec_driver_sql("UPDATE workspaces SET slug = 'custom' WHERE id = 'ws1'")
                # Run migration again — must not overwrite the custom slug.
                _migrate_schema(conn)

            with engine.connect() as conn:
                row = conn.exec_driver_sql(
                    "SELECT slug FROM workspaces WHERE id = 'ws1'"
                ).fetchone()
                assert row[0] == "custom"
        finally:
            _dispose(engine, path)

    def test_migration_m5b_creates_unique_index(self):
        """Q6: M5b creates a tenant-local unique index on slug."""
        from sqlalchemy.exc import IntegrityError

        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "INSERT INTO tenants (id, name, domain, created_at, settings) "
                    "VALUES ('t1', 'T', 't1.test', datetime('now'), '{}')"
                )
                conn.exec_driver_sql(
                    "INSERT INTO workspaces (id, tenant_id, name, created_at, settings, "
                    "max_tokens_per_day, max_cost_per_month, archived, is_default) "
                    "VALUES ('ws1', 't1', 'Alpha', datetime('now'), '{}', 1000000, 0.0, 0, 1)"
                )
                _migrate_schema(conn)

            # Index should exist
            with engine.connect() as conn:
                idxs = conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='workspaces'"
                ).fetchall()
                idx_names = {r[0] for r in idxs}
                assert "ix_workspaces_tenant_slug" in idx_names

            # Inserting a duplicate slug should fail (IntegrityError)
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        "INSERT INTO workspaces (id, tenant_id, name, slug, created_at, settings, "
                        "max_tokens_per_day, max_cost_per_month, archived, is_default) "
                        "VALUES ('ws2', 't1', 'Dup', 'alpha', datetime('now'), '{}', 1000000, 0.0, 0, 0)"
                    )

            # NULL slugs are allowed multiple times (SQLite UNIQUE semantics)
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "UPDATE workspaces SET slug = NULL WHERE id = 'ws1'"
                )
                conn.exec_driver_sql(
                    "INSERT INTO workspaces (id, tenant_id, name, slug, created_at, settings, "
                    "max_tokens_per_day, max_cost_per_month, archived, is_default) "
                    "VALUES ('ws3', 't1', 'NoSlug', NULL, datetime('now'), '{}', 1000000, 0.0, 0, 0)"
                )
        finally:
            _dispose(engine, path)


# ---------------------------------------------------------------------------
# 4. Route tests — /api/v1/workspaces
# ---------------------------------------------------------------------------
class TestCreateWorkspaceRoute:
    async def test_create_workspace_auto_generates_slug(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-as-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/workspaces",
                json={"name": "Team Alpha"},
                headers={"Authorization": f"Bearer {_admin_token(tid)}"},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["slug"] == "team-alpha"

    async def test_create_workspace_explicit_slug(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-es-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/workspaces",
                json={"name": "Team Alpha", "slug": "custom-slug", "description": "d", "icon": "🚀"},
                headers={"Authorization": f"Bearer {_admin_token(tid)}"},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["slug"] == "custom-slug"
            assert body["description"] == "d"
            assert body["icon"] == "🚀"

    async def test_create_workspace_slug_conflict_appends_suffix(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-cf-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            session.add(Workspace(id=f"ws-{suffix}-1", tenant_id=tid, name="Team Alpha", slug="team-alpha"))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/workspaces",
                json={"name": "Team Alpha"},
                headers={"Authorization": f"Bearer {_admin_token(tid)}"},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["slug"] == "team-alpha-2"

    async def test_create_workspace_sets_owner_id(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-oi-{suffix}"
        uid = f"u-oi-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            session.add(User(id=uid, tenant_id=tid, email=f"{uid}@test.com", name=uid, role="tenant_admin"))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/workspaces",
                json={"name": "Owned WS"},
                headers={"Authorization": f"Bearer {_admin_token(tid, uid)}"},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["owner_id"] == uid

    async def test_list_workspaces_includes_new_fields(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-lf-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            session.add(Workspace(
                id=f"ws-lf-{suffix}",
                tenant_id=tid,
                name="WS",
                slug="ws-slug",
                description="d",
                icon="🚀",
            ))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/workspaces",
                headers={"Authorization": f"Bearer {_admin_token(tid)}"},
            )
            assert resp.status_code == 200
            by_id = {w["id"]: w for w in resp.json()}
            ws = by_id[f"ws-lf-{suffix}"]
            assert ws["slug"] == "ws-slug"
            assert ws["description"] == "d"
            assert ws["icon"] == "🚀"
            # updated_at may be None on legacy rows but the key must be present
            assert "updated_at" in ws
            assert "owner_id" in ws


# ---------------------------------------------------------------------------
# 5. Admin route tests — /api/v1/admin/workspaces
# ---------------------------------------------------------------------------
class TestAdminWorkspacesRoute:
    async def test_admin_list_includes_new_fields(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-al-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            session.add(Workspace(
                id=f"ws-al-{suffix}",
                tenant_id=tid,
                name="WS",
                slug="admin-slug",
                description="d",
                icon="🚀",
            ))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/admin/workspaces",
                headers={"Authorization": f"Bearer {_admin_token(tid)}"},
            )
            assert resp.status_code == 200
            by_id = {w["id"]: w for w in resp.json()}
            ws = by_id[f"ws-al-{suffix}"]
            assert ws["slug"] == "admin-slug"
            assert ws["description"] == "d"
            assert ws["icon"] == "🚀"
            assert "owner_id" in ws
            assert "updated_at" in ws

    async def test_admin_create_with_new_fields(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-ac-{suffix}"
        uid = f"u-ac-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            session.add(User(id=uid, tenant_id=tid, email=f"{uid}@test.com", name=uid, role="tenant_admin"))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/admin/workspaces",
                json={"name": "WS", "slug": "admin-create", "description": "dd", "icon": "🌱"},
                headers={"Authorization": f"Bearer {_admin_token(tid, uid)}"},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["slug"] == "admin-create"
            assert body["description"] == "dd"
            assert body["icon"] == "🌱"
            assert body["owner_id"] == uid

    async def test_admin_update_slug_succeeds_when_unique(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-us-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            session.add(Workspace(id=f"ws-us-{suffix}", tenant_id=tid, name="WS", slug="old"))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.put(
                f"/api/v1/admin/workspaces/ws-us-{suffix}",
                json={"slug": "new-slug", "description": "updated", "icon": "🔥"},
                headers={"Authorization": f"Bearer {_admin_token(tid)}"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["slug"] == "new-slug"
            assert body["description"] == "updated"
            assert body["icon"] == "🔥"

    async def test_admin_update_slug_conflict_returns_409(self, app):
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-uc-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            session.add(Workspace(id=f"ws-uc-a-{suffix}", tenant_id=tid, name="A", slug="taken"))
            session.add(Workspace(id=f"ws-uc-b-{suffix}", tenant_id=tid, name="B", slug="other"))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Try to update B's slug to "taken" (already A's) → 409
            resp = await ac.put(
                f"/api/v1/admin/workspaces/ws-uc-b-{suffix}",
                json={"slug": "taken"},
                headers={"Authorization": f"Bearer {_admin_token(tid)}"},
            )
            assert resp.status_code == 409
            body = resp.json()
            assert body["error"]["code"] == "SLUG_CONFLICT"

    async def test_admin_update_refreshes_updated_at(self, app):
        import asyncio
        suffix = _uuid.uuid4().hex[:8]
        tid = f"t-uu-{suffix}"
        async with async_session() as session:
            session.add(Tenant(id=tid, name="T", domain=f"{tid}.test"))
            session.add(Workspace(id=f"ws-uu-{suffix}", tenant_id=tid, name="WS", slug="uu"))
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp1 = await ac.put(
                f"/api/v1/admin/workspaces/ws-uu-{suffix}",
                json={"description": "first"},
                headers={"Authorization": f"Bearer {_admin_token(tid)}"},
            )
            assert resp1.status_code == 200
            first_updated = resp1.json().get("updated_at")

            # Sleep briefly so the timestamp (if second-precision) differs.
            await asyncio.sleep(1.1)

            resp2 = await ac.put(
                f"/api/v1/admin/workspaces/ws-uu-{suffix}",
                json={"description": "second"},
                headers={"Authorization": f"Bearer {_admin_token(tid)}"},
            )
            assert resp2.status_code == 200
            second_updated = resp2.json().get("updated_at")

            assert first_updated is not None
            assert second_updated is not None
            assert second_updated != first_updated
