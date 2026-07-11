"""P3a: Tests for ToolRegistry, ToolEngine, and builtin tool handlers.

Covers:
- ToolRegistry: register/get/list/unregister, workspace-scoped shadowing
- ToolEngine: is_allowed, available_tools, execute routing, permission
  + not-found errors, builtin handler dispatch
- Builtin handlers: todo.write/read, compact, memory.save/recall,
  shell.exec, fs.ls/read/write/edit/glob/grep (path containment, happy path)
"""
import os
import tempfile

import pytest

from src.runtime.harness.agents import AgentDefinition
from src.runtime.harness.context import HarnessContext
from src.runtime.harness.tool_engine import (
    ToolDefinition,
    ToolEngine,
    ToolError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolRegistry,
)
from src.runtime.harness.tools import BUILTIN_HANDLERS, BUILTIN_TOOL_DEFINITIONS
from src.runtime.harness.tools.builtin import compact as _compact
from src.runtime.harness.tools.builtin import fs as _fs
from src.runtime.harness.tools.builtin import memory as _memory
from src.runtime.harness.tools.builtin import shell as _shell
from src.runtime.harness.tools.builtin import todo as _todo


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_ctx(
    *,
    workspace_root: str = "",
    workspace_id: str = "ws-te",
    user_id: str = "u-te",
    session_id: str = "s-te",
    agent_id: str = "agent-te",
) -> HarnessContext:
    """Build a minimal HarnessContext for tool execution tests."""
    agent = AgentDefinition(
        id=agent_id,
        name="test-agent",
        workspace_id=workspace_id,
        adapter="deepagents",
    )
    return HarnessContext(
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        trace_id="trace-te",
        agent=agent,
        workspace_root=workspace_root,
    )


def _make_engine(
    allowed_tools: list[str],
    registry: ToolRegistry | None = None,
) -> tuple[ToolEngine, ToolRegistry]:
    """Build a ToolEngine with builtin handlers + a fresh registry."""
    reg = registry or ToolRegistry()
    # Register all builtins so tests can pick which to allow.
    for td in BUILTIN_TOOL_DEFINITIONS:
        if reg.get(td.name) is None:
            reg.register(td)
    engine = ToolEngine(
        registry=reg,
        allowed_tools=allowed_tools,
        builtin_handlers=BUILTIN_HANDLERS,
    )
    return engine, reg


# ── ToolRegistry ─────────────────────────────────────────────────────────


class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        td = ToolDefinition(
            name="my_tool", description="d", input_schema={},
            handler="x",
        )
        reg.register(td)
        assert reg.get("my_tool") is td

    def test_get_missing_returns_none(self):
        reg = ToolRegistry()
        assert reg.get("nope") is None

    def test_unregister(self):
        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="t", description="d", input_schema={}, handler="x",
        ))
        assert reg.unregister("t") is True
        assert reg.get("t") is None
        assert reg.unregister("t") is False

    def test_workspace_scoped_shadows_builtin(self):
        reg = ToolRegistry()
        builtin = ToolDefinition(
            name="ls", description="builtin ls", input_schema={},
            handler="fs.ls", workspace_id=None,
        )
        custom = ToolDefinition(
            name="ls", description="custom ls", input_schema={},
            handler="custom.ls", workspace_id="ws-1",
        )
        reg.register(builtin)
        reg.register(custom)
        # Workspace lookup returns the shadowing custom tool.
        assert reg.get("ls", workspace_id="ws-1") is custom
        # Other workspace falls back to builtin.
        assert reg.get("ls", workspace_id="ws-2") is builtin
        # No workspace_id returns builtin.
        assert reg.get("ls") is builtin

    def test_list_includes_builtins_and_workspace_scoped(self):
        reg = ToolRegistry()
        reg.register(ToolDefinition(
            name="a", description="da", input_schema={}, handler="x",
        ))
        reg.register(ToolDefinition(
            name="b", description="db", input_schema={}, handler="x",
            workspace_id="ws-1",
        ))
        all_for_ws = reg.list("ws-1")
        names = {t.name for t in all_for_ws}
        assert names == {"a", "b"}
        # Another workspace only sees builtins.
        other = {t.name for t in reg.list("ws-2")}
        assert other == {"a"}

    def test_save_memory_description_is_proactive(self):
        """save_memory must instruct the agent to call it proactively when
        the user shares personal details — otherwise the model never stores
        memory on its own (regression for 'agent doesn't remember my name')."""
        save_def = next(
            d for d in BUILTIN_TOOL_DEFINITIONS if d.name == "save_memory"
        )
        desc = save_def.description.lower()
        assert "proactiv" in desc
        assert "name" in desc
        assert "profile" in desc
        assert "episodic" in desc


# ── ToolEngine ───────────────────────────────────────────────────────────


