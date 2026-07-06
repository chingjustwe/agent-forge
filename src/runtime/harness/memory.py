"""P3b-P2: MemoryStore — abstract memory backend with SQLite FTS5 default.

Long-term memory persistence for agents. Records are scoped to
session / user / workspace / agent. The default ``SQLiteMemoryStore``
uses SQLite FTS5 for full-text search; the abstract ``MemoryStore``
interface allows swapping in vector stores (sqlite-vec, Chroma) without
changing callers.

``MemoryScope`` is the per-run accessor injected into
``HarnessContext.memory``. Adapters and tools call ``MemoryScope``, not
the raw store — it auto-resolves ``scope_id`` from the context identity.
"""
from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import text

from src.infra.db.engine import async_session

logger = logging.getLogger(__name__)

MemoryScopeType = Literal["session", "user", "workspace", "agent"]


class MemoryRecord(BaseModel):
    """A single memory record."""

    id: str
    scope: MemoryScopeType
    scope_id: str
    key: str = ""
    content: str
    embedding: list[float] | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime | None = None
    expires_at: datetime | None = None


class MemoryStore(ABC):
    """Abstract memory backend. Default: SQLite + FTS5."""

    @abstractmethod
    async def save(self, record: MemoryRecord) -> str:
        """Persist a record. Returns the record id."""
        ...

    @abstractmethod
    async def recall(
        self,
        query: str,
        scope: str,
        scope_id: str,
        limit: int = 5,
    ) -> list[MemoryRecord]:
        """Full-text search for records matching ``query``."""
        ...

    @abstractmethod
    async def get(self, record_id: str) -> MemoryRecord | None:
        """Fetch a single record by id."""
        ...

    @abstractmethod
    async def delete(self, record_id: str) -> None:
        """Delete a record by id."""
        ...

    @abstractmethod
    async def list(
        self, scope: str, scope_id: str, limit: int = 100
    ) -> list[MemoryRecord]:
        """List records by scope (no search)."""
        ...


