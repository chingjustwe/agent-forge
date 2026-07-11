"""Tests for MemoryRecord, SQLiteMemoryStore, and MemoryScope.

Covers:
- MemoryRecord: default field values, full-field construction
- SQLiteMemoryStore: save/get, save returns id, missing get → None,
  list by scope, per-scope isolation, delete, list limit, recall
  edge cases (save() does not sync the external-content FTS5 index,
  so recall returns empty; list()/get() bypass FTS and are exercised
  thoroughly)
- MemoryScope: remember returns id, remember+list, remember+get,
  scope_id resolution per scope, per-scope isolation, delete,
  multiple coexisting scopes
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from src.infra.db.engine import async_session, engine
from src.runtime.harness.memory import (
    MemoryRecord,
    MemoryScope,
    SQLiteMemoryStore,
)


# ── DB setup ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def setup_db():
    """Create memory_records table + FTS5 virtual table before each test,
    drop after.

    SQLiteMemoryStore.save() inserts into memory_records only; it does
    not sync the external-content FTS5 index, so recall() will return
    empty unless the FTS table is manually populated. These tests
    therefore exercise list()/get() (which bypass FTS) and verify
    recall() edge-case behavior.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS memory_records ("
                "id VARCHAR(32) NOT NULL PRIMARY KEY,"
                "scope VARCHAR(20) NOT NULL,"
                "scope_id VARCHAR(32) NOT NULL,"
                "key TEXT,"
                "content TEXT NOT NULL,"
                "metadata TEXT NOT NULL DEFAULT '{}',"
                "memory_type TEXT NOT NULL DEFAULT 'episodic',"
                "created_at DATETIME NOT NULL,"
                "expires_at DATETIME"
                ")"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_memory_scope "
                "ON memory_records (scope, scope_id)"
            )
        )
        await conn.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_records_fts USING fts5("
                "content, content='memory_records', content_rowid='rowid'"
                ")"
            )
        )
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS memory_records_fts"))
        await conn.execute(text("DROP TABLE IF EXISTS memory_records"))


# ── TestMemoryRecord ────────────────────────────────────────────────────


class TestMemoryRecord:
    def test_defaults(self):
        rec = MemoryRecord(
            id="r1", scope="session", scope_id="s1", content="hello"
        )
        assert rec.id == "r1"
        assert rec.scope == "session"
        assert rec.scope_id == "s1"
        assert rec.content == "hello"
        assert rec.key == ""
        assert rec.memory_type == "episodic"
        assert rec.metadata == {}
        assert rec.embedding is None
        assert rec.created_at is None
        assert rec.expires_at is None

    def test_with_all_fields(self):
        ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        exp = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        rec = MemoryRecord(
            id="r2",
            scope="user",
            scope_id="u1",
            key="pref",
            content="likes tea",
            embedding=[0.1, 0.2, 0.3],
            metadata={"source": "survey"},
            created_at=ts,
            expires_at=exp,
        )
        assert rec.id == "r2"
        assert rec.scope == "user"
        assert rec.scope_id == "u1"
        assert rec.key == "pref"
        assert rec.content == "likes tea"
        assert rec.embedding == [0.1, 0.2, 0.3]
        assert rec.metadata == {"source": "survey"}
        assert rec.created_at == ts
        assert rec.expires_at == exp


# ── TestSQLiteMemoryStore ───────────────────────────────────────────────