class TestToolEngine:
    @pytest.mark.asyncio
    async def test_is_allowed(self):
        engine, _ = _make_engine(["todo_write", "ls"])
        assert engine.is_allowed("todo_write") is True
        assert engine.is_allowed("ls") is True
        assert engine.is_allowed("shell_exec") is False

    @pytest.mark.asyncio
    async def test_available_tools_respects_whitelist(self):
        engine, _ = _make_engine(["todo_write", "ls"])
        avail = engine.available_tools("ws-1")
        names = {t.name for t in avail}
        assert names == {"todo_write", "ls"}

    @pytest.mark.asyncio
    async def test_execute_permission_denied(self):
        engine, _ = _make_engine(["todo_write"])
        ctx = _make_ctx()
        with pytest.raises(ToolPermissionError, match="not allowed"):
            await engine.execute("shell_exec", {}, ctx)

    @pytest.mark.asyncio
    async def test_execute_not_registered(self):
        engine, _ = _make_engine(["bogus_tool"])
        ctx = _make_ctx()
        with pytest.raises(ToolNotFoundError, match="not registered"):
            await engine.execute("bogus_tool", {}, ctx)

    @pytest.mark.asyncio
    async def test_execute_builtin_dispatches_handler(self):
        engine, _ = _make_engine(["todo_write"])
        ctx = _make_ctx()
        result = await engine.execute(
            "todo_write",
            {"todos": [{"content": "task1", "status": "pending"}]},
            ctx,
        )
        assert result.error is None
        assert "Replaced task list" in result.output
        # Verify state was written to working_memory.
        assert ctx.working_memory["todos"][0]["content"] == "task1"


# ── Builtin handlers ─────────────────────────────────────────────────────


class TestTodoHandlers:
    @pytest.mark.asyncio
    async def test_write_and_read(self):
        ctx = _make_ctx()
        await _todo.write(
            {"todos": [
                {"content": "a", "status": "pending"},
                {"content": "b", "status": "completed"},
            ]},
            ctx,
        )
        result = await _todo.read({}, ctx)
        import json
        todos = json.loads(result["output"])
        assert len(todos) == 2
        assert todos[0]["content"] == "a"
        assert todos[1]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_write_rejects_invalid_status(self):
        ctx = _make_ctx()
        result = await _todo.write(
            {"todos": [{"content": "x", "status": "bogus"}]},
            ctx,
        )
        assert result["error"]
        assert "pending" in result["error"]

    @pytest.mark.asyncio
    async def test_write_rejects_non_array(self):
        ctx = _make_ctx()
        result = await _todo.write({"todos": "not-a-list"}, ctx)
        assert "must be an array" in result["error"]

    @pytest.mark.asyncio
    async def test_read_empty(self):
        ctx = _make_ctx()
        result = await _todo.read({}, ctx)
        import json
        assert json.loads(result["output"]) == []


class TestCompactHandler:
    @pytest.mark.asyncio
    async def test_run_stores_summary(self):
        ctx = _make_ctx()
        result = await _compact.run(
            {"summary": "We discussed testing strategy."},
            ctx,
        )
        assert result.get("error") is None
        assert ctx.working_memory["compact_summary"] == "We discussed testing strategy."
        assert ctx.working_memory["compacted"] is True

    @pytest.mark.asyncio
    async def test_run_rejects_empty_summary(self):
        ctx = _make_ctx()
        result = await _compact.run({"summary": "  "}, ctx)
        assert "non-empty" in result["error"]


class TestMemoryHandlers:
    @pytest.mark.asyncio
    async def test_save_and_recall(self):
        ctx = _make_ctx()
        await _memory.save(
            {"content": "The user likes tea", "scope": "session"},
            ctx,
        )
        await _memory.save(
            {"content": "The user dislikes coffee", "scope": "session"},
            ctx,
        )
        result = await _memory.recall({"query": "tea", "scope": "session"}, ctx)
        assert result.get("error") is None
        assert result["metadata"]["count"] == 1
        assert "tea" in result["output"]

    @pytest.mark.asyncio
    async def test_save_rejects_invalid_scope(self):
        ctx = _make_ctx()
        result = await _memory.save(
            {"content": "x", "scope": "bogus"}, ctx,
        )
        assert "scope" in result["error"]

    @pytest.mark.asyncio
    async def test_recall_empty(self):
        ctx = _make_ctx()
        result = await _memory.recall({"query": "anything"}, ctx)
        assert result["metadata"]["count"] == 0

    @pytest.mark.asyncio
    async def test_scope_id_resolution(self):
        """Memory records carry scope_id resolved from ctx identity."""
        ctx = _make_ctx(
            workspace_id="ws-mem", user_id="u-mem",
            session_id="s-mem", agent_id="a-mem",
        )
        await _memory.save({"content": "x", "scope": "session"}, ctx)
        await _memory.save({"content": "y", "scope": "user"}, ctx)
        await _memory.save({"content": "z", "scope": "workspace"}, ctx)
        await _memory.save({"content": "w", "scope": "agent"}, ctx)
        session_recs = ctx.working_memory["memory"]["session"]
        user_recs = ctx.working_memory["memory"]["user"]
        ws_recs = ctx.working_memory["memory"]["workspace"]
        agent_recs = ctx.working_memory["memory"]["agent"]
        assert session_recs[0]["scope_id"] == "s-mem"
        assert user_recs[0]["scope_id"] == "u-mem"
        assert ws_recs[0]["scope_id"] == "ws-mem"
        assert agent_recs[0]["scope_id"] == "a-mem"