class SQLiteMemoryStore(MemoryStore):
    """Default implementation: SQLite + FTS5 full-text search.

    The ``memory_records`` table stores the raw data; ``memory_records_fts``
    is a FTS5 virtual table for content search. Both are created by the
    M15 migration in ``main.py``.
    """

    async def save(self, record: MemoryRecord) -> str:
        if record.created_at is None:
            record.created_at = datetime.now(timezone.utc)
        async with async_session() as db:
            await db.execute(
                text(
                    "INSERT OR REPLACE INTO memory_records "
                    "(id, scope, scope_id, key, content, metadata, created_at, expires_at) "
                    "VALUES (:id, :scope, :sid, :key, :content, :meta, :cat, :exp)"
                ),
                {
                    "id": record.id,
                    "scope": record.scope,
                    "sid": record.scope_id,
                    "key": record.key,
                    "content": record.content,
                    "meta": json.dumps(record.metadata),
                    "cat": record.created_at.isoformat(),
                    "exp": record.expires_at.isoformat() if record.expires_at else None,
                },
            )
            # Sync FTS5 index: delete old entry and insert new one
            await db.execute(
                text(
                    "INSERT INTO memory_records_fts(rowid, content) "
                    "SELECT rowid, content FROM memory_records WHERE id = :id"
                ),
                {"id": record.id},
            )
            await db.commit()
        return record.id

    async def recall(
        self,
        query: str,
        scope: str,
        scope_id: str,
        limit: int = 5,
    ) -> list[MemoryRecord]:
        if not query.strip():
            return []
        # FTS5 MATCH with sanitization — wrap terms in quotes to avoid
        # syntax errors from special characters.
        fts_query = " ".join(f'"{w}"' for w in query.split() if w)
        if not fts_query:
            return []
        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT mr.id, mr.scope, mr.scope_id, mr.key, mr.content, "
                    "mr.metadata, mr.created_at, mr.expires_at "
                    "FROM memory_records mr "
                    "JOIN memory_records_fts fts ON mr.rowid = fts.rowid "
                    "WHERE mr.scope = :scope AND mr.scope_id = :sid "
                    "AND memory_records_fts MATCH :q "
                    "ORDER BY rank LIMIT :lim"
                ),
                {"scope": scope, "sid": scope_id, "q": fts_query, "lim": limit},
            )
            return [self._row_to_record(r) for r in result.fetchall()]

    async def get(self, record_id: str) -> MemoryRecord | None:
        async with async_session() as db:
            result = await db.execute(
                text("SELECT * FROM memory_records WHERE id = :id"),
                {"id": record_id},
            )
            row = result.fetchone()
            return self._row_to_record(row) if row else None

    async def delete(self, record_id: str) -> None:
        async with async_session() as db:
            await db.execute(
                text("DELETE FROM memory_records WHERE id = :id"),
                {"id": record_id},
            )
            await db.commit()

    async def list(
        self, scope: str, scope_id: str, limit: int = 100
    ) -> list[MemoryRecord]:
        async with async_session() as db:
            result = await db.execute(
                text(
                    "SELECT * FROM memory_records "
                    "WHERE scope = :scope AND scope_id = :sid "
                    "ORDER BY created_at DESC LIMIT :lim"
                ),
                {"scope": scope, "sid": scope_id, "lim": limit},
            )
            return [self._row_to_record(r) for r in result.fetchall()]

    def _row_to_record(self, row) -> MemoryRecord:
        metadata = json.loads(row.metadata) if row.metadata else {}
        created_at = None
        if row.created_at:
            try:
                created_at = datetime.fromisoformat(row.created_at)
            except (ValueError, TypeError):
                pass
        expires_at = None
        if row.expires_at:
            try:
                expires_at = datetime.fromisoformat(row.expires_at)
            except (ValueError, TypeError):
                pass
        return MemoryRecord(
            id=row.id,
            scope=row.scope,
            scope_id=row.scope_id,
            key=row.key or "",
            content=row.content,
            metadata=metadata,
            created_at=created_at,
            expires_at=expires_at,
        )


class MemoryScope:
    """Per-run memory accessor bound to context identity.

    Injected into ``HarnessContext.memory``. Adapters and tools call
    ``ctx.memory.remember()`` / ``ctx.memory.recall()`` — the scope_id
    is auto-resolved from the bound identity fields.
    """

    def __init__(
        self,
        store: MemoryStore,
        session_id: str,
        user_id: str,
        workspace_id: str,
        agent_id: str,
    ) -> None:
        self._store = store
        self._ids = {
            "session": session_id,
            "user": user_id,
            "workspace": workspace_id,
            "agent": agent_id,
        }

    async def remember(
        self,
        key: str,
        content: str,
        scope: MemoryScopeType = "session",
        metadata: dict | None = None,
    ) -> str:
        """Save a memory record. Returns the record id."""
        record = MemoryRecord(
            id=uuid.uuid4().hex[:32],
            scope=scope,
            scope_id=self._ids.get(scope, ""),
            key=key,
            content=content,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc),
        )
        return await self._store.save(record)

    async def recall(
        self,
        query: str,
        scope: MemoryScopeType = "session",
        limit: int = 5,
    ) -> list[MemoryRecord]:
        """Full-text search within the given scope."""
        return await self._store.recall(
            query=query,
            scope=scope,
            scope_id=self._ids.get(scope, ""),
            limit=limit,
        )

    async def list(
        self,
        scope: MemoryScopeType = "session",
        limit: int = 100,
    ) -> list[MemoryRecord]:
        """List records in the given scope (no search)."""
        return await self._store.list(
            scope=scope,
            scope_id=self._ids.get(scope, ""),
            limit=limit,
        )

    async def get(self, record_id: str) -> MemoryRecord | None:
        return await self._store.get(record_id)

    async def delete(self, record_id: str) -> None:
        await self._store.delete(record_id)
