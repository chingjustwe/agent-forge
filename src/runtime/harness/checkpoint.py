"""P3a-P1: CheckpointStore — checkpoint + crash recovery.

Persists per-session state (messages + tool state) so that a crashed
run can resume from the last checkpoint. The ``Checkpoints`` table
stores JSON snapshots keyed by ``(session_id, sequence)``.

Wave 2.5: ``CheckpointScope`` (the DirectLLM-only per-run accessor)
was removed alongside the DirectLLM adapter. The deepagents adapter
now consumes the ``CheckpointStore`` directly via ``ctx.checkpoint``.
"""
from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.engine import async_session

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext

logger = logging.getLogger(__name__)


class Checkpoint(BaseModel):
    """A conversation checkpoint (full state snapshot)."""

    session_id: str
    sequence: int  # monotonic per session
    messages: list[dict] = Field(default_factory=list)
    tool_state: dict = Field(default_factory=dict)
    agent_id: str = ""
    metadata: dict = Field(default_factory=dict)
    created_at: datetime | None = None


class CheckpointStore(ABC):
    """Abstract checkpoint backend."""

    @abstractmethod
    async def save(self, cp: Checkpoint) -> None: ...

    @abstractmethod
    async def load(
        self, session_id: str, sequence: int | None = None
    ) -> Checkpoint | None: ...

    @abstractmethod
    async def list(self, session_id: str) -> list[Checkpoint]: ...

    @abstractmethod
    async def delete(self, session_id: str, sequence: int) -> None: ...


class SQLiteCheckpointStore(CheckpointStore):
    """Default implementation: stores in SQLite ``checkpoints`` table.

    The table is created by the M13 migration in ``main.py``. Each row
    is a JSON snapshot of the full conversation state.
    """

    async def save(self, cp: Checkpoint) -> None:
        if cp.created_at is None:
            cp.created_at = datetime.now(timezone.utc)
        async with async_session() as db:
            await db.execute(
                text(
                    "INSERT OR REPLACE INTO checkpoints "
                    "(session_id, sequence, messages, tool_state, agent_id, metadata, created_at) "
                    "VALUES (:sid, :seq, :msg, :ts, :aid, :meta, :cat)"
                ),
                {
                    "sid": cp.session_id,
                    "seq": cp.sequence,
                    "msg": json.dumps(cp.messages),
                    "ts": json.dumps(cp.tool_state),
                    "aid": cp.agent_id,
                    "meta": json.dumps(cp.metadata),
                    "cat": cp.created_at.isoformat(),
                },
            )
            await db.commit()

    async def load(
        self, session_id: str, sequence: int | None = None
    ) -> Checkpoint | None:
        async with async_session() as db:
            if sequence is not None:
                result = await db.execute(
                    text(
                        "SELECT * FROM checkpoints "
                        "WHERE session_id = :sid AND sequence = :seq"
                    ),
                    {"sid": session_id, "seq": sequence},
                )
            else:
                result = await db.execute(
                    text(
                        "SELECT * FROM checkpoints "
                        "WHERE session_id = :sid ORDER BY sequence DESC LIMIT 1"
                    ),
                    {"sid": session_id},
                )
            row = result.fetchone()
            if row is None:
                return None
            return self._row_to_checkpoint(row)

    async def list(self, session_id: str) -> list[Checkpoint]:
        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT * FROM checkpoints "
                    "WHERE session_id = :sid ORDER BY sequence ASC"
                ),
                {"sid": session_id},
            )
            return [self._row_to_checkpoint(r) for r in result.fetchall()]

    async def delete(self, session_id: str, sequence: int) -> None:
        async with async_session() as db:
            await db.execute(
                text(
                    "DELETE FROM checkpoints "
                    "WHERE session_id = :sid AND sequence = :seq"
                ),
                {"sid": session_id, "seq": sequence},
            )
            await db.commit()

    async def next_sequence(self, session_id: str) -> int:
        """Return the next sequence number for a session."""
        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT COALESCE(MAX(sequence), 0) AS max_seq "
                    "FROM checkpoints WHERE session_id = :sid"
                ),
                {"sid": session_id},
            )
            row = result.fetchone()
            return (row.max_seq + 1) if row else 1

    def _row_to_checkpoint(self, row: Any) -> Checkpoint:
        messages = json.loads(row.messages) if row.messages else []
        tool_state = json.loads(row.tool_state) if row.tool_state else {}
        metadata = json.loads(row.metadata) if row.metadata else {}
        created_at = None
        if row.created_at:
            try:
                created_at = datetime.fromisoformat(row.created_at)
            except (ValueError, TypeError):
                created_at = None
        return Checkpoint(
            session_id=row.session_id,
            sequence=row.sequence,
            messages=messages,
            tool_state=tool_state,
            agent_id=row.agent_id,
            metadata=metadata,
            created_at=created_at,
        )
