"""Tests for SkillPackage and SkillRegistry.

Covers:
- SkillPackage: default field values, full-field construction
- SkillRegistry: markdown parsing (with frontmatter / without
  frontmatter / tools-as-comma-string), load by name, missing load →
  KeyError, list all scanned, list auto-triggers scan, reload with
  updated content, reload missing → KeyError, add_dir makes new
  skills loadable, scanning a non-existent dir is a no-op
"""
from __future__ import annotations

import pytest

from src.runtime.harness.skills import SkillPackage, SkillRegistry


# ── TestSkillPackage ────────────────────────────────────────────────────


class TestSkillPackage:
    def test_defaults(self):
        sk = SkillPackage(name="test")
        assert sk.name == "test"
        assert sk.description == ""
        assert sk.instructions == ""
        assert sk.tools == []
        assert sk.required_memory is False
        assert sk.version == "1.0"
        assert sk.file_path == ""

    def test_with_all_fields(self):
        sk = SkillPackage(
            name="full",
            description="A full skill",
            instructions="Do the thing.",
            tools=["a", "b"],
            required_memory=True,
            version="3.1.4",
            file_path="/skills/full.md",
        )
        assert sk.name == "full"
        assert sk.description == "A full skill"
        assert sk.instructions == "Do the thing."
        assert sk.tools == ["a", "b"]
        assert sk.required_memory is True
        assert sk.version == "3.1.4"
        assert sk.file_path == "/skills/full.md"


# ── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def skill_dir(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    return d


@pytest.fixture
def skill_file(skill_dir):
    f = skill_dir / "tdd.md"
    f.write_text(
        "---\n"
        "name: tdd-orchestrator\n"
        "description: Red-green-refactor discipline\n"
        "tools: [todo_write, todo_read, shell_exec]\n"
        "required_memory: true\n"
        'version: "2.0"\n'
        "---\n\n"
        "# TDD Orchestrator\n\n"
        "## Instructions\n"
        "Write tests first.\n"
    )
    return f


@pytest.fixture
def plain_skill_file(skill_dir):
    f = skill_dir / "notes.md"
    f.write_text("# Just notes\n\nSome freeform content without frontmatter.\n")
    return f


# ── TestSkillRegistry ───────────────────────────────────────────────────


