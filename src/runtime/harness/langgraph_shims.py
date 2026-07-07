"""Phase 4: Bridges between our harness and LangGraph/LangChain.

Two shims live here:

- ``LangGraphCheckpointShim``: adapts our ``SQLiteCheckpointStore``
  (Phase 3a-P1) to LangGraph's ``BaseCheckpointSaver`` abstract class.
  Per spec D2: single source of truth — writes go to the same
  ``checkpoints`` table (M13) that ``HarnessRuntime.checkpoint`` uses.

- ``LangChainToolShim``: wraps a harness ``ToolDefinition`` as a
  LangChain ``BaseTool`` whose ``_arun()`` delegates to
  ``ctx.tool_engine.execute()``. Per spec D6: tools are bridged, not
  re-implemented, so the Phase 3a pipeline (whitelist → sandbox →
  guardrail → hook → handler) runs for every deepagents tool call.

Imports of ``langgraph.*`` / ``langchain.*`` are confined to this file
(per spec §6.2 import discipline) so the ``direct_llm`` path stays
zero-dependency.
"""
from __future__ import annotations

import base64
import json
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional, Sequence

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    DeltaChannelHistory,
    PendingWrite,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import text

if TYPE_CHECKING:
    from src.runtime.harness.checkpoint import CheckpointStore
    from src.runtime.harness.context import HarnessContext
    from src.runtime.harness.tool_engine import ToolDefinition


logger = logging.getLogger(__name__)


# ── LangGraphCheckpointShim ──────────────────────────────────────────────


