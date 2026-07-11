"""Phase 4: Tests for LangGraphCheckpointShim + LangChainToolShim.

Covers:
- LangGraphCheckpointShim.aget_tuple returns None for empty session
- LangGraphCheckpointShim.aput writes a row with langgraph_checkpoint key
- aput then aget_tuple round-trips the LangGraph checkpoint
- alist yields newest-first (LangGraph convention)
- adelete_thread removes all rows for a thread
- Sync methods (get_tuple, put, etc.) raise NotImplementedError
- LangChainToolShim._arun delegates to ctx.tool_engine.execute
- LangChainToolShim._arun returns ERROR: ... string on tool error
- LangChainToolShim._run raises NotImplementedError
- Shim constructed with empty input_schema still works (permissive model)

Uses an in-memory SQLiteCheckpointStore via the shared test DB fixture.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.base import CheckpointTuple
from sqlalchemy import text

from src.infra.db.engine import engine
from src.infra.db.models import Base
from src.runtime.harness.checkpoint import Checkpoint, SQLiteCheckpointStore
from src.runtime.adapters.langgraph_bridge import (
    LangChainToolShim,
    LangGraphCheckpointShim,
)
from src.runtime.harness.tool_engine import ToolDefinition, ToolResult


# ── DB setup ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def setup_db():
    """Create checkpoints + checkpoint_writes + checkpoint_blobs tables
    before each test.

    All three tables are raw-SQL (M13 / M18 / M20 migrations) so we
    create them here explicitly, mirroring test_checkpoint.py. We DROP
    first because the DB is file-based and a leftover table from a
    previous test run may have an older schema (e.g. checkpoint_writes
    without the M20 checkpoint_id column).
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP TABLE IF EXISTS checkpoint_blobs"))
        await conn.execute(text("DROP TABLE IF EXISTS checkpoint_writes"))
        await conn.execute(text("DROP TABLE IF EXISTS checkpoints"))
        await conn.execute(
            text(
                "CREATE TABLE checkpoints ("
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
        await conn.execute(
            text(
                "CREATE TABLE checkpoint_writes ("
                "session_id VARCHAR(32) NOT NULL,"
                "checkpoint_id VARCHAR(64) NOT NULL DEFAULT '',"
                "task_id VARCHAR(64) NOT NULL,"
                "task_path VARCHAR(255) NOT NULL DEFAULT '',"
                "channel VARCHAR(64) NOT NULL,"
                "value TEXT NOT NULL,"
                "created_at DATETIME NOT NULL,"
                "PRIMARY KEY (session_id, checkpoint_id, task_id, task_path, channel)"
                ")"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE checkpoint_blobs ("
                "session_id VARCHAR(32) NOT NULL,"
                "channel VARCHAR(64) NOT NULL,"
                "version VARCHAR(64) NOT NULL,"
                "type VARCHAR(16) NOT NULL DEFAULT 'json',"
                "payload TEXT NOT NULL,"
                "PRIMARY KEY (session_id, channel, version)"
                ")"
            )
        )
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS checkpoint_blobs"))
        await conn.execute(text("DROP TABLE IF EXISTS checkpoint_writes"))
        await conn.execute(text("DROP TABLE IF EXISTS checkpoints"))


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_checkpoint_dict(
    *,
    messages: list[dict] | None = None,
    seq_id: str = "1",
    channel_versions: dict | None = None,
) -> dict:
    """Build a minimal LangGraph Checkpoint dict for testing.

    ``channel_versions`` defaults to ``{"messages": "1"}`` when messages
    are provided so the M20 blob-rebuild path in ``aget_tuple`` can
    recover them — mirroring how real LangGraph 1.2+ populates
    ``channel_versions`` for any channel that changed this step.
    """
    msgs = messages or []
    if channel_versions is None:
        channel_versions = {"messages": "1"} if msgs else {}
    return {
        "v": 1,
        "id": seq_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel_values": {"messages": msgs},
        "channel_versions": channel_versions,
        "versions_seen": {},
        "pending_sends": [],
    }


def _make_tool_def(
    *,
    name: str = "echo",
    input_schema: dict | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Echo tool: {name}",
        input_schema=input_schema or {"properties": {"text": {"type": "string"}}},
    )


class _FakeToolEngine:
    """Minimal stand-in for ToolEngine that records calls."""

    def __init__(self, result: ToolResult | None = None, error: str | None = None):
        self._result = result
        self._error = error
        self.calls: list[tuple[str, dict, Any]] = []

    async def execute(self, name: str, args: dict, ctx: Any) -> ToolResult:
        self.calls.append((name, args, ctx))
        if self._error:
            from src.runtime.harness.tool_engine import ToolError
            raise ToolError(self._error)
        return self._result or ToolResult(name=name, output="ok")


class _FakeCtx:
    """Minimal stand-in for HarnessContext."""
    def __init__(self, tool_engine: Any, workspace_id: str = "ws"):
        self.tool_engine = tool_engine
        self.workspace_id = workspace_id


# ── LangGraphCheckpointShim ─────────────────────────────────────────────


class TestLangGraphCheckpointShim:
    @pytest.mark.asyncio
    async def test_aget_tuple_returns_none_for_empty_session(self):
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-empty", "a-1")
        config = {"configurable": {"thread_id": "s-empty"}}
        result = await shim.aget_tuple(config)
        assert result is None

    @pytest.mark.asyncio
    async def test_aput_writes_row_with_langgraph_checkpoint_key(self):
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-1", "a-1")
        config = {"configurable": {"thread_id": "s-1"}}
        cp = _make_checkpoint_dict(messages=[{"role": "user", "content": "hi"}])

        await shim.aput(config, cp, {"step": 1}, {})

        # Verify the row exists in the store
        rows = await store.list("s-1")
        assert len(rows) == 1
        assert "langgraph_checkpoint" in rows[0].tool_state

    @pytest.mark.asyncio
    async def test_aput_then_aget_tuple_round_trips(self):
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-rt", "a-1")
        config = {"configurable": {"thread_id": "s-rt"}}
        original_cp = _make_checkpoint_dict(
            messages=[{"role": "user", "content": "hello"}],
            seq_id="42",
        )

        # M20: pass new_versions matching channel_versions so the
        # messages channel is persisted as a blob and rebuilt by
        # aget_tuple. (Real LangGraph always populates new_versions for
        # any channel that changed this step.)
        await shim.aput(config, original_cp, {"step": 1}, {"messages": "1"})
        result = await shim.aget_tuple(config)

        assert result is not None
        assert isinstance(result, CheckpointTuple)
        # The checkpoint id should round-trip
        assert result.checkpoint["id"] == "42"
        # Messages should be preserved in channel_values (rebuilt from blobs)
        messages = result.checkpoint["channel_values"]["messages"]
        assert len(messages) == 1
        assert messages[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_alist_yields_newest_first(self):
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-list", "a-1")
        config = {"configurable": {"thread_id": "s-list"}}

        # Insert 3 checkpoints in order. Use seq_id matching the
        # internal sequence (1, 2, 3) so the checkpoint_id returned in
        # each tuple's config (which is now the langgraph id stashed
        # by aput, NOT str(cp.sequence)) lines up with insertion order.
        for i in range(3):
            cp = _make_checkpoint_dict(seq_id=str(i + 1))
            await shim.aput(config, cp, {"step": i}, {})

        # alist should yield newest-first (highest sequence first)
        results = []
        async for tup in shim.alist(config):
            results.append(tup)

        assert len(results) == 3
        # Newest first: sequence 3, 2, 1
        seqs = [tup.config["configurable"]["checkpoint_id"] for tup in results]
        assert seqs == ["3", "2", "1"]

    @pytest.mark.asyncio
    async def test_alist_respects_limit(self):
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-lim", "a-1")
        config = {"configurable": {"thread_id": "s-lim"}}

        for i in range(5):
            cp = _make_checkpoint_dict(seq_id=str(i + 1))
            await shim.aput(config, cp, {"step": i}, {})

        results = []
        async for tup in shim.alist(config, limit=2):
            results.append(tup)

        assert len(results) == 2
        # Should be the 2 newest (sequences 5 and 4)
        seqs = [tup.config["configurable"]["checkpoint_id"] for tup in results]
        assert seqs == ["5", "4"]

    @pytest.mark.asyncio
    async def test_adelete_thread_removes_all_rows(self):
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-del", "a-1")
        config = {"configurable": {"thread_id": "s-del"}}

        for i in range(3):
            cp = _make_checkpoint_dict()
            await shim.aput(config, cp, {"step": i}, {})

        # Verify rows exist
        rows_before = await store.list("s-del")
        assert len(rows_before) == 3

        await shim.adelete_thread("s-del")

        rows_after = await store.list("s-del")
        assert len(rows_after) == 0

    @pytest.mark.asyncio
    async def test_aget_tuple_handles_legacy_checkpoint_without_langgraph_payload(self):
        """Legacy Phase 3 checkpoints have no langgraph_checkpoint key —
        the shim should synthesize an empty LangGraph checkpoint."""
        store = SQLiteCheckpointStore()
        # Save a legacy-style checkpoint directly (no langgraph_checkpoint)
        legacy_cp = Checkpoint(
            session_id="s-legacy",
            sequence=1,
            messages=[{"role": "user", "content": "old"}],
            tool_state={"some_other_key": "value"},  # no langgraph_checkpoint
            agent_id="a-1",
        )
        await store.save(legacy_cp)

        shim = LangGraphCheckpointShim(store, "s-legacy", "a-1")
        config = {"configurable": {"thread_id": "s-legacy"}}
        result = await shim.aget_tuple(config)

        assert result is not None
        # Should synthesize a valid (empty) LangGraph checkpoint
        assert result.checkpoint["v"] == 1
        assert result.checkpoint["channel_values"]["messages"] == []

    def test_sync_methods_raise_not_implemented(self):
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-sync", "a-1")
        config = {"configurable": {"thread_id": "s-sync"}}

        with pytest.raises(NotImplementedError):
            shim.get_tuple(config)
        with pytest.raises(NotImplementedError):
            shim.put(config, {}, {}, {})
        with pytest.raises(NotImplementedError):
            shim.put_writes(config, [], "t-1")
        with pytest.raises(NotImplementedError):
            shim.delete_thread("s-sync")

    @pytest.mark.asyncio
    async def test_aput_writes_plain_messages_column(self):
        """The messages column should contain human-readable dicts
        for backward compat with Phase 3 debug tooling."""
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-msg", "a-1")
        config = {"configurable": {"thread_id": "s-msg"}}
        cp = _make_checkpoint_dict(
            messages=[{"role": "user", "content": "hello world"}]
        )

        await shim.aput(config, cp, {}, {})

        rows = await store.list("s-msg")
        assert len(rows) == 1
        # messages column should have the plain dict
        assert rows[0].messages == [{"role": "user", "content": "hello world"}]

    @pytest.mark.asyncio
    async def test_aput_writes_empty_messages_when_channel_values_empty(self):
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-empty-msg", "a-1")
        config = {"configurable": {"thread_id": "s-empty-msg"}}
        # Checkpoint with no messages in channel_values
        cp = _make_checkpoint_dict(messages=[])

        await shim.aput(config, cp, {}, {})

        rows = await store.list("s-empty-msg")
        assert len(rows) == 1
        assert rows[0].messages == []

    @pytest.mark.asyncio
    async def test_aput_reconstructs_full_messages_from_deltas(self):
        """Wave 2 regression: the messages column must hold the FULL
        conversation, not just the per-step delta.

        LangGraph writes one checkpoint per graph step; the ``messages``
        channel is a DeltaChannel, so each step's ``channel_values`` only
        carries the newly appended message(s). The shim must walk the
        blobs table and concatenate the deltas to recover the complete
        message history (this is what previously produced "0 messages")."""
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-delta", "a-1")
        config = {"configurable": {"thread_id": "s-delta"}}

        # Step 1: user message (version 1)
        await shim.aput(
            config,
            _make_checkpoint_dict(
                messages=[{"role": "user", "content": "hi"}],
                seq_id="1",
                channel_versions={"messages": "1"},
            ),
            {},
            {"messages": "1"},
        )
        # Step 2: assistant reply (version 2) — only the new message in channel_values
        await shim.aput(
            config,
            _make_checkpoint_dict(
                messages=[{"role": "assistant", "content": "hello"}],
                seq_id="2",
                channel_versions={"messages": "2"},
            ),
            {},
            {"messages": "2"},
        )

        rows = await store.list("s-delta")
        assert len(rows) == 2
        # The latest checkpoint must contain BOTH messages.
        latest = rows[-1]
        assert latest.messages == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        # reconstruct_checkpoint_messages must agree (covers legacy rows
        # whose messages column may be empty).
        rebuilt = await shim.reconstruct_checkpoint_messages(latest)
        assert rebuilt == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

    @pytest.mark.asyncio
    async def test_aput_reconstructs_messages_from_writes(self):
        """Wave 2 regression: the authoritative full conversation lives in
        the per-step ``messages`` writes (``checkpoint_writes``), NOT in the
        checkpoint's ``channel_values['messages']`` (which LangGraph 1.2+
        leaves empty for a DeltaChannel). aput must reconstruct the FULL
        list from those writes and store it in the ``messages`` column.

        Mirrors the real langgraph flow: ``aput_writes`` (messages wrapped
        as a single-element list) is called before each ``aput``.
        """
        from langchain_core.messages import AIMessage, HumanMessage

        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-w", "a-1")
        config = {"configurable": {"thread_id": "s-w"}}

        # Step 1: input user message written, then checkpoint.
        await shim.aput_writes(
            config, [("messages", [HumanMessage(content="Hi there")])], task_id="t1"
        )
        await shim.aput(
            config,
            _make_checkpoint_dict(
                messages=[{"role": "user", "content": "Hi there"}],
                seq_id="1",
                channel_versions={"__start__": "1"},
            ),
            {},
            {"__start__": "1"},
        )
        # Step 2: assistant reply written, then checkpoint. channel_values
        # carries no messages (mirrors real DeltaChannel behaviour).
        await shim.aput_writes(
            config,
            [("messages", [AIMessage(content="Hello! I am a test agent.")])],
            task_id="t2",
        )
        await shim.aput(
            config,
            _make_checkpoint_dict(messages=[], seq_id="2", channel_versions={"messages": "2"}),
            {},
            {"messages": "2"},
        )

        rows = await store.list("s-w")
        assert len(rows) == 2
        latest = rows[-1]
        assert latest.messages == [
            {"role": "user", "content": "Hi there"},
            {"role": "assistant", "content": "Hello! I am a test agent."},
        ]
        rebuilt = await shim.reconstruct_checkpoint_messages(latest)
        assert rebuilt == latest.messages

    @pytest.mark.asyncio
    async def test_reconstruct_checkpoint_messages_falls_back_to_writes(self):
        """When a stored checkpoint row has an empty ``messages`` column
        (the old bug), reconstruct_checkpoint_messages must recover the
        full conversation from the ``checkpoint_writes`` table instead of
        returning [] — this is what list_checkpoints / restore rely on.
        """
        from langchain_core.messages import HumanMessage

        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-fb", "a-1")
        config = {"configurable": {"thread_id": "s-fb"}}

        # A checkpoint row with EMPTY messages (as the old bug produced).
        empty_cp = Checkpoint(
            session_id="s-fb", sequence=1, messages=[], tool_state={}, agent_id="a-1"
        )
        await store.save(empty_cp)
        # But the per-step message writes exist.
        await shim.aput_writes(
            config, [("messages", [HumanMessage(content="recovered")])], task_id="t1"
        )

        rebuilt = await shim.reconstruct_checkpoint_messages(empty_cp)
        assert rebuilt == [{"role": "user", "content": "recovered"}]

    # ── Phase 4c: pending writes durability ──

    @pytest.mark.asyncio
    async def test_aput_writes_persists_rows_to_checkpoint_writes_table(self):
        """Phase 4c: aput_writes must persist writes so a crash before
        the next aput doesn't lose them."""
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-pw-1", "a-1")
        config = {"configurable": {"thread_id": "s-pw-1"}}

        writes = [
            ("messages", {"role": "assistant", "content": "hi"}),
            ("__interrupt__", {"value": "paused"}),
        ]
        await shim.aput_writes(config, writes, task_id="task-1", task_path="")

        # Verify rows exist in checkpoint_writes table.
        from src.infra.db.engine import async_session

        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT channel FROM checkpoint_writes "
                    "WHERE session_id = :sid"
                ),
                {"sid": "s-pw-1"},
            )
            channels = {row.channel for row in result.fetchall()}
        assert channels == {"messages", "__interrupt__"}

    @pytest.mark.asyncio
    async def test_aput_writes_is_idempotent_for_same_task_and_channel(self):
        """Duplicate (task_id, channel) pairs upsert, not insert."""
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-pw-2", "a-1")
        config = {"configurable": {"thread_id": "s-pw-2"}}

        await shim.aput_writes(
            config, [("messages", {"v": 1})], task_id="task-1"
        )
        await shim.aput_writes(
            config, [("messages", {"v": 2})], task_id="task-1"
        )

        from src.infra.db.engine import async_session

        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT COUNT(*) AS n FROM checkpoint_writes "
                    "WHERE session_id = :sid AND task_id = :tid"
                ),
                {"sid": "s-pw-2", "tid": "task-1"},
            )
            count = result.fetchone().n
        assert count == 1

    @pytest.mark.asyncio
    async def test_aput_writes_with_empty_writes_is_noop(self):
        """Calling aput_writes with an empty writes list does nothing."""
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-pw-3", "a-1")
        config = {"configurable": {"thread_id": "s-pw-3"}}

        await shim.aput_writes(config, [], task_id="task-x")

        from src.infra.db.engine import async_session

        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT COUNT(*) AS n FROM checkpoint_writes "
                    "WHERE session_id = :sid"
                ),
                {"sid": "s-pw-3"},
            )
            assert result.fetchone().n == 0

    @pytest.mark.asyncio
    async def test_aget_tuple_loads_pending_writes(self):
        """aget_tuple must surface pending writes from checkpoint_writes.

        M20: writes are scoped to a checkpoint_id, so aput_writes must
        be called with the config returned by aput (which carries the
        checkpoint_id) for aget_tuple to find them.
        """
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-pw-4", "a-1")
        config = {"configurable": {"thread_id": "s-pw-4"}}

        # First persist a checkpoint so aget_tuple has something to load.
        cp = _make_checkpoint_dict(messages=[{"role": "user", "content": "x"}])
        cp_config = await shim.aput(config, cp, {}, {"messages": "1"})

        # Then persist writes using the config returned by aput (which
        # carries checkpoint_id).
        await shim.aput_writes(
            cp_config,
            [("messages", {"role": "assistant", "content": "pending"})],
            task_id="task-1",
        )

        tuple_ = await shim.aget_tuple(config)
        assert tuple_ is not None
        assert len(tuple_.pending_writes) == 1
        # pending_writes are (task_id, channel, value) triples.
        assert tuple_.pending_writes[0][0] == "task-1"
        assert tuple_.pending_writes[0][1] == "messages"

    @pytest.mark.asyncio
    async def test_adelete_thread_clears_pending_writes(self):
        """adelete_thread must also clear checkpoint_writes rows."""
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-pw-5", "a-1")
        config = {"configurable": {"thread_id": "s-pw-5"}}

        cp = _make_checkpoint_dict(messages=[])
        cp_config = await shim.aput(config, cp, {}, {})
        await shim.aput_writes(
            cp_config, [("messages", {"v": 1})], task_id="task-1"
        )

        await shim.adelete_thread("s-pw-5")

        from src.infra.db.engine import async_session

        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT COUNT(*) AS n FROM checkpoint_writes "
                    "WHERE session_id = :sid"
                ),
                {"sid": "s-pw-5"},
            )
            assert result.fetchone().n == 0

    @pytest.mark.asyncio
    async def test_pending_writes_round_trip_complex_values(self):
        """Round-trip a dict with nested structure through aput_writes
        and aget_tuple."""
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-pw-6", "a-1")
        config = {"configurable": {"thread_id": "s-pw-6"}}

        cp = _make_checkpoint_dict(messages=[])
        cp_config = await shim.aput(config, cp, {}, {})

        nested = {"k": {"nested": [1, 2, {"three": 3}]}, "flag": True}
        await shim.aput_writes(
            cp_config, [("state", nested)], task_id="task-1"
        )

        tuple_ = await shim.aget_tuple(config)
        assert tuple_ is not None
        assert len(tuple_.pending_writes) == 1
        # pending_writes are (task_id, channel, value) triples — see
        # langgraph.checkpoint.base.PendingWrite.
        task_id, channel, value = tuple_.pending_writes[0]
        assert task_id == "task-1"
        assert channel == "state"
        assert value == nested

    @pytest.mark.asyncio
    async def test_load_writes_returns_pending_write_triples(self):
        """Regression: _load_writes must return ``(task_id, channel,
        value)`` triples — not ``(channel, value)`` pairs.

        langgraph 1.2+ unpacks each pending write as
        ``for tid, k, v in saved.pending_writes``. Returning 2-tuples
        caused ``ValueError: not enough values to unpack (expected 3,
        got 2)`` on the second turn of any checkpointed conversation
        (see langgraph/pregel/_loop.py __aenter__).
        """
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-pw-7", "a-1")
        config = {"configurable": {"thread_id": "s-pw-7"}}

        # Persist two writes from two different tasks to verify
        # task_id is correctly threaded through.
        await shim.aput_writes(
            config,
            [("messages", {"role": "assistant", "content": "a"}),
             ("state", {"k": 1})],
            task_id="task-A",
        )
        await shim.aput_writes(
            config,
            [("messages", {"role": "assistant", "content": "b"})],
            task_id="task-B",
        )

        writes = await shim._load_writes("s-pw-7")
        assert len(writes) == 3
        # Every entry must be a 3-tuple matching PendingWrite.
        for w in writes:
            assert len(w) == 3, f"expected 3-tuple, got {w!r}"
            assert isinstance(w[0], str)  # task_id
            assert isinstance(w[1], str)  # channel
        # Sanity-check task_ids and channels.
        task_ids = {w[0] for w in writes}
        channels = {w[1] for w in writes}
        assert task_ids == {"task-A", "task-B"}
        assert channels == {"messages", "state"}

    @pytest.mark.asyncio
    async def test_reconstruct_checkpoint_messages_returns_per_checkpoint_snapshot(self):
        """REGRESSION (2026-07-11): each checkpoint must return its OWN
        message snapshot, not the full session's concatenated writes.

        The restore UI (list_checkpoints) groups checkpoints by user-message
        count. If ``reconstruct_checkpoint_messages`` returned the same
        full-session list for every checkpoint (e.g. by always reading
        ``checkpoint_writes``), then every checkpoint would report the max
        user count and the grouping would collapse them into a single
        restore point — the bug where "only the latest checkpoint history
        remains".

        Here the ``checkpoint_writes`` table holds the FULL session
        (3 messages), but each checkpoint row stores a distinct snapshot.
        The method must return the per-row snapshot, not the writes.
        """
        from langchain_core.messages import AIMessage, HumanMessage

        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-percp", "a-1")
        config = {"configurable": {"thread_id": "s-percp"}}

        # Two checkpoints with DISTINCT snapshots.
        cp1 = Checkpoint(
            session_id="s-percp",
            sequence=1,
            messages=[{"role": "user", "content": "hi"}],
            tool_state={},
            agent_id="a-1",
        )
        cp2 = Checkpoint(
            session_id="s-percp",
            sequence=2,
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
            tool_state={},
            agent_id="a-1",
        )
        await store.save(cp1)
        await store.save(cp2)

        # The session's checkpoint_writes hold the FULL conversation (longer
        # than cp1's snapshot). If the buggy "always read writes" path ran,
        # BOTH checkpoints would return this full list.
        await shim.aput_writes(
            config, [("messages", [HumanMessage(content="hi")])], task_id="t1"
        )
        await shim.aput_writes(
            config, [("messages", [AIMessage(content="hello")])], task_id="t2"
        )
        await shim.aput_writes(
            config, [("messages", [HumanMessage(content="third")])], task_id="t3"
        )

        rebuilt1 = await shim.reconstruct_checkpoint_messages(cp1)
        rebuilt2 = await shim.reconstruct_checkpoint_messages(cp2)

        # Each checkpoint returns ONLY its own snapshot.
        assert rebuilt1 == [{"role": "user", "content": "hi"}], (
            "cp1 must return its own 1-message snapshot, not the full session"
        )
        assert rebuilt2 == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ], "cp2 must return its own 2-message snapshot, not the full session"
        # And they must differ (prove they are not the same collapsed list).
        assert rebuilt1 != rebuilt2


