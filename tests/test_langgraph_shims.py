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
from src.runtime.harness.langgraph_shims import (
    LangChainToolShim,
    LangGraphCheckpointShim,
)
from src.runtime.harness.tool_engine import ToolDefinition, ToolResult


# ── DB setup ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def setup_db():
    """Create checkpoints table before each test, drop after.

    The ``checkpoints`` table is a raw-SQL table (M13 migration) so we
    create it here explicitly (mirrors test_checkpoint.py).
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


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_checkpoint_dict(
    *,
    messages: list[dict] | None = None,
    seq_id: str = "1",
) -> dict:
    """Build a minimal LangGraph Checkpoint dict for testing."""
    return {
        "v": 1,
        "id": seq_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel_values": {"messages": messages or []},
        "channel_versions": {},
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

        await shim.aput(config, original_cp, {"step": 1}, {})
        result = await shim.aget_tuple(config)

        assert result is not None
        assert isinstance(result, CheckpointTuple)
        # The checkpoint id should round-trip
        assert result.checkpoint["id"] == "42"
        # Messages should be preserved in channel_values
        messages = result.checkpoint["channel_values"]["messages"]
        assert len(messages) == 1
        assert messages[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_alist_yields_newest_first(self):
        store = SQLiteCheckpointStore()
        shim = LangGraphCheckpointShim(store, "s-list", "a-1")
        config = {"configurable": {"thread_id": "s-list"}}

        # Insert 3 checkpoints in order
        for i in range(3):
            cp = _make_checkpoint_dict(seq_id=str(i))
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
            cp = _make_checkpoint_dict(seq_id=str(i))
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