class TestSQLiteMemoryStore:
    @pytest.mark.asyncio
    async def test_save_and_get(self):
        store = SQLiteMemoryStore()
        rec = MemoryRecord(
            id="m1",
            scope="session",
            scope_id="s1",
            key="note",
            content="hello world",
            metadata={"k": "v"},
        )
        await store.save(rec)
        got = await store.get("m1")
        assert got is not None
        assert got.id == "m1"
        assert got.scope == "session"
        assert got.scope_id == "s1"
        assert got.key == "note"
        assert got.content == "hello world"
        assert got.metadata == {"k": "v"}
        assert got.created_at is not None

    @pytest.mark.asyncio
    async def test_save_returns_id(self):
        store = SQLiteMemoryStore()
        rec = MemoryRecord(
            id="m2", scope="session", scope_id="s1", content="data"
        )
        returned = await store.save(rec)
        assert returned == "m2"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self):
        store = SQLiteMemoryStore()
        assert await store.get("does-not-exist") is None

    @pytest.mark.asyncio
    async def test_list_by_scope(self):
        store = SQLiteMemoryStore()
        for i in range(3):
            await store.save(
                MemoryRecord(
                    id=f"l{i}",
                    scope="session",
                    scope_id="sx",
                    content=f"item {i}",
                )
            )
        records = await store.list("session", "sx")
        assert len(records) == 3
        assert {r.id for r in records} == {"l0", "l1", "l2"}

    @pytest.mark.asyncio
    async def test_list_isolated_per_scope(self):
        store = SQLiteMemoryStore()
        await store.save(
            MemoryRecord(id="a1", scope="session", scope_id="sa", content="A")
        )
        await store.save(
            MemoryRecord(id="b1", scope="user", scope_id="ub", content="B")
        )
        await store.save(
            MemoryRecord(
                id="a2", scope="session", scope_id="sa2", content="A2"
            )
        )
        sa_records = await store.list("session", "sa")
        assert len(sa_records) == 1
        assert sa_records[0].id == "a1"
        ub_records = await store.list("user", "ub")
        assert len(ub_records) == 1
        assert ub_records[0].id == "b1"
        sa2_records = await store.list("session", "sa2")
        assert len(sa2_records) == 1
        assert sa2_records[0].id == "a2"

    @pytest.mark.asyncio
    async def test_delete(self):
        store = SQLiteMemoryStore()
        await store.save(
            MemoryRecord(
                id="d1", scope="session", scope_id="s1", content="bye"
            )
        )
        assert await store.get("d1") is not None
        await store.delete("d1")
        assert await store.get("d1") is None

    @pytest.mark.asyncio
    async def test_list_limit(self):
        store = SQLiteMemoryStore()
        for i in range(5):
            await store.save(
                MemoryRecord(
                    id=f"n{i}",
                    scope="session",
                    scope_id="sl",
                    content=f"num {i}",
                )
            )
        records = await store.list("session", "sl", limit=3)
        assert len(records) == 3

    @pytest.mark.asyncio
    async def test_recall_returns_empty_for_no_match(self):
        store = SQLiteMemoryStore()
        await store.save(
            MemoryRecord(
                id="r1",
                scope="session",
                scope_id="s1",
                content="the quick brown fox",
            )
        )
        # FTS index is not synced by save(), so recall returns empty.
        # A non-matching query must also return empty.
        results = await store.recall("elephant", "session", "s1")
        assert results == []

    @pytest.mark.asyncio
    async def test_recall_empty_query_returns_empty(self):
        store = SQLiteMemoryStore()
        await store.save(
            MemoryRecord(
                id="r2",
                scope="session",
                scope_id="s1",
                content="something",
            )
        )
        assert await store.recall("", "session", "s1") == []
        assert await store.recall("   ", "session", "s1") == []


# ── TestMemoryScope ─────────────────────────────────────────────────────