# ── Checkpoint message fidelity (tool_calls / tool_call_id) ───────────────
#
# These guard against the "Messages with role 'tool' must be a response to a
# preceding message with 'tool_calls'" BadRequestError. The shim must
# preserve tool-call metadata when round-tripping messages through the
# plain-dict format used by the ``messages`` column and the restore UI.


class TestCheckpointMessageFidelity:
    def _shim(self) -> LangGraphCheckpointShim:
        store = SQLiteCheckpointStore()
        return LangGraphCheckpointShim(store, "s-fid", "a-1")

    def test_extract_plain_preserves_tool_calls_on_ai_message(self):
        """_extract_plain_messages must keep AIMessage.tool_calls so the
        restored conversation still has a preceding tool_call for its
        ToolMessage."""
        from langchain_core.messages import AIMessage

        shim = self._shim()
        ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "echo", "args": {"text": "x"}, "id": "call_1", "type": "tool_call"}
            ],
        )
        out = shim._extract_plain_messages([ai])
        assert len(out) == 1
        assert out[0]["role"] == "assistant"
        # Dict tool_calls are preserved verbatim (including "type").
        assert out[0]["tool_calls"] == [
            {"name": "echo", "args": {"text": "x"}, "id": "call_1", "type": "tool_call"}
        ]

    def test_extract_plain_preserves_tool_call_id_on_tool_message(self):
        """_extract_plain_messages must keep ToolMessage.tool_call_id so the
        tool message can be matched to its originating assistant tool_call."""
        from langchain_core.messages import ToolMessage

        shim = self._shim()
        tool = ToolMessage(content="the result", tool_call_id="call_1")
        out = shim._extract_plain_messages([tool])
        assert len(out) == 1
        assert out[0]["role"] == "tool"
        assert out[0]["tool_call_id"] == "call_1"

    def test_extract_plain_omits_tool_keys_when_absent(self):
        """When there are no tool calls, no spurious tool_calls/tool_call_id
        keys are added (keeps the plain format minimal and stable)."""
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        shim = self._shim()
        out = shim._extract_plain_messages([
            HumanMessage(content="hi"),
            AIMessage(content="hello"),
            ToolMessage(content="r", tool_call_id=""),  # empty id -> omitted
        ])
        assert "tool_calls" not in out[0]
        assert "tool_calls" not in out[1]
        assert "tool_call_id" not in out[2]

    def test_extract_plain_flattens_nested_lists(self):
        """LangGraph writes messages wrapped as a single-element list; the
        extractor must flatten nested lists without duplicating."""
        from langchain_core.messages import AIMessage

        shim = self._shim()
        ai = AIMessage(
            content="",
            tool_calls=[{"name": "echo", "args": {}, "id": "c1"}],
        )
        # Wrapped like a real LangGraph write: [ [AIMessage] ]
        out = shim._extract_plain_messages([[ai]])
        assert len(out) == 1
        assert out[0]["tool_calls"][0]["id"] == "c1"

    def test_to_langchain_message_restores_tool_calls(self):
        """_to_langchain_message must rebuild AIMessage.tool_calls from a
        plain dict — this is the exact path that was previously dropping
        tool_calls and causing the provider BadRequestError."""
        shim = self._shim()
        ai = shim._to_langchain_message({
            "role": "assistant",
            "content": "",
            "tool_calls": [{"name": "echo", "args": {"text": "x"}, "id": "call_1"}],
        })
        from langchain_core.messages import AIMessage

        assert isinstance(ai, AIMessage)
        assert len(ai.tool_calls) == 1
        assert ai.tool_calls[0]["id"] == "call_1"
        assert ai.tool_calls[0]["name"] == "echo"

    def test_to_langchain_message_builds_tool_message_with_id(self):
        """_to_langchain_message must rebuild ToolMessage with tool_call_id
        from a plain dict."""
        shim = self._shim()
        tool = shim._to_langchain_message({
            "role": "tool",
            "content": "the result",
            "tool_call_id": "call_1",
        })
        from langchain_core.messages import ToolMessage

        assert isinstance(tool, ToolMessage)
        assert tool.tool_call_id == "call_1"

    def test_round_trip_ai_tool_calls_fidelity(self):
        """Full fidelity: AIMessage(tool_calls) → plain dict →
        AIMessage(tool_calls)."""
        from langchain_core.messages import AIMessage

        shim = self._shim()
        original = AIMessage(
            content="",
            tool_calls=[{"name": "echo", "args": {"text": "x"}, "id": "call_1"}],
        )
        plain = shim._extract_plain_messages([original])
        restored = shim._to_langchain_message(plain[0])

        assert isinstance(restored, AIMessage)
        assert restored.tool_calls == original.tool_calls

    def test_round_trip_tool_message_fidelity(self):
        """Full fidelity: ToolMessage(tool_call_id) → plain dict →
        ToolMessage(tool_call_id)."""
        from langchain_core.messages import ToolMessage

        shim = self._shim()
        original = ToolMessage(content="result", tool_call_id="call_1")
        plain = shim._extract_plain_messages([original])
        restored = shim._to_langchain_message(plain[0])

        assert isinstance(restored, ToolMessage)
        assert restored.tool_call_id == "call_1"

    def test_restored_conversation_is_provider_valid(self):
        """REGRESSION (BadRequestError): after a manual checkpoint restore,
        the reconstructed message sequence must be valid for the LLM
        provider — i.e. a ToolMessage is preceded by an AIMessage that
        carries tool_calls.

        This mirrors the stored plain-dict list (with tool metadata now
        preserved) being converted back to LangChain messages before the
        next turn.
        """
        shim = self._shim()
        stored = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"name": "echo", "args": {"text": "x"}, "id": "call_1"}
                ],
            },
            {"role": "tool", "content": "result", "tool_call_id": "call_1"},
            {"role": "assistant", "content": "done"},
        ]
        msgs = shim._to_langchain_messages(stored)

        from langchain_core.messages import AIMessage, ToolMessage

        assert isinstance(msgs[1], AIMessage)
        assert msgs[1].tool_calls[0]["id"] == "call_1"
        assert isinstance(msgs[2], ToolMessage)
        assert msgs[2].tool_call_id == "call_1"
        # The tool message has a preceding assistant message with tool_calls.
        assert msgs[2].tool_call_id == msgs[1].tool_calls[0]["id"]


