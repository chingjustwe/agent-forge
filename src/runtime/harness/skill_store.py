"""SkillStore — writable backend abstraction for workspace-level skills.

The ``workspace`` layer of the skill system is writable (created / edited /
deleted via the API). This module defines the ``SkillStore`` interface and
two implementations:

- ``DBSkillStore`` (default): persists to the ``skills`` table.
- ``FilesystemSkillStore``: persists one markdown file per skill under
  ``<skill_fs_root>/<workspace_id>/<name>.md`` (same frontmatter format as
  the read-only directory layers).

Only the workspace layer goes through a ``SkillStore``; the user / project
directory layers are read-only and handled directly by ``SkillRegistry``.
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from src.infra.db.engine import async_session
from src.infra.db.models import Skill as SkillRow

from .skills import (
    SKILL_NAME_RE,
    SkillPackage,
    parse_skill_markdown,
    render_skill_markdown,
)

logger = logging.getLogger(__name__)


def _validate_name(name: str) -> None:
    if not name or not SKILL_NAME_RE.match(name):
        raise ValueError(
            f"Invalid skill name {name!r}: only [a-z0-9_-] characters allowed"
        )


class SkillStore(ABC):
    """Storage backend for workspace-level writable skills."""

    @abstractmethod
    async def list(self, workspace_id: str) -> list[SkillPackage]:
        """List all skills in a workspace."""

    @abstractmethod
    async def get(self, workspace_id: str, name: str) -> SkillPackage | None:
        """Get a skill by name, or None if it does not exist."""

    @abstractmethod
    async def save(self, workspace_id: str, pkg: SkillPackage) -> SkillPackage:
        """Create or update a skill (upsert on (workspace_id, name))."""

    @abstractmethod
    async def delete(self, workspace_id: str, name: str) -> bool:
        """Delete a skill. Returns True if a skill was removed."""

    @abstractmethod
    async def exists(self, workspace_id: str, name: str) -> bool:
        """Return whether a skill exists."""


class DBSkillStore(SkillStore):
    """Default backend — persists workspace skills to the ``skills`` table."""

    @staticmethod
    def _row_to_pkg(row: SkillRow) -> SkillPackage:
        return SkillPackage(
            name=row.name,
            description=row.description or "",
            instructions=row.instructions or "",
            tools=list(row.tools or []),
            required_memory=bool(row.required_memory),
            version=row.version or "1.0",
            file_path="",
            layer="workspace",
            editable=True,
            workspace_id=row.workspace_id,
        )

    async def list(self, workspace_id: str) -> list[SkillPackage]:
        async with async_session() as db:
            result = await db.execute(
                select(SkillRow)
                .where(SkillRow.workspace_id == workspace_id)
                .order_by(SkillRow.name)
            )
            return [self._row_to_pkg(r) for r in result.scalars().all()]

    async def get(self, workspace_id: str, name: str) -> SkillPackage | None:
        async with async_session() as db:
            result = await db.execute(
                select(SkillRow).where(
                    SkillRow.workspace_id == workspace_id,
                    SkillRow.name == name,
                )
            )
            row = result.scalar_one_or_none()
            return self._row_to_pkg(row) if row is not None else None

    async def save(self, workspace_id: str, pkg: SkillPackage) -> SkillPackage:
        _validate_name(pkg.name)
        now = datetime.now(timezone.utc)
        async with async_session() as db:
            result = await db.execute(
                select(SkillRow).where(
                    SkillRow.workspace_id == workspace_id,
                    SkillRow.name == pkg.name,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = SkillRow(
                    id=uuid.uuid4().hex,
                    workspace_id=workspace_id,
                    name=pkg.name,
                    created_at=now,
                )
                db.add(row)
            row.description = pkg.description
            row.instructions = pkg.instructions
            row.tools = list(pkg.tools or [])
            row.required_memory = 1 if pkg.required_memory else 0
            row.version = pkg.version or "1.0"
            row.updated_at = now
            await db.commit()
            await db.refresh(row)
            return self._row_to_pkg(row)

    async def delete(self, workspace_id: str, name: str) -> bool:
        async with async_session() as db:
            result = await db.execute(
                sa_delete(SkillRow).where(
                    SkillRow.workspace_id == workspace_id,
                    SkillRow.name == name,
                )
            )
            await db.commit()
            return result.rowcount > 0

    async def exists(self, workspace_id: str, name: str) -> bool:
        return (await self.get(workspace_id, name)) is not None


class FilesystemSkillStore(SkillStore):
    """Filesystem backend — one markdown file per workspace skill.

    Layout: ``<root>/<workspace_id>/<name>.md`` with the same
    frontmatter format as the read-only directory layers.
    """

    def __init__(self, root: str | Path = "./data/skills") -> None:
        self._root = Path(root)

    def _ws_dir(self, workspace_id: str) -> Path:
        return self._root / workspace_id

    def _path(self, workspace_id: str, name: str) -> Path:
        return self._ws_dir(workspace_id) / f"{name}.md"

    def _read(self, workspace_id: str, path: Path) -> SkillPackage:
        content = path.read_text(encoding="utf-8")
        fields = parse_skill_markdown(content, name_fallback=path.stem)
        return SkillPackage(
            **fields,
            file_path=str(path),
            layer="workspace",
            editable=True,
            workspace_id=workspace_id,
        )

    async def list(self, workspace_id: str) -> list[SkillPackage]:
        d = self._ws_dir(workspace_id)
        if not d.is_dir():
            return []
        skills: list[SkillPackage] = []
        for md_file in sorted(d.glob("*.md")):
            try:
                skills.append(self._read(workspace_id, md_file))
            except Exception as exc:
                logger.warning("Failed to read skill %s: %s", md_file, exc)
        return skills

    async def get(self, workspace_id: str, name: str) -> SkillPackage | None:
        path = self._path(workspace_id, name)
        if not path.is_file():
            return None
        return self._read(workspace_id, path)

    async def save(self, workspace_id: str, pkg: SkillPackage) -> SkillPackage:
        _validate_name(pkg.name)
        d = self._ws_dir(workspace_id)
        d.mkdir(parents=True, exist_ok=True)
        path = self._path(workspace_id, pkg.name)
        stored = pkg.model_copy(
            update={
                "layer": "workspace",
                "editable": True,
                "workspace_id": workspace_id,
                "file_path": str(path),
            }
        )
        path.write_text(render_skill_markdown(stored), encoding="utf-8")
        return stored

    async def delete(self, workspace_id: str, name: str) -> bool:
        path = self._path(workspace_id, name)
        if not path.is_file():
            return False
        path.unlink()
        return True

    async def exists(self, workspace_id: str, name: str) -> bool:
        return self._path(workspace_id, name).is_file()


def build_skill_store(backend: str, *, fs_root: str = "./data/skills") -> SkillStore:
    """Factory: instantiate a SkillStore from a backend name."""
    if backend == "filesystem":
        return FilesystemSkillStore(fs_root)
    return DBSkillStore()