class TestMemoryScope:
    @pytest.mark.asyncio
    async def test_remember_returns_id(self):
        store = SQLiteMemoryStore()
        scope = MemoryScope(
            store=store,
            session_id="sess-1",
            user_id="user-1",
            workspace_id="ws-1",
            agent_id="agent-1",
        )
        rid = await scope.remember(key="k", content="v")
        assert isinstance(rid, str)
        assert len(rid) > 0

    @pytest.mark.asyncio
    async def test_remember_and_list(self):
        store = SQLiteMemoryStore()
        scope = MemoryScope(
            store=store,
            session_id="sess-2",
            user_id="user-2",
            workspace_id="ws-2",
            agent_id="agent-2",
        )
        await scope.remember(key="k", content="v", scope="session")
        records = await scope.list(scope="session")
        assert len(records) == 1
        assert records[0].content == "v"
        assert records[0].key == "k"

    @pytest.mark.asyncio
    async def test_remember_and_get(self):
        store = SQLiteMemoryStore()
        scope = MemoryScope(
            store=store,
            session_id="sess-3",
            user_id="user-3",
            workspace_id="ws-3",
            agent_id="agent-3",
        )
        rid = await scope.remember(
            key="pref", content="dark mode", scope="session"
        )
        got = await scope.get(rid)
        assert got is not None
        assert got.content == "dark mode"
        assert got.key == "pref"

    @pytest.mark.asyncio
    async def test_recall_uses_correct_scope_id(self):
        store = SQLiteMemoryStore()
        scope = MemoryScope(
            store=store,
            session_id="sess-4",
            user_id="user-4",
            workspace_id="ws-4",
            agent_id="agent-4",
        )
        rid = await scope.remember(key="k", content="v", scope="user")
        got = await scope.get(rid)
        assert got is not None
        assert got.scope == "user"
        assert got.scope_id == "user-4"

    @pytest.mark.asyncio
    async def test_list_isolated_per_scope(self):
        store = SQLiteMemoryStore()
        scope = MemoryScope(
            store=store,
            session_id="sess-5",
            user_id="user-5",
            workspace_id="ws-5",
            agent_id="agent-5",
        )
        await scope.remember(key="k", content="session-data", scope="session")
        # Listing the user scope should not surface session-scoped records.
        user_records = await scope.list(scope="user")
        assert user_records == []
        session_records = await scope.list(scope="session")
        assert len(session_records) == 1

    @pytest.mark.asyncio
    async def test_delete(self):
        store = SQLiteMemoryStore()
        scope = MemoryScope(
            store=store,
            session_id="sess-6",
            user_id="user-6",
            workspace_id="ws-6",
            agent_id="agent-6",
        )
        rid = await scope.remember(key="k", content="v", scope="session")
        assert await scope.get(rid) is not None
        await scope.delete(rid)
        assert await scope.get(rid) is None

    @pytest.mark.asyncio
    async def test_multiple_scopes(self):
        store = SQLiteMemoryStore()
        scope = MemoryScope(
            store=store,
            session_id="sess-7",
            user_id="user-7",
            workspace_id="ws-7",
            agent_id="agent-7",
        )
        await scope.remember(key="s", content="session-c", scope="session")
        await scope.remember(key="u", content="user-c", scope="user")
        await scope.remember(key="w", content="workspace-c", scope="workspace")

        session_records = await scope.list(scope="session")
        user_records = await scope.list(scope="user")
        workspace_records = await scope.list(scope="workspace")
        assert len(session_records) == 1
        assert len(user_records) == 1
        assert len(workspace_records) == 1
        assert session_records[0].content == "session-c"
        assert user_records[0].content == "user-c"
        assert workspace_records[0].content == "workspace-c"


# ── Wave 3: memory_type field ──────────────────────────────────────────


class TestMemoryRecordType:
    """MemoryRecord.memory_type field."""

    def test_default_is_episodic(self):
        rec = MemoryRecord(
            id="t1", scope="session", scope_id="s1", content="fact"
        )
        assert rec.memory_type == "episodic"

    def test_profile_type(self):
        rec = MemoryRecord(
            id="t2",
            scope="user",
            scope_id="u1",
            content="prefers Python",
            memory_type="profile",
        )
        assert rec.memory_type == "profile"


