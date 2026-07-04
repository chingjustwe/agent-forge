"""Slug helpers for Workspace URLs.

P1-3: slugify a workspace name and ensure tenant-local uniqueness.
"""
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.models import Workspace


def slugify(name: str) -> str:
    """Convert a name to a URL-friendly slug.

    - lowercase
    - keep only [a-z0-9], whitespace, ``_`` and ``-`` (drop other specials)
    - collapse runs of whitespace / ``_`` / ``-`` into a single dash
    - trim leading/trailing dashes
    - fall back to "workspace" when the result is empty
    """
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9\s_-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    s = s.strip("-")
    return s or "workspace"


async def unique_slug(
    db: AsyncSession,
    tenant_id: str,
    base_slug: str,
    exclude_ws_id: str | None = None,
) -> str:
    """Ensure slug is unique within tenant by appending ``-2`` / ``-3`` on conflict.

    ``exclude_ws_id`` is used for update flows so a workspace's current slug
    is not treated as a conflict against itself.
    """
    candidate = base_slug
    suffix = 2
    while True:
        stmt = select(Workspace.id).where(
            Workspace.tenant_id == tenant_id,
            Workspace.slug == candidate,
        )
        if exclude_ws_id is not None:
            stmt = stmt.where(Workspace.id != exclude_ws_id)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing is None:
            return candidate
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