class TestSkillRegistry:
    def test_parse_markdown_with_frontmatter(self, skill_file):
        reg = SkillRegistry([skill_file.parent])
        sk = reg._parse_markdown(skill_file)
        assert sk.name == "tdd-orchestrator"
        assert sk.description == "Red-green-refactor discipline"
        assert sk.tools == ["todo_write", "todo_read", "shell_exec"]
        assert sk.required_memory is True
        assert sk.version == "2.0"
        assert "# TDD Orchestrator" in sk.instructions
        assert "Write tests first." in sk.instructions
        assert sk.file_path == str(skill_file)

    def test_parse_markdown_no_frontmatter(self, plain_skill_file):
        reg = SkillRegistry([plain_skill_file.parent])
        sk = reg._parse_markdown(plain_skill_file)
        assert sk.name == "notes"
        assert sk.description == ""
        assert sk.tools == []
        assert sk.required_memory is False
        assert sk.version == "1.0"
        assert "# Just notes" in sk.instructions
        assert "Some freeform content" in sk.instructions

    def test_parse_markdown_tools_as_string(self, skill_dir):
        f = skill_dir / "strtools.md"
        f.write_text(
            "---\n"
            "name: str-tools\n"
            "description: tools as a comma string\n"
            "tools: tool1, tool2, tool3\n"
            "---\n\n"
            "Body text.\n"
        )
        reg = SkillRegistry([skill_dir])
        sk = reg._parse_markdown(f)
        assert sk.tools == ["tool1", "tool2", "tool3"]

    @pytest.mark.asyncio
    async def test_load_by_name(self, skill_file):
        reg = SkillRegistry([skill_file.parent])
        await reg.scan()
        sk = await reg.load("tdd-orchestrator")
        assert sk.name == "tdd-orchestrator"
        assert sk.description == "Red-green-refactor discipline"

    @pytest.mark.asyncio
    async def test_load_missing_raises_keyerror(self, skill_dir):
        reg = SkillRegistry([skill_dir])
        await reg.scan()
        with pytest.raises(KeyError):
            await reg.load("nonexistent-skill")

    @pytest.mark.asyncio
    async def test_list_returns_all_scanned(self, skill_dir, skill_file):
        # Add a second skill to the same dir
        second = skill_dir / "second.md"
        second.write_text(
            "---\nname: second-one\ndescription: number two\n---\n\nBody.\n"
        )
        reg = SkillRegistry([skill_dir])
        await reg.scan()
        skills = await reg.list()
        assert len(skills) == 2
        assert {s.name for s in skills} == {"tdd-orchestrator", "second-one"}

    @pytest.mark.asyncio
    async def test_list_triggers_scan_if_cache_empty(self, skill_file):
        reg = SkillRegistry([skill_file.parent])
        # list() without an explicit scan() should auto-scan
        skills = await reg.list()
        assert len(skills) == 1
        assert skills[0].name == "tdd-orchestrator"

    @pytest.mark.asyncio
    async def test_reload(self, skill_dir):
        # Filename matches skill name so reload() can locate the file
        # (reload searches for {name}.md, not the original cache key).
        f = skill_dir / "reloadable.md"
        f.write_text(
            "---\n"
            "name: reloadable\n"
            "description: v1\n"
            'version: "1.0"\n'
            "---\n\n"
            "Original body.\n"
        )
        reg = SkillRegistry([skill_dir])
        await reg.scan()
        original = await reg.load("reloadable")
        assert original.description == "v1"
        assert original.version == "1.0"

        # Rewrite the file and hot-reload
        f.write_text(
            "---\n"
            "name: reloadable\n"
            "description: v2\n"
            'version: "2.0"\n'
            "---\n\n"
            "Updated body.\n"
        )
        reloaded = await reg.reload("reloadable")
        assert reloaded.description == "v2"
        assert reloaded.version == "2.0"
        assert "Updated body." in reloaded.instructions
        # Cache reflects the reloaded version
        assert (await reg.load("reloadable")).description == "v2"

    @pytest.mark.asyncio
    async def test_reload_missing_raises_keyerror(self, skill_dir):
        reg = SkillRegistry([skill_dir])
        await reg.scan()
        with pytest.raises(KeyError):
            await reg.reload("ghost-skill")

    @pytest.mark.asyncio
    async def test_add_dir(self, tmp_path, skill_dir, skill_file):
        # First dir has tdd-orchestrator; a second dir holds another skill.
        second_dir = tmp_path / "more-skills"
        second_dir.mkdir()
        (second_dir / "extra.md").write_text(
            "---\nname: extra\ndescription: from second dir\n---\n\nExtra.\n"
        )
        reg = SkillRegistry([skill_dir])
        await reg.scan()
        # extra is not yet visible — only skill_dir was scanned
        with pytest.raises(KeyError):
            await reg.load("extra")
        # After adding the second dir, extra becomes loadable
        reg.add_dir(second_dir)
        sk = await reg.load("extra")
        assert sk.name == "extra"
        assert sk.description == "from second dir"

    @pytest.mark.asyncio
    async def test_scan_nonexistent_dir(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        reg = SkillRegistry([missing])
        skills = await reg.scan()
        assert skills == []
        # list() auto-scans when cache is empty; still empty
        assert await reg.list() == []


# ── TestSkillLayers ─────────────────────────────────────────────────────


def _write_skill(d, name, *, description="", body="body"):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"
    )


