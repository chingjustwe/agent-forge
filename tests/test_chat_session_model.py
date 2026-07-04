"""Tests for ChatSession + ChatMessage models and the M3 migration.

RED phase: these tests exercise models and migration that have not been
implemented yet — they are expected to fail until the GREEN phase adds
the ``ChatSession`` / ``ChatMessage`` models and the M3 migration block
inside ``_migrate_schema``.
"""
import os
import tempfile

import pytest
from sqlalchemy import create_engine, inspect

from src.infra.db.engine import async_session
from src.infra.db.models import Base, ChatMessage, ChatSession, Tenant, Workspace, User
from src.main import _migrate_schema


# ---------------------------------------------------------------------------
# Helpers (mirror test_workspace_member_model.py patterns)
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
    """Create tenants/workspaces/users — but NOT chat_sessions / chat_messages."""
    tables = [Tenant.__table__, Workspace.__table__, User.__table__]
    with engine.begin() as conn:
        Base.metadata.create_all(conn, tables=tables)


# ---------------------------------------------------------------------------
# 1. ChatSession model definition
# ---------------------------------------------------------------------------
class TestChatSessionModel:
    def test_tablename(self):
        assert ChatSession.__tablename__ == "chat_sessions"

    def test_fields_exist(self):
        cols = {c.name for c in ChatSession.__table__.columns}
        assert {
            "id",
            "workspace_id",
            "owner_id",
            "title",
            "visibility",
            "agent_name",
            "archived",
            "created_at",
            "updated_at",
        } <= cols

    def test_workspace_id_foreign_key(self):
        fks = ChatSession.__table__.columns["workspace_id"].foreign_keys
        assert any(fk.target_fullname == "workspaces.id" for fk in fks)

    def test_owner_id_foreign_key(self):
        fks = ChatSession.__table__.columns["owner_id"].foreign_keys
        assert any(fk.target_fullname == "users.id" for fk in fks)

    def test_default_title(self):
        col = ChatSession.__table__.columns["title"]
        assert col.default is not None
        arg = col.default.arg
        if callable(arg):
            arg = arg(None)
        assert arg == "New Chat"

    def test_default_visibility_private(self):
        col = ChatSession.__table__.columns["visibility"]
        assert col.default is not None
        arg = col.default.arg
        if callable(arg):
            arg = arg(None)
        assert arg == "private"

    def test_default_archived_zero(self):
        col = ChatSession.__table__.columns["archived"]
        assert col.default is not None
        arg = col.default.arg
        if callable(arg):
            arg = arg(None)
        assert arg == 0

    def test_agent_name_nullable(self):
        assert ChatSession.__table__.columns["agent_name"].nullable is True


# ---------------------------------------------------------------------------
# 2. ChatMessage model definition
# ---------------------------------------------------------------------------
class TestChatMessageModel:
    def test_tablename(self):
        assert ChatMessage.__tablename__ == "chat_messages"

    def test_fields_exist(self):
        cols = {c.name for c in ChatMessage.__table__.columns}
        assert {
            "id",
            "session_id",
            "role",
            "content",
            "tokens",
            "created_at",
        } <= cols

    def test_session_id_foreign_key(self):
        fks = ChatMessage.__table__.columns["session_id"].foreign_keys
        assert any(fk.target_fullname == "chat_sessions.id" for fk in fks)

    def test_default_tokens_zero(self):
        col = ChatMessage.__table__.columns["tokens"]
        assert col.default is not None
        arg = col.default.arg
        if callable(arg):
            arg = arg(None)
        assert arg == 0


