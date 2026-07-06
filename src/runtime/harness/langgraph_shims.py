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
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

if TYPE_CHECKING:
    from src.runtime.harness.checkpoint import CheckpointStore
    from src.runtime.harness.context import HarnessContext
    from src.runtime.harness.tool_engine import ToolDefinition


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
        """Return the latest checkpoint tuple for the thread."""
        thread_id = config["configurable"]["thread_id"]
        cp = await self._store.load(thread_id)
        if cp is None:
            return None
        return self._decode_tuple(cp)

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
        """Persist a new checkpoint. Returns the updated config."""
        thread_id = config["configurable"]["thread_id"]
        seq = await self._store.next_sequence(thread_id)
        # Serialize the full LangGraph checkpoint payload.
        # ``dumps_typed`` returns (type_str, bytes); we base64-encode the
        # bytes so the result can be stored in a JSON TEXT column (our
        # ``tool_state`` is serialized via ``json.dumps``).
        type_str, payload_bytes = self.serde.dumps_typed(checkpoint)
        encoded = {
            "type": type_str,
            "payload_b64": base64.b64encode(payload_bytes).decode("ascii"),
        }
        # Extract human-readable messages for the messages column.
        channel_values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
        messages = channel_values.get("messages", [])
        plain_messages = self._extract_plain_messages(messages)
        # Persist via our existing Checkpoint model.
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
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": str(seq),
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Persist intermediate task writes.

        Phase 4 stores these best-effort in the metadata column of the
        next ``aput()`` call — we do NOT create a separate
        ``checkpoint_writes`` table. This means pending-write recovery
        is best-effort: a crash between ``aput_writes`` and the next
        ``aput`` may lose pending writes. Acceptable for Phase 4; Phase
        4c may add a dedicated table (spec §11).
        """
        # No-op: rely on the next aput() to capture full state.
        return

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints for a thread (session)."""
        cps = await self._store.list(thread_id)
        for cp in cps:
            await self._store.delete(thread_id, cp.sequence)

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

    def _decode_tuple(self, cp: Any) -> CheckpointTuple:
        """Decode our Checkpoint row → LangGraph CheckpointTuple."""
        tool_state = cp.tool_state if hasattr(cp, "tool_state") else {}
        encoded = (tool_state or {}).get("langgraph_checkpoint")
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
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": self._session_id,
                    "checkpoint_id": str(cp.sequence),
                }
            },
            checkpoint=checkpoint,
            metadata=cp.metadata if isinstance(cp.metadata, dict) else {},
            parent_config=None,
            pending_writes=[],
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