class TestSkillLayers:
    def _reg(self, tmp_path):
        from src.runtime.harness.skill_store import FilesystemSkillStore

        user_dir = tmp_path / "user"
        project_dir = tmp_path / "agents" / "skills"
        store = FilesystemSkillStore(tmp_path / "store")
        return (
            SkillRegistry(user_dir=user_dir, project_dir=project_dir, store=store),
            user_dir,
            project_dir,
            store,
        )

    @pytest.mark.asyncio
    async def test_scan_tags_layers(self, tmp_path):
        reg, user_dir, project_dir, _ = self._reg(tmp_path)
        _write_skill(user_dir, "u-skill")
        _write_skill(project_dir, "p-skill")
        await reg.scan()
        by_name = {s.name: s for s in await reg.list()}
        assert by_name["u-skill"].layer == "user"
        assert by_name["u-skill"].editable is False
        assert by_name["p-skill"].layer == "project"

    @pytest.mark.asyncio
    async def test_list_merges_workspace_layer(self, tmp_path):
        reg, user_dir, project_dir, store = self._reg(tmp_path)
        _write_skill(project_dir, "p-skill")
        await reg.scan()
        await store.save(
            "ws-1",
            SkillPackage(name="w-skill", instructions="ws body"),
        )
        names = {s.name: s.layer for s in await reg.list("ws-1")}
        assert names["p-skill"] == "project"
        assert names["w-skill"] == "workspace"
        # Without a workspace_id the workspace layer is not included.
        names_global = {s.name for s in await reg.list()}
        assert "w-skill" not in names_global

    @pytest.mark.asyncio
    async def test_project_overrides_user(self, tmp_path):
        reg, user_dir, project_dir, _ = self._reg(tmp_path)
        _write_skill(user_dir, "shared", body="user body")
        _write_skill(project_dir, "shared", body="project body")
        await reg.scan()
        loaded = await reg.load("shared")
        assert loaded.layer == "project"
        assert "project body" in loaded.instructions

    @pytest.mark.asyncio
    async def test_workspace_overrides_directory_layers(self, tmp_path):
        reg, user_dir, project_dir, store = self._reg(tmp_path)
        _write_skill(project_dir, "shared", body="project body")
        await reg.scan()
        await store.save(
            "ws-1",
            SkillPackage(name="shared", instructions="workspace body"),
        )
        loaded = await reg.load("shared", "ws-1")
        assert loaded.layer == "workspace"
        assert "workspace body" in loaded.instructions
        # Different workspace does not see it → falls back to project.
        fallback = await reg.load("shared", "ws-other")
        assert fallback.layer == "project"

    @pytest.mark.asyncio
    async def test_get_readonly_ignores_store(self, tmp_path):
        reg, user_dir, project_dir, store = self._reg(tmp_path)
        _write_skill(project_dir, "p-skill")
        await reg.scan()
        await store.save("ws-1", SkillPackage(name="w-skill"))
        ro = await reg.get_readonly("p-skill")
        assert ro.layer == "project"
        with pytest.raises(KeyError):
            await reg.get_readonly("w-skill")

    @pytest.mark.asyncio
    async def test_legacy_project_dir_scanned(self, tmp_path):
        # Backward compatibility: an extra legacy dir added via add_dir is
        # scanned as the project layer (this is how registry.create wires the
        # old ``.agents/skills`` alongside ``agents/skills``).
        project_dir = tmp_path / "agents" / "skills"
        legacy_dir = tmp_path / ".agents" / "skills"
        _write_skill(legacy_dir, "legacy-skill")
        reg = SkillRegistry(user_dir=None, project_dir=project_dir, store=None)
        reg.add_dir(legacy_dir, "project")
        await reg.scan()
        names = {s.name for s in await reg.list()}
        assert "legacy-skill" in names

    @pytest.mark.asyncio
    async def test_nested_skill_dir_with_skill_md(self, tmp_path):
        # Real-world layout: each skill is a folder containing SKILL.md
        # (e.g. ~/.agents/skills/fastapi-python/SKILL.md). The scanner must
        # discover it and name it after the folder, not "SKILL".
        reg, user_dir, project_dir, _ = self._reg(tmp_path)
        skill_dir = project_dir / "fastapi-python"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: fastapi-python\ndescription: FastAPI tips\n---\n\nuse pydantic\n"
        )
        await reg.scan()
        by_name = {s.name: s for s in await reg.list()}
        assert "fastapi-python" in by_name
        assert by_name["fastapi-python"].layer == "project"
        assert by_name["fastapi-python"].file_path.endswith("SKILL.md")
        # Cold-load path also resolves the nested layout.
        loaded = await reg.load("fastapi-python")
        assert loaded.layer == "project"
        assert "use pydantic" in loaded.instructions
        # Reload works for nested directories too.
        reloaded = await reg.reload("fastapi-python")
        assert reloaded.name == "fastapi-python"
