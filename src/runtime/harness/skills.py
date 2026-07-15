"""SkillRegistry — multi-layer skill aggregation.

Skills come from three layers (Skills layers spec):

- ``user``    — directory ``settings.skill_user_dir`` (default
  ``~/.agents/skills``). Read-only, global.
- ``project`` — directory ``agents/skills`` at the repo root (plus the
  legacy ``.agents/skills`` for backward compatibility). Read-only, global.
- ``workspace`` — writable, per-workspace skills stored via a
  ``SkillStore`` backend (``db`` by default). Editable through the API.

Directory layers are markdown files with YAML frontmatter (metadata) and a
body (the instruction text injected into the system prompt by
``PromptAssembler``). Name-resolution priority is
``workspace > project > user``.

The registry is backward-compatible with the original single-directory
constructor ``SkillRegistry([Path(".agents/skills")])`` — those dirs are
treated as the ``project`` layer.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .skill_store import SkillStore

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)

# Valid skill name pattern — shared with SkillStore so names are consistent
# across directory files and the writable backend.
SKILL_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


class SkillPackage(BaseModel):
    """A loaded skill definition."""

    name: str
    description: str = ""
    instructions: str = ""
    tools: list[str] = Field(default_factory=list)
    required_memory: bool = False
    version: str = "1.0"
    file_path: str = ""
    # ── layer metadata (Skills layers spec) ──
    layer: str = "project"          # "user" | "project" | "workspace"
    editable: bool = False          # True only for the workspace layer
    workspace_id: str | None = None  # set only for the workspace layer
    created_by: str | None = None   # owner (workspace layer only)


def parse_skill_markdown(content: str, *, name_fallback: str = "") -> dict[str, Any]:
    """Parse markdown-with-frontmatter into a field dict.

    Shared by the directory scanner and ``FilesystemSkillStore`` so both
    honour the exact same frontmatter format.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {
            "name": name_fallback,
            "description": "",
            "instructions": content.strip(),
            "tools": [],
            "required_memory": False,
            "version": "1.0",
        }

    frontmatter_text, body = match.groups()
    meta = yaml.safe_load(frontmatter_text) or {}
    if not isinstance(meta, dict):
        meta = {}

    tools = meta.get("tools") or []
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]

    return {
        "name": meta.get("name") or name_fallback,
        "description": meta.get("description") or "",
        "instructions": body.strip(),
        "tools": tools,
        "required_memory": bool(meta.get("required_memory", False)),
        "version": str(meta.get("version") or "1.0"),
    }


def render_skill_markdown(pkg: SkillPackage) -> str:
    """Render a ``SkillPackage`` back to markdown-with-frontmatter.

    Used by ``FilesystemSkillStore`` to persist workspace skills in the
    same format as directory-layer skills.
    """
    meta = {
        "name": pkg.name,
        "description": pkg.description,
        "tools": pkg.tools,
        "required_memory": pkg.required_memory,
        "version": pkg.version,
    }
    frontmatter = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{frontmatter}\n---\n\n{pkg.instructions}\n"


def _find_skill_md(skill_dir: Path) -> Path | None:
    """Locate the primary markdown file inside a skill *directory*.

    Skills may be stored as a folder containing ``SKILL.md`` (preferred),
    ``<name>.md``, or the first ``.md`` file found. Returns ``None`` if the
    directory holds no markdown.
    """
    for candidate in (skill_dir / "SKILL.md", skill_dir / f"{skill_dir.name}.md"):
        if candidate.is_file():
            return candidate
    mds = sorted(skill_dir.glob("*.md"))
    return mds[0] if mds else None