# ── LangChainToolShim ───────────────────────────────────────────────────


class TestLangChainToolShim:
    @pytest.mark.asyncio
    async def test_arun_delegates_to_tool_engine(self):
        tool_def = _make_tool_def(name="echo")
        engine = _FakeToolEngine(
            result=ToolResult(name="echo", output="echoed!")
        )
        ctx = _FakeCtx(engine)

        shim = LangChainToolShim(tool_def, ctx)
        result = await shim._arun(text="hello")

        assert result == "echoed!"
        assert len(engine.calls) == 1
        assert engine.calls[0][0] == "echo"
        assert engine.calls[0][1] == {"text": "hello"}

    @pytest.mark.asyncio
    async def test_arun_returns_error_string_on_tool_error(self):
        tool_def = _make_tool_def(name="boom")
        engine = _FakeToolEngine(error="ToolPermissionError: not allowed")
        ctx = _FakeCtx(engine)

        shim = LangChainToolShim(tool_def, ctx)
        result = await shim._arun(text="x")

        # ToolError is caught and returned as ERROR: string so deepagents
        # sees a tool failure rather than crashing the agent loop.
        assert result.startswith("ERROR: ")
        assert "ToolError" in result
        assert "ToolPermissionError: not allowed" in result

    @pytest.mark.asyncio
    async def test_arun_returns_error_string_when_result_has_error(self):
        """If the engine returns a ToolResult with error set, the shim
        returns an ERROR: string so deepagents sees it as a tool failure."""
        tool_def = _make_tool_def(name="fail")
        engine = _FakeToolEngine(
            result=ToolResult(name="fail", output="", error="something went wrong")
        )
        ctx = _FakeCtx(engine)

        shim = LangChainToolShim(tool_def, ctx)
        result = await shim._arun()

        assert result == "ERROR: something went wrong"

    def test_run_raises_not_implemented(self):
        tool_def = _make_tool_def()
        engine = _FakeToolEngine()
        ctx = _FakeCtx(engine)

        shim = LangChainToolShim(tool_def, ctx)
        with pytest.raises(NotImplementedError):
            shim._run()

    @pytest.mark.asyncio
    async def test_shim_with_empty_input_schema(self):
        """Shim should work with no args_schema (permissive fallback)."""
        tool_def = _make_tool_def(input_schema={})
        engine = _FakeToolEngine(result=ToolResult(name="x", output="ok"))
        ctx = _FakeCtx(engine)

        shim = LangChainToolShim(tool_def, ctx)
        # _arun should still work even without args_schema
        result = await shim._arun()
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_shim_with_none_input_schema(self):
        """Shim should work when input_schema is None."""
        tool_def = _make_tool_def(input_schema=None)
        engine = _FakeToolEngine(result=ToolResult(name="x", output="ok"))
        ctx = _FakeCtx(engine)

        shim = LangChainToolShim(tool_def, ctx)
        result = await shim._arun()
        assert result == "ok"

    def test_shim_name_and_description_set_from_tool_def(self):
        tool_def = _make_tool_def(name="my_tool")
        engine = _FakeToolEngine()
        ctx = _FakeCtx(engine)

        shim = LangChainToolShim(tool_def, ctx)
        assert shim.name == "my_tool"
        assert "my_tool" in shim.description

    def test_build_args_schema_returns_none_for_empty_props(self):
        schema = LangChainToolShim._build_args_schema({"properties": {}})
        assert schema is None

    def test_build_args_schema_returns_none_for_non_dict(self):
        schema = LangChainToolShim._build_args_schema(None)
        assert schema is None

    def test_build_args_schema_returns_model_for_valid_schema(self):
        schema = LangChainToolShim._build_args_schema(
            {"properties": {"x": {"type": "string"}, "y": {"type": "int"}}}
        )
        assert schema is not None
        # Should have x and y fields
        assert "x" in schema.model_fields
        assert "y" in schema.model_fields