class TestMemoryTypePersistence:
    """SQLiteMemoryStore: save/get preserves memory_type."""

    @pytest.mark.asyncio
    async def test_save_and_get_profile(self):
        store = SQLiteMemoryStore()
        rec = MemoryRecord(
            id="mt1",
            scope="user",
            scope_id="u1",
            content="uses uv",
            memory_type="profile",
        )
        await store.save(rec)
        got = await store.get("mt1")
        assert got is not None
        assert got.memory_type == "profile"

    @pytest.mark.asyncio
    async def test_save_and_get_episodic(self):
        store = SQLiteMemoryStore()
        rec = MemoryRecord(
            id="mt2",
            scope="user",
            scope_id="u1",
            content="project in /data",
            memory_type="episodic",
        )
        await store.save(rec)
        got = await store.get("mt2")
        assert got is not None
        assert got.memory_type == "episodic"

    @pytest.mark.asyncio
    async def test_default_episodic_when_not_specified(self):
        store = SQLiteMemoryStore()
        rec = MemoryRecord(
            id="mt3",
            scope="user",
            scope_id="u1",
            content="generic fact",
        )
        await store.save(rec)
        got = await store.get("mt3")
        assert got is not None
        assert got.memory_type == "episodic"


class TestRecallProfiles:
    """SQLiteMemoryStore.recall_profiles: fetch all profile records."""

    @pytest.mark.asyncio
    async def test_recall_profiles_returns_only_profiles(self):
        store = SQLiteMemoryStore()
        await store.save(
            MemoryRecord(
                id="p1",
                scope="user",
                scope_id="u1",
                content="prefers Python",
                memory_type="profile",
            )
        )
        await store.save(
            MemoryRecord(
                id="p2",
                scope="user",
                scope_id="u1",
                content="uses uv",
                memory_type="profile",
            )
        )
        await store.save(
            MemoryRecord(
                id="e1",
                scope="user",
                scope_id="u1",
                content="project in /data",
                memory_type="episodic",
            )
        )
        profiles = await store.recall_profiles("user", "u1")
        assert len(profiles) == 2
        assert {r.id for r in profiles} == {"p1", "p2"}

    @pytest.mark.asyncio
    async def test_recall_profiles_empty_when_none(self):
        store = SQLiteMemoryStore()
        await store.save(
            MemoryRecord(
                id="e1",
                scope="user",
                scope_id="u1",
                content="episodic only",
                memory_type="episodic",
            )
        )
        profiles = await store.recall_profiles("user", "u1")
        assert profiles == []

    @pytest.mark.asyncio
    async def test_recall_profiles_respects_scope(self):
        store = SQLiteMemoryStore()
        await store.save(
            MemoryRecord(
                id="pa",
                scope="user",
                scope_id="u-a",
                content="user A pref",
                memory_type="profile",
            )
        )
        await store.save(
            MemoryRecord(
                id="pb",
                scope="user",
                scope_id="u-b",
                content="user B pref",
                memory_type="profile",
            )
        )
        a_profiles = await store.recall_profiles("user", "u-a")
        b_profiles = await store.recall_profiles("user", "u-b")
        assert len(a_profiles) == 1
        assert a_profiles[0].id == "pa"
        assert len(b_profiles) == 1
        assert b_profiles[0].id == "pb"

    @pytest.mark.asyncio
    async def test_recall_profiles_respects_limit(self):
        store = SQLiteMemoryStore()
        for i in range(5):
            await store.save(
                MemoryRecord(
                    id=f"pl{i}",
                    scope="user",
                    scope_id="u1",
                    content=f"pref {i}",
                    memory_type="profile",
                )
            )
        profiles = await store.recall_profiles("user", "u1", limit=3)
        assert len(profiles) == 3