class LangGraphCheckpointShim(BaseCheckpointSaver):
    """Adapts our ``SQLiteCheckpointStore`` to LangGraph's checkpointer API.

    Per spec D2: single source of truth. Writes go to the same
    ``checkpoints`` table (M13) that ``HarnessRuntime.checkpoint`` uses.
    Reads load the latest LangGraph checkpoint tuple for the thread
    (= our session_id).

    The shim is **per-run**: constructed in ``DeepAgentsAdapter.run()``
    with the current session_id. LangGraph calls ``aput``/``aput_writes``
    during execution; ``HarnessRuntime.checkpoint.commit()`` at run end
    is a no-op when this shim is active (the data is already durable).

    Serialization strategy: the full LangGraph ``Checkpoint`` object is
    msgpack-encoded via ``JsonPlusSerializer.dumps_typed`` and stored in
    our ``tool_state`` column under the ``langgraph_checkpoint`` key.
    The ``messages`` column is populated from
    ``checkpoint["channel_values"]["messages"]`` for backward compat
    with Phase 3 debug tooling. The ``metadata`` column carries
    LangGraph's ``metadata`` dict unchanged.
    """

    serde = JsonPlusSerializer()

    def __init__(self, store: "CheckpointStore", session_id: str, agent_id: str) -> None:
        # ``store`` is our SQLiteCheckpointStore (Phase 3a-P1).
        self._store = store
        self._session_id = session_id
        self._agent_id = agent_id

    # ── Async API (deepagents only uses async) ──

    async def aget_tuple(
        self, config: RunnableConfig
    ) -> Optional[CheckpointTuple]:
        """Return the latest checkpoint tuple for the thread.

        Phase 4c: also loads pending writes from the
        ``checkpoint_writes`` table so a crash between ``aput_writes``
        and the next ``aput`` no longer loses intermediate state.
        """
        thread_id = config["configurable"]["thread_id"]
        cp = await self._store.load(thread_id)
        if cp is None:
            return None
        # Recover the langgraph checkpoint_id stashed by aput. We must
        # use this (not our internal sequence number) to scope
        # _load_writes — aput_writes stores writes keyed by langgraph's
        # checkpoint UUID, so filtering by str(seq) would miss them and
        # the second turn of conversation would lose all pending writes
        # (including the user's message and the assistant's reply).
        tool_state = cp.tool_state if hasattr(cp, "tool_state") else {}
        encoded_state = (tool_state or {}).get("langgraph_checkpoint") or {}
        lg_checkpoint_id = ""
        if isinstance(encoded_state, dict):
            lg_checkpoint_id = encoded_state.get("checkpoint_id", "") or ""
        tuple_ = self._decode_tuple(
            cp, await self._load_writes(thread_id, lg_checkpoint_id)
        )
        # M20: rebuild channel_values from the blobs table. LangGraph 1.2+
        # stores each channel's value separately (keyed by version) and
        # the checkpoint dict we persisted only carries the channels that
        # changed in the *last* step — without this rebuild, messages
        # (and other long-lived channels) come back empty on resume.
        #
        # We ALWAYS set channel_values here (even when empty) because
        # aput pops it before serializing the checkpoint envelope —
        # LangGraph's internal code accesses checkpoint["channel_values"]
        # directly (not via .get) and raises KeyError if it's missing.
        rebuilt_cv = await self._load_channel_values(
            thread_id, tuple_.checkpoint.get("channel_versions", {}) or {}
        )
        merged = dict(tuple_.checkpoint.get("channel_values", {}) or {})
        merged.update(rebuilt_cv)
        tuple_ = tuple_._replace(
            checkpoint={**tuple_.checkpoint, "channel_values": merged}
        )
        return tuple_

    async def aget_delta_channel_history(
        self, *, config: RunnableConfig, channels: Sequence[str]
    ) -> Mapping[str, DeltaChannelHistory]:
        """Walk the checkpoint chain to recover DeltaChannel values.

        deepagents uses ``DeltaChannel`` for the ``messages`` channel
        (``snapshot_frequency=50``), so messages are stored as deltas
        (pending writes) across multiple checkpoints rather than as
        full snapshots in every checkpoint blob. Recovery requires
        walking the parent chain newest→oldest, collecting pending
        writes for the requested channels until a snapshot (non-empty
        blob) is found.

        The default implementation in ``BaseCheckpointSaver`` relies on
        ``parent_config`` being set on each checkpoint tuple. We don't
        track parent IDs (our store uses a flat sequence), so we
        override this method to walk our store's checkpoint list
        directly.
        """
        if not channels:
            return {}
        thread_id = config["configurable"]["thread_id"]
        # Load all checkpoints oldest-first, then reverse for newest-first.
        all_cps = await self._store.list(thread_id)
        reversed_cps = list(reversed(all_cps))

        collected_by_ch: dict[str, list[PendingWrite]] = {
            c: [] for c in channels
        }
        seed_by_ch: dict[str, Any] = {}
        remaining: set[str] = set(channels)

        # Skip the first (target) checkpoint — its writes are already
        # surfaced via the tuple's pending_writes, not delta history.
        for cp in reversed_cps[1:]:
            if not remaining:
                break
            # Recover the langgraph checkpoint_id stashed by aput.
            tool_state = cp.tool_state if hasattr(cp, "tool_state") else {}
            encoded = (tool_state or {}).get("langgraph_checkpoint") or {}
            lg_checkpoint_id = ""
            if isinstance(encoded, dict):
                lg_checkpoint_id = encoded.get("checkpoint_id", "") or ""

            # Collect pending writes for this ancestor checkpoint.
            writes = await self._load_writes(thread_id, lg_checkpoint_id)
            for write in reversed(writes):
                ch = write[1]
                if ch in remaining:
                    collected_by_ch[ch].append(write)

            # Check if this checkpoint has a seed (non-empty blob) for
            # any remaining channel — terminates the walk per-channel.
            tuple_ = self._decode_tuple(cp)
            rebuilt_cv = await self._load_channel_values(
                thread_id, tuple_.checkpoint.get("channel_versions", {}) or {}
            )
            for ch in list(remaining):
                if ch in rebuilt_cv:
                    seed_by_ch[ch] = rebuilt_cv[ch]
                    remaining.discard(ch)

        result: dict[str, DeltaChannelHistory] = {}
        for ch in channels:
            entry: DeltaChannelHistory = {
                "writes": list(reversed(collected_by_ch[ch]))
            }
            if ch in seed_by_ch:
                entry["seed"] = seed_by_ch[ch]
            result[ch] = entry
        return result

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """Iterate checkpoints newest-first (LangGraph convention).

        ``filter`` is ignored (Phase 4 does not index metadata fields).
        """
        if config is None:
            return
        thread_id = config["configurable"]["thread_id"]
        checkpoints = await self._store.list(thread_id)
        # Our store returns oldest-first; LangGraph wants newest-first.
        reversed_cps = list(reversed(checkpoints))
        if before is not None:
            cutoff = before["configurable"].get("checkpoint_id")
            if cutoff is not None:
                reversed_cps = [
                    cp for cp in reversed_cps
                    if str(cp.sequence) < str(cutoff)
                ]
        if limit is not None:
            reversed_cps = reversed_cps[:limit]
        for cp in reversed_cps:
            yield self._decode_tuple(cp)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Persist a new checkpoint. Returns the updated config.

        LangGraph 1.2+ calls ``aput`` with a checkpoint whose
        ``channel_values`` only contains channels that changed in the
        *current* step — long-lived channels like ``messages`` are
        only present when they get a new version this step. To make
        resume work we mirror MemorySaver's approach: for every entry
        in ``new_versions`` we persist the channel's value (or an
        ``empty`` marker) to the ``checkpoint_blobs`` table keyed by
        ``(thread_id, channel, version)``. ``aget_tuple`` then rebuilds
        the full ``channel_values`` from ``channel_versions``.
        """
        thread_id = config["configurable"]["thread_id"]
        seq = await self._store.next_sequence(thread_id)
        # LangGraph's checkpoint["id"] is the canonical checkpoint ID
        # used by put_writes / get_tuple to scope pending writes. We
        # must return THIS id (not our internal sequence number) so
        # that aput_writes and _load_writes agree on the same key.
        checkpoint_id = checkpoint.get("id", "") if isinstance(checkpoint, dict) else ""
        # Pop channel_values before serializing the checkpoint envelope
        # (matches MemorySaver.put — the values live in blobs, not in
        # the checkpoint dict).
        checkpoint_copy = dict(checkpoint) if isinstance(checkpoint, dict) else {}
        channel_values = checkpoint_copy.pop("channel_values", {}) or {}
        type_str, payload_bytes = self.serde.dumps_typed(checkpoint_copy)
        encoded = {
            "type": type_str,
            "payload_b64": base64.b64encode(payload_bytes).decode("ascii"),
        }
        # Stash the langgraph checkpoint id in tool_state so aget_tuple
        # can recover it (our store only knows the sequence number).
        encoded["checkpoint_id"] = checkpoint_id
        # Human-readable messages column (debug tooling only).
        messages = channel_values.get("messages", [])
        plain_messages = self._extract_plain_messages(messages)
        # Persist the checkpoint envelope via our existing store.
        from src.runtime.harness.checkpoint import Checkpoint as CPCheckpoint

        cp_record = CPCheckpoint(
            session_id=thread_id,
            sequence=seq,
            messages=plain_messages,
            tool_state={"langgraph_checkpoint": encoded},
            agent_id=self._agent_id,
            metadata=metadata if isinstance(metadata, dict) else {},
        )
        await self._store.save(cp_record)
        # M20: persist per-channel value blobs for each channel that got
        # a new version in this step. ``channel_values`` may not contain
        # every channel in ``new_versions`` (langgraph only populates
        # the ones that changed this step); we store an ``empty`` marker
        # for the rest so ``_load_channel_values`` can skip them.
        await self._save_blobs(thread_id, channel_values, new_versions or {})
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def _save_blobs(
        self,
        thread_id: str,
        channel_values: dict,
        new_versions: dict,
    ) -> None:
        """Persist per-channel value blobs (M20).

        Mirrors MemorySaver.put's blob loop: for each channel in
        ``new_versions``, store its serialized value (or ``empty`` if
        the channel isn't in ``channel_values``). ``aget_tuple`` reads
        these back via ``_load_channel_values`` to rebuild the full
        ``channel_values`` dict from ``channel_versions``.
        """
        if not new_versions:
            return
        from src.infra.db.engine import async_session

        rows = []
        for channel, version in new_versions.items():
            if version is None:
                continue
            value = channel_values.get(channel)
            if value is not None:
                type_str, payload_bytes = self.serde.dumps_typed(value)
                payload_b64 = base64.b64encode(payload_bytes).decode("ascii")
                rows.append({
                    "channel": channel,
                    "version": str(version),
                    "type": type_str,
                    "payload": payload_b64,
                })
            else:
                # "empty" marker — _load_channel_values will skip it.
                rows.append({
                    "channel": channel,
                    "version": str(version),
                    "type": "empty",
                    "payload": "",
                })
        if not rows:
            return
        try:
            async with async_session() as db:
                for row in rows:
                    await db.execute(
                        text(
                            "INSERT OR REPLACE INTO checkpoint_blobs "
                            "(session_id, channel, version, type, payload) "
                            "VALUES (:sid, :ch, :ver, :typ, :pay)"
                        ),
                        {
                            "sid": thread_id,
                            "ch": row["channel"],
                            "ver": row["version"],
                            "typ": row["type"],
                            "pay": row["payload"],
                        },
                    )
                await db.commit()
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Failed to save checkpoint blobs: %s", exc)

    async def _load_channel_values(
        self,
        thread_id: str,
        channel_versions: dict,
    ) -> dict:
        """Rebuild ``channel_values`` from the blobs table (M20).

        For each ``(channel, version)`` in ``channel_versions``, load
        the blob and decode it. ``empty`` markers are skipped (the
        channel wasn't present at that version).
        """
        if not channel_versions:
            return {}
        from src.infra.db.engine import async_session

        # Build a (channel, version) → (type, payload) lookup.
        out: dict[str, Any] = {}
        try:
            async with async_session() as db:
                # SQLite param substitution doesn't support tuples, so
                # build a parameterized IN clause per row. For typical
                # channel counts (<20) this is fine.
                for channel, version in channel_versions.items():
                    if version is None:
                        continue
                    row = (
                        await db.execute(
                            text(
                                "SELECT type, payload FROM checkpoint_blobs "
                                "WHERE session_id = :sid AND channel = :ch "
                                "AND version = :ver"
                            ),
                            {
                                "sid": thread_id,
                                "ch": channel,
                                "ver": str(version),
                            },
                        )
                    ).first()
                    if row is None or row.type == "empty":
                        continue
                    try:
                        payload_bytes = base64.b64decode(row.payload)
                        out[channel] = self.serde.loads_typed((row.type, payload_bytes))
                    except Exception:
                        continue
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("Failed to load channel values: %s", exc)
        return out

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Persist intermediate task writes to ``checkpoint_writes``.

        Phase 4c: writes are now durable. Each ``(channel, value)`` pair
        is upserted (INSERT OR REPLACE) so duplicate task_ids don't
        create duplicate rows — LangGraph may call aput_writes multiple
        times for the same task as it streams intermediate state.

        M20: writes are now scoped to a specific ``checkpoint_id`` so
        ``aget_tuple`` can load only the pending writes that belong to
        the checkpoint being resumed. Without this scoping, writes from
        a later checkpoint leak into the pending_writes of an earlier
        one and LangGraph re-applies them out of order.
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = config["configurable"].get("checkpoint_id", "")
        if not writes:
            return
        from datetime import datetime, timezone

        from src.infra.db.engine import async_session

        # Serialize each value via the same serde used for checkpoints
        # so any LangGraph type (BaseMessage, dict, etc.) round-trips.
        rows = []
        for channel, value in writes:
            type_str, payload_bytes = self.serde.dumps_typed(value)
            rows.append({
                "session_id": thread_id,
                "checkpoint_id": checkpoint_id,
                "task_id": task_id,
                "task_path": task_path,
                "channel": channel,
                "value": json.dumps({
                    "type": type_str,
                    "payload_b64": base64.b64encode(payload_bytes).decode("ascii"),
                }),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        async with async_session() as db:
            for row in rows:
                await db.execute(
                    text(
                        "INSERT OR REPLACE INTO checkpoint_writes "
                        "(session_id, checkpoint_id, task_id, task_path, channel, value, created_at) "
                        "VALUES (:sid, :cid, :tid, :tp, :ch, :val, :cat)"
                    ),
                    {
                        "sid": row["session_id"],
                        "cid": row["checkpoint_id"],
                        "tid": row["task_id"],
                        "tp": row["task_path"],
                        "ch": row["channel"],
                        "val": row["value"],
                        "cat": row["created_at"],
                    },
                )
            await db.commit()

    async def _load_writes(
        self,
        thread_id: str,
        checkpoint_id: str = "",
    ) -> list[tuple[str, str, Any]]:
        """Load pending writes for a thread (optionally scoped to a checkpoint).

        Returns ``(task_id, channel, value)`` triples to match
        langgraph's ``PendingWrite`` type. langgraph 1.2+ unpacks each
        pending write as ``for tid, k, v in saved.pending_writes`` —
        returning the older ``(channel, value)`` 2-tuple shape here
        raises ``ValueError: not enough values to unpack (expected 3,
        got 2)`` on the second turn of any checkpointed conversation.

        When ``checkpoint_id`` is provided, only writes for that
        checkpoint are returned — this matches LangGraph 1.2+'s
        semantics where pending_writes belong to a specific checkpoint
        (the one that was in progress when the writes were emitted).
        """
        from src.infra.db.engine import async_session

        out: list[tuple[str, str, Any]] = []
        async with async_session() as db:
            if checkpoint_id:
                result = await db.execute(
                    text(
                        "SELECT task_id, channel, value FROM checkpoint_writes "
                        "WHERE session_id = :sid AND checkpoint_id = :cid "
                        "ORDER BY task_id, created_at"
                    ),
                    {"sid": thread_id, "cid": checkpoint_id},
                )
            else:
                result = await db.execute(
                    text(
                        "SELECT task_id, channel, value FROM checkpoint_writes "
                        "WHERE session_id = :sid "
                        "ORDER BY task_id, created_at"
                    ),
                    {"sid": thread_id},
                )
            for row in result.fetchall():
                try:
                    encoded = json.loads(row.value)
                    payload_bytes = base64.b64decode(encoded["payload_b64"])
                    value = self.serde.loads_typed(
                        (encoded.get("type", "json"), payload_bytes)
                    )
                    out.append((row.task_id, row.channel, value))
                except Exception:
                    # Skip malformed rows rather than crashing the load.
                    continue
        return out

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints and pending writes for a thread."""
        cps = await self._store.list(thread_id)
        for cp in cps:
            await self._store.delete(thread_id, cp.sequence)
        # Phase 4c: also clear the pending-writes + blobs tables for this thread.
        from src.infra.db.engine import async_session

        async with async_session() as db:
            await db.execute(
                text("DELETE FROM checkpoint_writes WHERE session_id = :sid"),
                {"sid": thread_id},
            )
            await db.execute(
                text("DELETE FROM checkpoint_blobs WHERE session_id = :sid"),
                {"sid": thread_id},
            )
            await db.commit()

    # ── Sync API (LangGraph requires these even if only async is used) ──
    # All sync methods raise NotImplementedError. deepagents only uses
    # the async API, so this is safe in production. Tests that invoke
    # the graph synchronously will fail loud rather than silently.

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        raise NotImplementedError("Use aget_tuple (async only)")

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        raise NotImplementedError("Use alist (async only)")

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        raise NotImplementedError("Use aput (async only)")

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        raise NotImplementedError("Use aput_writes (async only)")

    def delete_thread(self, thread_id: str) -> None:
        raise NotImplementedError("Use adelete_thread (async only)")

    # ── Internal helpers ──

    def _decode_tuple(
        self, cp: Any, pending_writes: list[tuple[str, str, Any]] | None = None
    ) -> CheckpointTuple:
        """Decode our Checkpoint row → LangGraph CheckpointTuple.

        ``pending_writes`` is the list of ``(task_id, channel, value)``
        triples loaded from the ``checkpoint_writes`` table (Phase 4c).
        When None, the tuple is returned with an empty pending_writes
        list (preserves legacy behavior for the alist path).
        """
        tool_state = cp.tool_state if hasattr(cp, "tool_state") else {}
        encoded = (tool_state or {}).get("langgraph_checkpoint")
        # Recover the langgraph checkpoint_id stashed by aput (empty
        # for legacy Phase 3 checkpoints). The returned config MUST
        # carry this id so langgraph's internal loop can correlate
        # subsequent aput_writes calls with the right checkpoint.
        lg_checkpoint_id = ""
        if isinstance(encoded, dict):
            lg_checkpoint_id = encoded.get("checkpoint_id", "") or ""
        if encoded is None:
            # Legacy Phase 3 checkpoint with no LangGraph payload.
            # Synthesize an empty LangGraph checkpoint so LangGraph can
            # resume without crashing.
            checkpoint: Checkpoint = {
                "v": 1,
                "id": str(cp.sequence),
                "ts": cp.created_at.isoformat() if cp.created_at else "",
                "channel_values": {"messages": []},
                "channel_versions": {},
                "versions_seen": {},
                "pending_sends": [],
            }
        elif isinstance(encoded, dict) and "payload_b64" in encoded:
            # Phase 4 format: base64-encoded bytes (JSON-safe).
            payload_bytes = base64.b64decode(encoded["payload_b64"])
            checkpoint = self.serde.loads_typed(
                (encoded.get("type", "json"), payload_bytes)
            )
            # aput pops channel_values before serializing (values live in
            # blobs). Ensure the key exists so downstream code that does
            # checkpoint["channel_values"] doesn't raise KeyError.
            if isinstance(checkpoint, dict) and "channel_values" not in checkpoint:
                checkpoint["channel_values"] = {}
        else:
            # Unknown format — fall back to empty checkpoint.
            checkpoint = {
                "v": 1,
                "id": str(cp.sequence),
                "ts": cp.created_at.isoformat() if cp.created_at else "",
                "channel_values": {"messages": []},
                "channel_versions": {},
                "versions_seen": {},
                "pending_sends": [],
            }
        # Phase 4 does not track parent chain (spec §12 open question 3).
        # Prefer the stashed langgraph checkpoint_id; fall back to the
        # internal sequence number for legacy checkpoints without one.
        config_checkpoint_id = lg_checkpoint_id or str(cp.sequence)
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": self._session_id,
                    "checkpoint_id": config_checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=cp.metadata if isinstance(cp.metadata, dict) else {},
            parent_config=None,
            pending_writes=pending_writes or [],
        )

    def _extract_plain_messages(self, langchain_messages: list) -> list[dict]:
        """Convert LangChain BaseMessage list → plain dicts for the
        ``messages`` column. Used for human-readable debug output."""
        out: list[dict] = []
        for m in langchain_messages:
            # LangChain BaseMessage has .type and .content attrs; dicts
            # (from older checkpoints) are passed through.
            if isinstance(m, dict):
                out.append({
                    "role": m.get("role", "user"),
                    "content": m.get("content", ""),
                })
            else:
                role = getattr(m, "type", "user")
                content = getattr(m, "content", "")
                out.append({"role": role, "content": content})
        return out


# ── LangChainToolShim ────────────────────────────────────────────────────


class LangChainToolShim(BaseTool):
    """Wraps a harness ``ToolDefinition`` as a LangChain ``BaseTool``.

    Per spec D6: ``_arun`` delegates to ``ctx.tool_engine.execute()``
    so the Phase 3a pipeline (whitelist → sandbox → guardrail → hook →
    handler) runs for every tool call coming from deepagents.

    The shim holds a reference to the per-run ``HarnessContext``. This
    is safe because shims are constructed fresh inside each
    ``DeepAgentsAdapter.run()`` call and never reused across runs.

    Pydantic v2 note: ``BaseTool`` is a Pydantic v2 model, so private
    attrs must be set via ``object.__setattr__`` to avoid the model
    validator rejecting unknown fields.
    """

    name: str = ""
    description: str = ""
    args_schema: Any = None

    def __init__(self, tool_def: "ToolDefinition", ctx: "HarnessContext") -> None:
        args_schema = self._build_args_schema(tool_def.input_schema)
        super().__init__(
            name=tool_def.name,
            description=tool_def.description,
            args_schema=args_schema,
        )
        # Use object.__setattr__ for private attrs (Pydantic v2 model).
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_tool_def", tool_def)

    def _run(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError("Use _arun (async only)")

    async def _arun(self, **kwargs: Any) -> str:
        ctx = object.__getattribute__(self, "_ctx")
        tool_def = object.__getattribute__(self, "_tool_def")
        try:
            result = await ctx.tool_engine.execute(tool_def.name, kwargs, ctx)
        except Exception as exc:
            # ToolError, ToolPermissionError, ToolNotFoundError, etc. —
            # return as ERROR: string so deepagents sees a tool failure
            # rather than crashing the agent loop.
            return f"ERROR: {type(exc).__name__}: {exc}"
        if result.error:
            return f"ERROR: {result.error}"
        return result.output

    @staticmethod
    def _build_args_schema(input_schema: dict | None) -> Any:
        """Convert a JSON Schema dict → permissive Pydantic model class.

        Used as ``args_schema`` so LangChain validates tool call args
        before dispatching to ``_arun``. Falls back to ``None`` (no
        validation) on parse failure so we never block a tool call at
        the schema layer — strict validation lives in the harness tool
        engine and the per-tool handler.
        """
        from pydantic import create_model

        if not isinstance(input_schema, dict):
            return None
        props = input_schema.get("properties", {})
        if not props:
            return None
        # Naive conversion: treat all properties as Optional[Any].
        fields = {name: (Any, None) for name in props}
        return create_model("ToolArgs", **fields)
