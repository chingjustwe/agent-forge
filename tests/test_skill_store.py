"""Tests for SkillStore backends (Skills layers spec).

Covers:
- DBSkillStore: save / get / list / delete / exists, upsert on same name,
  name validation.
- FilesystemSkillStore: write file → readable via list/get, delete removes
  file, roundtrip through markdown frontmatter.
- build_skill_store factory: backend selection.
"""
from __future__ import annotations

import pytest

from src.runtime.harness.skill_store import (
    DBSkillStore,
    FilesystemSkillStore,
    build_skill_store,
)
from src.runtime.harness.skills import SkillPackage


def _pkg(name: str, **kw) -> SkillPackage:
    return SkillPackage(name=name, **kw)


# ── DBSkillStore ─────────────────────────────────────────────────────────


class TestDBSkillStore:
    @pytest.mark.asyncio
    async def test_save_get_roundtrip(self):
        store = DBSkillStore()
        ws = "ws-db-1"
        saved = await store.save(
            ws,
            _pkg(
                "alpha",
                description="first",
                instructions="do alpha",
                tools=["t1", "t2"],
                required_memory=True,
                version="2.0",
            ),
        )
        assert saved.layer == "workspace"
        assert saved.editable is True
        assert saved.workspace_id == ws

        got = await store.get(ws, "alpha")
        assert got is not None
        assert got.description == "first"
        assert got.instructions == "do alpha"
        assert got.tools == ["t1", "t2"]
        assert got.required_memory is True
        assert got.version == "2.0"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self):
        store = DBSkillStore()
        assert await store.get("ws-db-2", "ghost") is None

    @pytest.mark.asyncio
    async def test_upsert_same_name(self):
        store = DBSkillStore()
        ws = "ws-db-3"
        await store.save(ws, _pkg("beta", description="v1"))
        await store.save(ws, _pkg("beta", description="v2", instructions="new"))
        got = await store.get(ws, "beta")
        assert got.description == "v2"
        assert got.instructions == "new"
        # Only one row (no duplicate).
        rows = await store.list(ws)
        assert len([s for s in rows if s.name == "beta"]) == 1

    @pytest.mark.asyncio
    async def test_list_scoped_to_workspace(self):
        store = DBSkillStore()
        await store.save("ws-db-4a", _pkg("only-a"))
        await store.save("ws-db-4b", _pkg("only-b"))
        names_a = {s.name for s in await store.list("ws-db-4a")}
        names_b = {s.name for s in await store.list("ws-db-4b")}
        assert "only-a" in names_a and "only-b" not in names_a
        assert "only-b" in names_b and "only-a" not in names_b

    @pytest.mark.asyncio
    async def test_delete(self):
        store = DBSkillStore()
        ws = "ws-db-5"
        await store.save(ws, _pkg("gamma"))
        assert await store.exists(ws, "gamma") is True
        assert await store.delete(ws, "gamma") is True
        assert await store.exists(ws, "gamma") is False
        # Deleting again is a no-op → False.
        assert await store.delete(ws, "gamma") is False

    @pytest.mark.asyncio
    async def test_invalid_name_rejected(self):
        store = DBSkillStore()
        with pytest.raises(ValueError):
            await store.save("ws-db-6", _pkg("Bad Name!"))


# ── FilesystemSkillStore ─────────────────────────────────────────────────


class TestFilesystemSkillStore:
    @pytest.mark.asyncio
    async def test_save_creates_file_and_reads_back(self, tmp_path):
        store = FilesystemSkillStore(tmp_path)
        ws = "ws-fs-1"
        await store.save(
            ws,
            _pkg(
                "delta",
                description="fs skill",
                instructions="body text",
                tools=["x"],
                version="1.2",
            ),
        )
        # File exists at <root>/<ws>/<name>.md
        assert (tmp_path / ws / "delta.md").is_file()

        got = await store.get(ws, "delta")
        assert got is not None
        assert got.description == "fs skill"
        assert got.instructions == "body text"
        assert got.tools == ["x"]
        assert got.version == "1.2"
        assert got.layer == "workspace"
        assert got.editable is True

    @pytest.mark.asyncio
    async def test_list_reads_all(self, tmp_path):
        store = FilesystemSkillStore(tmp_path)
        ws = "ws-fs-2"
        await store.save(ws, _pkg("one"))
        await store.save(ws, _pkg("two"))
        names = {s.name for s in await store.list(ws)}
        assert names == {"one", "two"}

    @pytest.mark.asyncio
    async def test_list_missing_dir_empty(self, tmp_path):
        store = FilesystemSkillStore(tmp_path)
        assert await store.list("nope") == []

    @pytest.mark.asyncio
    async def test_delete_removes_file(self, tmp_path):
        store = FilesystemSkillStore(tmp_path)
        ws = "ws-fs-3"
        await store.save(ws, _pkg("eps"))
        assert await store.delete(ws, "eps") is True
        assert not (tmp_path / ws / "eps.md").exists()
        assert await store.delete(ws, "eps") is False

    @pytest.mark.asyncio
    async def test_invalid_name_rejected(self, tmp_path):
        store = FilesystemSkillStore(tmp_path)
        with pytest.raises(ValueError):
            await store.save("ws-fs-4", _pkg("NOPE!"))


# ── build_skill_store factory ────────────────────────────────────────────


class TestBuildSkillStore:
    def test_default_is_db(self):
        assert isinstance(build_skill_store("db"), DBSkillStore)

    def test_unknown_falls_back_to_db(self):
        assert isinstance(build_skill_store("weird"), DBSkillStore)

    def test_filesystem(self, tmp_path):
        store = build_skill_store("filesystem", fs_root=str(tmp_path))
        assert isinstance(store, FilesystemSkillStore)
