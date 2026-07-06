"""P3b-P2: SkillRegistry — markdown-based skill loading.

Skills are markdown files (``.agents/skills/*.md``) with YAML
frontmatter. The frontmatter contains metadata (name, description,
required tools, version); the body is the instruction text injected
into the system prompt by ``PromptAssembler``.

No DB table — skills are file-based with an in-memory cache. The
registry scans configured directories on startup and supports
hot-reload of individual skills.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


class SkillPackage(BaseModel):
    """A loaded skill definition."""

    name: str
    description: str = ""
    instructions: str = ""
    tools: list[str] = Field(default_factory=list)
    required_memory: bool = False
    version: str = "1.0"
    file_path: str = ""


class SkillRegistry:
    """Loads skills from ``.agents/skills/*.md`` and configurable dirs.

    Usage:
        registry = SkillRegistry([Path(".agents/skills")])
        await registry.scan()  # load all at startup
        skill = await registry.load("tdd-orchestrator")
    """

    def __init__(self, skill_dirs: list[Path] | None = None) -> None:
        self._skill_dirs = skill_dirs or [Path(".agents/skills")]
        self._cache: dict[str, SkillPackage] = {}

    def add_dir(self, path: Path) -> None:
        """Add a skill directory to scan."""
        if path not in self._skill_dirs:
            self._skill_dirs.append(path)

    async def scan(self) -> list[SkillPackage]:
        """Scan all configured directories and load all skills.

        Returns the list of loaded skills. Existing cache entries are
        replaced if the file has changed.
        """
        loaded: list[SkillPackage] = []
        for d in self._skill_dirs:
            if not d.is_dir():
                continue
            for md_file in sorted(d.glob("*.md")):
                try:
                    skill = self._parse_markdown(md_file)
                    self._cache[skill.name] = skill
                    loaded.append(skill)
                except Exception as exc:
                    logger.warning("Failed to load skill %s: %s", md_file, exc)
        logger.info("SkillRegistry: loaded %d skills", len(loaded))
        return loaded

    async def load(self, name: str) -> SkillPackage:
        """Load a skill by name. Raises KeyError if not found."""
        if name in self._cache:
            return self._cache[name]
        # Try to find and load the file
        for d in self._skill_dirs:
            path = d / f"{name}.md"
            if path.is_file():
                skill = self._parse_markdown(path)
                self._cache[skill.name] = skill
                return skill
        raise KeyError(f"Skill {name!r} not found")

    async def list(self) -> list[SkillPackage]:
        """List all cached skills. Runs scan() if cache is empty."""
        if not self._cache:
            await self.scan()
        return list(self._cache.values())

    async def reload(self, name: str) -> SkillPackage:
        """Hot-reload a skill from disk."""
        for d in self._skill_dirs:
            path = d / f"{name}.md"
            if path.is_file():
                skill = self._parse_markdown(path)
                self._cache[skill.name] = skill
                logger.info("SkillRegistry: reloaded %r", name)
                return skill
        raise KeyError(f"Skill {name!r} not found")

    def _parse_markdown(self, path: Path) -> SkillPackage:
        """Parse a markdown file with YAML frontmatter into a SkillPackage.

        Format:
            ---
            name: my-skill
            description: ...
            tools: [tool1, tool2]
            required_memory: true
            version: "1.0"
            ---
            # Instructions body...
        """
        content = path.read_text(encoding="utf-8")
        match = _FRONTMATTER_RE.match(content)
        if not match:
            # No frontmatter — use filename as name, full content as instructions
            name = path.stem
            return SkillPackage(
                name=name,
                description="",
                instructions=content.strip(),
                file_path=str(path),
            )

        frontmatter_text, body = match.groups()
        meta = yaml.safe_load(frontmatter_text) or {}
        if not isinstance(meta, dict):
            meta = {}

        name = meta.get("name", path.stem)
        description = meta.get("description", "")
        tools = meta.get("tools", [])
        if isinstance(tools, str):
            tools = [t.strip() for t in tools.split(",") if t.strip()]
        required_memory = bool(meta.get("required_memory", False))
        version = str(meta.get("version", "1.0"))

        return SkillPackage(
            name=name,
            description=description,
            instructions=body.strip(),
            tools=tools,
            required_memory=required_memory,
            version=version,
            file_path=str(path),
        )