# ---------------------------------------------------------------------------
# 3. CRUD tests (async)
# ---------------------------------------------------------------------------
class TestChatSessionCRUD:
    @pytest.mark.asyncio
    async def test_create_session_defaults(self):
        async with async_session() as session:
            # Need parent rows to satisfy FKs.
            session.add(Tenant(id="t-cs-1", name="T", domain="t-cs-1.test"))
            session.add(Workspace(id="ws-cs-1", tenant_id="t-cs-1", name="WS"))
            session.add(User(id="u-cs-1", tenant_id="t-cs-1", email="u1@t.test", name="U1"))
            await session.flush()

            cs = ChatSession(workspace_id="ws-cs-1", owner_id="u-cs-1")
            session.add(cs)
            await session.commit()
            await session.refresh(cs)

            assert cs.id  # auto-generated
            assert cs.title == "New Chat"
            assert cs.visibility == "private"
            assert cs.archived == 0
            assert cs.agent_name is None

    @pytest.mark.asyncio
    async def test_create_session_with_visibility(self):
        async with async_session() as session:
            session.add(Tenant(id="t-cs-2", name="T", domain="t-cs-2.test"))
            session.add(Workspace(id="ws-cs-2", tenant_id="t-cs-2", name="WS"))
            session.add(User(id="u-cs-2", tenant_id="t-cs-2", email="u2@t.test", name="U2"))
            await session.flush()

            cs = ChatSession(
                workspace_id="ws-cs-2",
                owner_id="u-cs-2",
                title="My Chat",
                visibility="workspace",
                agent_name="direct-llm",
            )
            session.add(cs)
            await session.commit()
            await session.refresh(cs)

            assert cs.title == "My Chat"
            assert cs.visibility == "workspace"
            assert cs.agent_name == "direct-llm"

    @pytest.mark.asyncio
    async def test_soft_delete_session(self):
        async with async_session() as session:
            session.add(Tenant(id="t-cs-3", name="T", domain="t-cs-3.test"))
            session.add(Workspace(id="ws-cs-3", tenant_id="t-cs-3", name="WS"))
            session.add(User(id="u-cs-3", tenant_id="t-cs-3", email="u3@t.test", name="U3"))
            await session.flush()

            cs = ChatSession(workspace_id="ws-cs-3", owner_id="u-cs-3")
            session.add(cs)
            await session.commit()
            await session.refresh(cs)

            cs.archived = 1
            await session.commit()
            await session.refresh(cs)
            assert cs.archived == 1

    @pytest.mark.asyncio
    async def test_create_message(self):
        async with async_session() as session:
            session.add(Tenant(id="t-cs-4", name="T", domain="t-cs-4.test"))
            session.add(Workspace(id="ws-cs-4", tenant_id="t-cs-4", name="WS"))
            session.add(User(id="u-cs-4", tenant_id="t-cs-4", email="u4@t.test", name="U4"))
            await session.flush()

            cs = ChatSession(workspace_id="ws-cs-4", owner_id="u-cs-4")
            session.add(cs)
            await session.commit()
            await session.refresh(cs)

            msg = ChatMessage(
                session_id=cs.id,
                role="user",
                content="hello",
                tokens=5,
            )
            session.add(msg)
            await session.commit()
            await session.refresh(msg)

            assert msg.id
            assert msg.role == "user"
            assert msg.content == "hello"
            assert msg.tokens == 5

    @pytest.mark.asyncio
    async def test_message_default_tokens_zero(self):
        async with async_session() as session:
            session.add(Tenant(id="t-cs-5", name="T", domain="t-cs-5.test"))
            session.add(Workspace(id="ws-cs-5", tenant_id="t-cs-5", name="WS"))
            session.add(User(id="u-cs-5", tenant_id="t-cs-5", email="u5@t.test", name="U5"))
            await session.flush()

            cs = ChatSession(workspace_id="ws-cs-5", owner_id="u-cs-5")
            session.add(cs)
            await session.commit()
            await session.refresh(cs)

            msg = ChatMessage(session_id=cs.id, role="assistant", content="hi")
            session.add(msg)
            await session.commit()
            await session.refresh(msg)
            assert msg.tokens == 0


# ---------------------------------------------------------------------------
# 4. Migration tests (sync SQLite)
# ---------------------------------------------------------------------------
class TestChatSessionMigration:
    def test_migration_creates_chat_tables(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            with engine.connect() as conn:
                tables = inspect(conn).get_table_names()
                assert "chat_sessions" not in tables
                assert "chat_messages" not in tables

            with engine.begin() as conn:
                _migrate_schema(conn)

            with engine.connect() as conn:
                tables = inspect(conn).get_table_names()
                assert "chat_sessions" in tables
                assert "chat_messages" in tables
        finally:
            _dispose(engine, path)

    def test_migration_creates_indexes(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            with engine.begin() as conn:
                _migrate_schema(conn)

            with engine.connect() as conn:
                insp = inspect(conn)
                session_indexes = {i["name"] for i in insp.get_indexes("chat_sessions")}
                message_indexes = {i["name"] for i in insp.get_indexes("chat_messages")}
                assert "ix_chat_sessions_workspace_id" in session_indexes
                assert "ix_chat_sessions_owner_id" in session_indexes
                assert "ix_chat_messages_session_id" in message_indexes
        finally:
            _dispose(engine, path)

    def test_migration_idempotent(self):
        engine, path = _make_sync_engine()
        try:
            _create_legacy_tables(engine)
            with engine.begin() as conn:
                _migrate_schema(conn)
            # Running again must not raise.
            with engine.begin() as conn:
                _migrate_schema(conn)

            with engine.connect() as conn:
                tables = inspect(conn).get_table_names()
                assert "chat_sessions" in tables
                assert "chat_messages" in tables
        finally:
            _dispose(engine, path)

    def test_migration_no_legacy_tables(self):
        """Migration must not raise when tenants/workspaces/users are absent."""
        engine, path = _make_sync_engine()
        try:
            with engine.begin() as conn:
                _migrate_schema(conn)
        finally:
            _dispose(engine, path)