class TestListByMemoryType:
    """SQLiteMemoryStore.list with memory_type filter."""

    @pytest.mark.asyncio
    async def test_list_profiles_only(self):
        store = SQLiteMemoryStore()
        await store.save(
            MemoryRecord(
                id="lp1",
                scope="user",
                scope_id="u1",
                content="pref 1",
                memory_type="profile",
            )
        )
        await store.save(
            MemoryRecord(
                id="le1",
                scope="user",
                scope_id="u1",
                content="fact 1",
                memory_type="episodic",
            )
        )
        profiles = await store.list("user", "u1", memory_type="profile")
        assert len(profiles) == 1
        assert profiles[0].id == "lp1"

    @pytest.mark.asyncio
    async def test_list_episodic_only(self):
        store = SQLiteMemoryStore()
        await store.save(
            MemoryRecord(
                id="lp2",
                scope="user",
                scope_id="u1",
                content="pref 2",
                memory_type="profile",
            )
        )
        await store.save(
            MemoryRecord(
                id="le2",
                scope="user",
                scope_id="u1",
                content="fact 2",
                memory_type="episodic",
            )
        )
        episodic = await store.list("user", "u1", memory_type="episodic")
        assert len(episodic) == 1
        assert episodic[0].id == "le2"

    @pytest.mark.asyncio
    async def test_list_all_types_when_no_filter(self):
        store = SQLiteMemoryStore()
        await store.save(
            MemoryRecord(
                id="la1",
                scope="user",
                scope_id="u1",
                content="pref",
                memory_type="profile",
            )
        )
        await store.save(
            MemoryRecord(
                id="la2",
                scope="user",
                scope_id="u1",
                content="fact",
                memory_type="episodic",
            )
        )
        all_records = await store.list("user", "u1")
        assert len(all_records) == 2


class TestMemoryScopeMemoryType:
    """MemoryScope: remember/recall/list with memory_type."""

    @pytest.mark.asyncio
    async def test_remember_with_memory_type(self):
        store = SQLiteMemoryStore()
        scope = MemoryScope(
            store=store,
            session_id="sess-mt",
            user_id="user-mt",
            workspace_id="ws-mt",
            agent_id="agent-mt",
        )
        rid = await scope.remember(
            key="pref",
            content="likes dark mode",
            scope="user",
            memory_type="profile",
        )
        got = await scope.get(rid)
        assert got is not None
        assert got.memory_type == "profile"

    @pytest.mark.asyncio
    async def test_remember_default_episodic(self):
        store = SQLiteMemoryStore()
        scope = MemoryScope(
            store=store,
            session_id="sess-mt2",
            user_id="user-mt2",
            workspace_id="ws-mt2",
            agent_id="agent-mt2",
        )
        rid = await scope.remember(
            key="note",
            content="temporary fact",
            scope="session",
        )
        got = await scope.get(rid)
        assert got is not None
        assert got.memory_type == "episodic"

    @pytest.mark.asyncio
    async def test_recall_profiles(self):
        store = SQLiteMemoryStore()
        scope = MemoryScope(
            store=store,
            session_id="sess-mt3",
            user_id="user-mt3",
            workspace_id="ws-mt3",
            agent_id="agent-mt3",
        )
        await scope.remember(
            key="pref",
            content="user pref",
            scope="user",
            memory_type="profile",
        )
        await scope.remember(
            key="fact",
            content="episodic fact",
            scope="user",
            memory_type="episodic",
        )
        profiles = await scope.recall_profiles(scope="user")
        assert len(profiles) == 1
        assert profiles[0].content == "user pref"

    @pytest.mark.asyncio
    async def test_list_with_memory_type_filter(self):
        store = SQLiteMemoryStore()
        scope = MemoryScope(
            store=store,
            session_id="sess-mt4",
            user_id="user-mt4",
            workspace_id="ws-mt4",
            agent_id="agent-mt4",
        )
        await scope.remember(
            key="p", content="pref", scope="user", memory_type="profile"
        )
        await scope.remember(
            key="e", content="fact", scope="user", memory_type="episodic"
        )
        profiles = await scope.list(scope="user", memory_type="profile")
        assert len(profiles) == 1
        assert profiles[0].memory_type == "profile"
        all_records = await scope.list(scope="user")
        assert len(all_records) == 2
