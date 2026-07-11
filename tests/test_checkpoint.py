"""Tests for Checkpoint model and SQLiteCheckpointStore.

Covers:
- Checkpoint: default field values, full-field construction
- SQLiteCheckpointStore: save/load, load_latest, load by sequence, list,
  delete, next_sequence, missing session returns None

Wave 2.5: CheckpointScope tests removed (class deleted with DirectLLM).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from src.infra.db.engine import async_session, engine
from src.infra.db.models import Base
from src.runtime.harness.checkpoint import (
    Checkpoint,
    SQLiteCheckpointStore,
)


# ── DB setup ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def setup_db():
    """Create tables before each test, drop after.

    Base.metadata tables are created by the session-scoped conftest
    fixture; the ``checkpoints`` table is a raw-SQL table (M13
    migration) so we create it here explicitly.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS checkpoints ("
                "session_id VARCHAR(32) NOT NULL,"
                "sequence INTEGER NOT NULL,"
                "messages TEXT NOT NULL,"
                "tool_state TEXT NOT NULL,"
                "agent_id VARCHAR(32) NOT NULL,"
                "metadata TEXT NOT NULL DEFAULT '{}',"
                "created_at DATETIME NOT NULL,"
                "PRIMARY KEY (session_id, sequence)"
                ")"
            )
        )
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS checkpoints"))


# ── TestCheckpointModel ─────────────────────────────────────────────────


class TestCheckpointModel:
    def test_checkpoint_defaults(self):
        cp = Checkpoint(session_id="s", sequence=1)
        assert cp.session_id == "s"
        assert cp.sequence == 1
        assert cp.messages == []
        assert cp.tool_state == {}
        assert cp.agent_id == ""
        assert cp.metadata == {}
        assert cp.created_at is None

    def test_checkpoint_with_fields(self):
        ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        cp = Checkpoint(
            session_id="s2",
            sequence=7,
            messages=[{"role": "user", "content": "hi"}],
            tool_state={"tool_x": {"done": True}},
            agent_id="agent-9",
            metadata={"run_id": "r1"},
            created_at=ts,
        )
        assert cp.session_id == "s2"
        assert cp.sequence == 7
        assert cp.messages == [{"role": "user", "content": "hi"}]
        assert cp.tool_state == {"tool_x": {"done": True}}
        assert cp.agent_id == "agent-9"
        assert cp.metadata == {"run_id": "r1"}
        assert cp.created_at == ts


# ── TestSQLiteCheckpointStore ───────────────────────────────────────────


class TestSQLiteCheckpointStore:
    @pytest.mark.asyncio
    async def test_save_and_load(self):
        store = SQLiteCheckpointStore()
        cp = Checkpoint(
            session_id="s1",
            sequence=1,
            messages=[{"role": "user", "content": "hello"}],
            tool_state={"t": {"v": 1}},
            agent_id="a1",
            metadata={"k": "v"},
        )
        await store.save(cp)
        loaded = await store.load("s1", 1)
        assert loaded is not None
        assert loaded.session_id == "s1"
        assert loaded.sequence == 1
        assert loaded.messages == [{"role": "user", "content": "hello"}]
        assert loaded.tool_state == {"t": {"v": 1}}
        assert loaded.agent_id == "a1"
        assert loaded.metadata == {"k": "v"}
        assert loaded.created_at is not None

    @pytest.mark.asyncio
    async def test_load_latest(self):
        store = SQLiteCheckpointStore()
        for seq in (1, 2, 3):
            await store.save(
                Checkpoint(
                    session_id="s2",
                    sequence=seq,
                    messages=[{"seq": seq}],
                    tool_state={},
                    agent_id="a2",
                )
            )
        latest = await store.load("s2")
        assert latest is not None
        assert latest.sequence == 3

    @pytest.mark.asyncio
    async def test_load_specific_sequence(self):
        store = SQLiteCheckpointStore()
        for seq in (1, 2, 3):
            await store.save(
                Checkpoint(
                    session_id="s3",
                    sequence=seq,
                    messages=[{"seq": seq}],
                    tool_state={},
                    agent_id="a3",
                )
            )
        cp = await store.load("s3", 2)
        assert cp is not None
        assert cp.sequence == 2
        assert cp.messages == [{"seq": 2}]

    @pytest.mark.asyncio
    async def test_list_all(self):
        store = SQLiteCheckpointStore()
        for seq in (1, 2, 3):
            await store.save(
                Checkpoint(
                    session_id="s4",
                    sequence=seq,
                    messages=[],
                    tool_state={},
                    agent_id="a4",
                )
            )
        all_cps = await store.list("s4")
        assert len(all_cps) == 3
        assert [cp.sequence for cp in all_cps] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_delete(self):
        store = SQLiteCheckpointStore()
        await store.save(
            Checkpoint(
                session_id="s5",
                sequence=1,
                messages=[],
                tool_state={},
                agent_id="a5",
            )
        )
        await store.save(
            Checkpoint(
                session_id="s5",
                sequence=2,
                messages=[],
                tool_state={},
                agent_id="a5",
            )
        )
        await store.delete("s5", 1)
        remaining = await store.list("s5")
        assert len(remaining) == 1
        assert remaining[0].sequence == 2

    @pytest.mark.asyncio
    async def test_next_sequence(self):
        store = SQLiteCheckpointStore()
        await store.save(
            Checkpoint(
                session_id="s6",
                sequence=1,
                messages=[],
                tool_state={},
                agent_id="a6",
            )
        )
        await store.save(
            Checkpoint(
                session_id="s6",
                sequence=2,
                messages=[],
                tool_state={},
                agent_id="a6",
            )
        )
        assert await store.next_sequence("s6") == 3

    @pytest.mark.asyncio
    async def test_load_missing_returns_none(self):
        store = SQLiteCheckpointStore()
        assert await store.load("nonexistent-session") is None