class TestShellHandler:
    @pytest.mark.asyncio
    async def test_exec_echo(self, tmp_path):
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _shell_exec_helper("echo hello", ctx)
        assert result.get("error") is None
        assert "hello" in result["output"]
        assert result["metadata"]["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_exec_timeout(self, tmp_path):
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _shell_exec_helper("sleep 10", ctx, timeout=1)
        assert "timed out" in result["error"]

    @pytest.mark.asyncio
    async def test_exec_rejects_empty(self, tmp_path):
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _shell.exec({"command": ""}, ctx)
        assert "non-empty" in result["error"]


async def _shell_exec_helper(cmd: str, ctx, timeout: int = 30):
    """Thin wrapper to import shell.exec without clashing with builtin exec."""
    from src.runtime.harness.tools.builtin import shell as _shell
    return await _shell.exec({"command": cmd, "timeout": timeout}, ctx)


class TestFsHandlers:
    @pytest.mark.asyncio
    async def test_path_containment_rejects_dotdot(self, tmp_path):
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _fs.ls({"path": "../../../etc"}, ctx)
        assert "escapes workspace root" in result["error"]

    @pytest.mark.asyncio
    async def test_ls_lists_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("hi")
        (tmp_path / "b.txt").write_text("yo")
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _fs.ls({"path": "."}, ctx)
        assert result.get("error") is None
        assert "a.txt" in result["output"]
        assert "b.txt" in result["output"]
        assert result["metadata"]["count"] == 2

    @pytest.mark.asyncio
    async def test_read_returns_content(self, tmp_path):
        (tmp_path / "note.md").write_text("line1\nline2\nline3\n")
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _fs.read({"path": "note.md"}, ctx)
        assert result.get("error") is None
        assert "line1" in result["output"]
        assert "line3" in result["output"]
        assert result["metadata"]["total_lines"] == 3

    @pytest.mark.asyncio
    async def test_read_with_offset_and_limit(self, tmp_path):
        (tmp_path / "n.txt").write_text("l1\nl2\nl3\nl4\nl5\n")
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _fs.read({"path": "n.txt", "offset": 2, "limit": 2}, ctx)
        assert "l2" in result["output"]
        assert "l3" in result["output"]
        assert "l1" not in result["output"]
        assert "l4" not in result["output"]

    @pytest.mark.asyncio
    async def test_write_creates_file(self, tmp_path):
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _fs.write(
            {"path": "new.txt", "content": "hello"}, ctx,
        )
        assert result.get("error") is None
        assert (tmp_path / "new.txt").read_text() == "hello"

    @pytest.mark.asyncio
    async def test_write_creates_nested_dirs(self, tmp_path):
        ctx = _make_ctx(workspace_root=str(tmp_path))
        await _fs.write(
            {"path": "sub/dir/file.txt", "content": "x"}, ctx,
        )
        assert (tmp_path / "sub" / "dir" / "file.txt").read_text() == "x"

    @pytest.mark.asyncio
    async def test_edit_replaces_unique_string(self, tmp_path):
        (tmp_path / "e.txt").write_text("foo bar baz")
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _fs.edit(
            {"path": "e.txt", "old_string": "bar", "new_string": "qux"},
            ctx,
        )
        assert result.get("error") is None
        assert (tmp_path / "e.txt").read_text() == "foo qux baz"

    @pytest.mark.asyncio
    async def test_edit_rejects_non_unique(self, tmp_path):
        (tmp_path / "dup.txt").write_text("x x x")
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _fs.edit(
            {"path": "dup.txt", "old_string": "x", "new_string": "y"},
            ctx,
        )
        assert "appears 3 times" in result["error"]

    @pytest.mark.asyncio
    async def test_edit_rejects_missing(self, tmp_path):
        (tmp_path / "m.txt").write_text("abc")
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _fs.edit(
            {"path": "m.txt", "old_string": "zzz", "new_string": "y"},
            ctx,
        )
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_glob_finds_matches(self, tmp_path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("x")
        (tmp_path / "c.txt").write_text("x")
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _fs.glob({"pattern": "*.py"}, ctx)
        assert result.get("error") is None
        assert "a.py" in result["output"]
        assert "b.py" in result["output"]
        assert "c.txt" not in result["output"]
        assert result["metadata"]["count"] == 2

    @pytest.mark.asyncio
    async def test_grep_finds_matches(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello world\nfoo bar\n")
        (tmp_path / "b.txt").write_text("nothing here\n")
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _fs.grep({"pattern": "foo"}, ctx)
        assert result.get("error") is None
        assert "a.txt" in result["output"]
        assert "b.txt" not in result["output"]
        assert result["metadata"]["hits"] == 1

    @pytest.mark.asyncio
    async def test_grep_invalid_regex(self, tmp_path):
        ctx = _make_ctx(workspace_root=str(tmp_path))
        result = await _fs.grep({"pattern": "(unclosed"}, ctx)
        assert "Invalid regex" in result["error"]
