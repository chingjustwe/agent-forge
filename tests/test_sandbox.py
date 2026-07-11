"""P3a-P1: Tests for SandboxManager — subprocess isolation for tool execution.

Covers:
- SandboxManager.execute: echo, args joining, timeout, output truncation,
  failure exit codes, cwd override, default mode
- SandboxManager policy: set/get per-workspace policy, default fallback
- SandboxManager.execute_tool: missing command error, command-from-args
  dispatch, policy timeout capping
"""
import pytest

from src.runtime.harness.agents import AgentDefinition
from src.runtime.harness.context import HarnessContext
from src.runtime.harness.sandbox import SandboxManager, SandboxPolicy
from src.runtime.harness.tool_engine import ToolDefinition, ToolResult


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_ctx():
    agent = AgentDefinition(id="", name="test", workspace_id="ws-1", adapter="deepagents")
    return HarnessContext(
        workspace_id="ws-1",
        user_id="u-1",
        session_id="s-1",
        trace_id="t-1",
        agent=agent,
    )


def _make_tool(name="custom_cmd", metadata=None):
    return ToolDefinition(
        name=name,
        description="test",
        input_schema={},
        source="custom",
        requires_sandbox=True,
    )


# ── SandboxManager.execute ──────────────────────────────────────────────


class TestSandboxManager:
    @pytest.mark.asyncio
    async def test_execute_echo(self):
        sandbox = SandboxManager()
        result = await sandbox.execute("echo hello")
        assert "hello" in result.stdout
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_execute_with_args(self):
        sandbox = SandboxManager()
        result = await sandbox.execute("echo", args=["a", "b"])
        assert "a b" in result.stdout

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        sandbox = SandboxManager()
        result = await sandbox.execute("sleep 10", timeout=1)
        assert "timed out" in result.stderr
        assert result.exit_code == -1

    @pytest.mark.asyncio
    async def test_execute_truncation(self):
        sandbox = SandboxManager()
        # Output 2 MB of 'x' — exceeds the 1 MB default cap.
        result = await sandbox.execute("python3 -c \"print('x'*2000000)\"")
        assert result.truncated is True

    @pytest.mark.asyncio
    async def test_execute_failure(self):
        sandbox = SandboxManager()
        result = await sandbox.execute("false")
        assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_execute_cwd(self):
        sandbox = SandboxManager()
        result = await sandbox.execute("pwd", cwd="/tmp")
        assert "/tmp" in result.stdout

    def test_default_mode_is_subprocess(self):
        sandbox = SandboxManager()
        assert sandbox.mode == "subprocess"

    def test_set_policy_and_get_policy(self):
        sandbox = SandboxManager()
        policy = SandboxPolicy(workspace_id="ws-1", max_timeout=10)
        sandbox.set_policy("ws-1", policy)
        assert sandbox.get_policy("ws-1") is policy

    def test_get_policy_returns_default_for_unknown_workspace(self):
        default = SandboxPolicy(max_timeout=99)
        sandbox = SandboxManager(default_policy=default)
        assert sandbox.get_policy("unknown-ws") is default


# ── SandboxManager.execute_tool ─────────────────────────────────────────


class TestSandboxExecuteTool:
    @pytest.mark.asyncio
    async def test_execute_tool_no_command(self):
        sandbox = SandboxManager()
        tool = _make_tool()
        ctx = _make_ctx()
        result = await sandbox.execute_tool(tool, {}, ctx)
        assert result.error is not None
        assert "No 'command'" in result.error

    @pytest.mark.asyncio
    async def test_execute_tool_with_command(self):
        sandbox = SandboxManager()
        tool = _make_tool()
        ctx = _make_ctx()
        result = await sandbox.execute_tool(
            tool, {"command": "echo", "x": "hello"}, ctx,
        )
        assert result.error is None
        assert "hello" in result.output
        assert result.metadata["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_execute_tool_respects_policy_timeout(self):
        sandbox = SandboxManager()
        sandbox.set_policy(
            "ws-1", SandboxPolicy(workspace_id="ws-1", max_timeout=2),
        )
        tool = _make_tool()
        ctx = _make_ctx()
        # Requested timeout=10 but policy caps it to 2; sleep 5 would
        # succeed with 10s but times out under the capped 2s.
        result = await sandbox.execute_tool(
            tool, {"command": "sleep 5", "timeout": 10}, ctx,
        )
        assert result.metadata["exit_code"] == -1
        assert "timed out" in result.error