class SkillRegistry:
    """Aggregates skills across user / project / workspace layers.

    Usage (new)::

        registry = SkillRegistry(
            user_dir=Path.home() / ".agents/skills",
            project_dir=Path("agents/skills"),
            store=DBSkillStore(),
        )
        await registry.scan()
        skill = await registry.load("tdd-orchestrator", workspace_id="ws_1")

    Usage (legacy, still supported)::

        registry = SkillRegistry([Path(".agents/skills")])  # project layer
    """

    def __init__(
        self,
        skill_dirs: list[Path] | None = None,
        *,
        user_dir: Path | None = None,
        project_dir: Path | None = None,
        store: "SkillStore | None" = None,
    ) -> None:
        # (Path, layer) pairs to scan. Order matters: lower priority first so
        # higher-priority layers overwrite same-named entries in the cache.
        self._dirs: list[tuple[Path, str]] = []
        if user_dir is not None:
            self._dirs.append((user_dir, "user"))
        if project_dir is not None:
            self._dirs.append((project_dir, "project"))
        # Legacy positional dirs → treated as the project layer.
        for d in skill_dirs or []:
            self._dirs.append((d, "project"))
        if not self._dirs:
            self._dirs.append((Path(".agents/skills"), "project"))

        self._store = store
        # Directory-layer cache (merged, name → package).
        self._cache: dict[str, SkillPackage] = {}

    @property
    def store(self) -> "SkillStore | None":
        """The writable workspace-layer backend (or None)."""
        return self._store

    def add_dir(self, path: Path, layer: str = "project") -> None:
        """Add a skill directory to scan (defaults to the project layer)."""
        if not any(d == path for d, _ in self._dirs):
            self._dirs.append((path, layer))

    async def scan(self) -> list[SkillPackage]:
        """Scan all configured directories and (re)load directory-layer skills.

        Supports both flat (``<dir>/<name>.md``) and nested
        (``<dir>/<name>/SKILL.md``) layouts. Higher-priority layers (project)
        overwrite same-named lower-priority ones (user).
        """
        loaded: list[SkillPackage] = []
        for d, layer in self._dirs:
            if not d.is_dir():
                continue
            # Flat markdown files directly in the skill directory.
            for md_file in sorted(d.glob("*.md")):
                try:
                    skill = self._parse_markdown(md_file, layer=layer)
                    self._cache[skill.name] = skill
                    loaded.append(skill)
                except Exception as exc:
                    logger.warning("Failed to load skill %s: %s", md_file, exc)
            # Nested skill directories: <d>/<name>/SKILL.md (or <name>.md).
            for sub in sorted(p for p in d.iterdir() if p.is_dir()):
                md_file = _find_skill_md(sub)
                if md_file is None:
                    continue
                try:
                    skill = self._parse_markdown(
                        md_file, layer=layer, name_fallback=sub.name
                    )
                    self._cache[skill.name] = skill
                    loaded.append(skill)
                except Exception as exc:
                    logger.warning("Failed to load skill %s: %s", sub, exc)
        logger.info("SkillRegistry: loaded %d directory skills", len(loaded))
        return loaded

    def _resolve_md(self, d: Path, name: str) -> Path | None:
        """Find the markdown file for ``name`` in directory ``d``.

        Handles both flat (``<d>/<name>.md``) and nested
        (``<d>/<name>/SKILL.md``) layouts. Returns ``None`` if not found.
        """
        flat = d / f"{name}.md"
        if flat.is_file():
            return flat
        nested = d / name
        if nested.is_dir():
            return _find_skill_md(nested)
        return None

    async def load(
        self, name: str, workspace_id: str | None = None
    ) -> SkillPackage:
        """Load a skill by name. Raises KeyError if not found.

        Resolution priority: ``workspace (store) > project > user``.
        """
        # 1. Workspace layer (highest priority).
        if workspace_id and self._store is not None:
            pkg = await self._store.get(workspace_id, name)
            if pkg is not None:
                return pkg
        # 2. Directory-layer cache.
        if name in self._cache:
            return self._cache[name]
        # 3. Directory-layer file lookup (cold cache).
        for d, layer in self._dirs:
            path = self._resolve_md(d, name)
            if path is not None:
                skill = self._parse_markdown(path, layer=layer, name_fallback=name)
                self._cache[skill.name] = skill
                return skill
        raise KeyError(f"Skill {name!r} not found")

    async def list(self, workspace_id: str | None = None) -> list[SkillPackage]:
        """List available skills.

        With no ``workspace_id`` returns only directory-layer skills. With a
        ``workspace_id`` merges in the workspace layer (which overrides
        same-named directory skills).
        """
        if not self._cache:
            await self.scan()
        merged: dict[str, SkillPackage] = dict(self._cache)
        if workspace_id and self._store is not None:
            for pkg in await self._store.list(workspace_id):
                merged[pkg.name] = pkg  # workspace layer wins
        return list(merged.values())

    async def get_readonly(self, name: str) -> SkillPackage:
        """Load a skill only from the read-only directory layers."""
        if name in self._cache:
            return self._cache[name]
        for d, layer in self._dirs:
            path = self._resolve_md(d, name)
            if path is not None:
                skill = self._parse_markdown(path, layer=layer, name_fallback=name)
                self._cache[skill.name] = skill
                return skill
        raise KeyError(f"Skill {name!r} not found")

    async def reload(self, name: str) -> SkillPackage:
        """Hot-reload a directory-layer skill from disk.

        Only directory layers support reload; workspace-layer skills are
        served live from the store and do not need reloading.
        """
        for d, layer in self._dirs:
            path = self._resolve_md(d, name)
            if path is not None:
                skill = self._parse_markdown(path, layer=layer, name_fallback=name)
                self._cache[skill.name] = skill
                logger.info("SkillRegistry: reloaded %r", name)
                return skill
        raise KeyError(f"Skill {name!r} not found")

    def _parse_markdown(
        self, path: Path, *, layer: str = "project", name_fallback: str | None = None
    ) -> SkillPackage:
        """Parse a markdown file with YAML frontmatter into a SkillPackage."""
        content = path.read_text(encoding="utf-8")
        fields = parse_skill_markdown(content, name_fallback=name_fallback or path.stem)
        return SkillPackage(
            **fields,
            file_path=str(path),
            layer=layer,
            editable=False,
        )
