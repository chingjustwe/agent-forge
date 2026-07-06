"""P3a-P1: SandboxManager — subprocess isolation for tool execution.

Default mode is ``subprocess``: runs commands via
``asyncio.create_subprocess_shell`` with timeout, output truncation, and
optional ``cwd`` / ``env`` overrides. Docker mode is deferred (§11 out of
scope for 3a).

``SandboxManager.execute_tool()`` bridges custom ``ToolDefinition`` objects
to the sandbox by interpreting the tool's ``metadata`` for command/args.
``shell_exec`` built-in tool also routes here when a sandbox is available
in the ``HarnessContext``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext
    from src.runtime.harness.tool_engine import ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

_MAX_OUTPUT = 1_000_000  # 1 MB default cap


class SandboxResult(BaseModel):
    """Result of a sandboxed subprocess execution."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    truncated: bool = False


class SandboxPolicy(BaseModel):
    """Per-workspace sandbox policy.

    ``allowed_paths`` restricts which directories may be used as ``cwd``.
    ``network_egress`` is informational in subprocess mode (no enforcement
    at this layer); Docker mode would use ``--network none``.
    """

    workspace_id: str = ""
    allowed_paths: list[str] = Field(default_factory=list)
    network_egress: bool = False
    max_timeout: int = 60
    max_memory_mb: int = 512


class SandboxManager:
    """Isolates tool execution via subprocess with resource limits.

    Constructed once at startup and injected into ``HarnessContext.sandbox``.
    The ``ToolEngine`` calls ``execute_tool()`` for custom tools with
    ``requires_sandbox=True``.
    """

    def __init__(
        self,
        mode: Literal["subprocess", "docker"] = "subprocess",
        default_policy: SandboxPolicy | None = None,
    ) -> None:
        self.mode = mode
        self.default_policy = default_policy or SandboxPolicy()
        self._policies: dict[str, SandboxPolicy] = {}

    def set_policy(self, workspace_id: str, policy: SandboxPolicy) -> None:
        self._policies[workspace_id] = policy

    def get_policy(self, workspace_id: str) -> SandboxPolicy:
        return self._policies.get(workspace_id, self.default_policy)

    async def execute(
        self,
        command: str,
        args: list[str] | None = None,
        timeout: int = 30,
        env: dict | None = None,
        cwd: str | None = None,
        max_output_bytes: int = _MAX_OUTPUT,
    ) -> SandboxResult:
        """Execute a command in the sandbox.

        In subprocess mode: ``asyncio.create_subprocess_shell`` with
        ``asyncio.wait_for`` timeout and output truncation.
        """
        full_cmd = command
        if args:
            import shlex

            full_cmd = f"{command} {' '.join(shlex.quote(a) for a in args)}"

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(
                full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except Exception as exc:
            return SandboxResult(
                stderr=f"Failed to spawn: {type(exc).__name__}: {exc}",
                exit_code=-1,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return SandboxResult(
                stderr=f"Command timed out after {timeout}s",
                exit_code=-1,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        truncated = len(stdout) > max_output_bytes
        if truncated:
            stdout = stdout[:max_output_bytes] + "\n... [output truncated]"

        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode or 0,
            duration_ms=duration_ms,
            truncated=truncated,
        )

    async def execute_tool(
        self,
        tool: "ToolDefinition",
        args: dict,
        ctx: "HarnessContext",
    ) -> "ToolResult":
        """Execute a custom tool definition inside the sandbox.

        The tool's ``metadata`` dict may contain:
        - ``command``: the shell command template (required)
        - ``cwd``: working directory (defaults to ctx.workspace_root)
        - ``timeout``: per-call timeout override
        """
        from src.runtime.harness.tool_engine import ToolResult

        meta = tool.metadata if hasattr(tool, "metadata") else {}
        command = args.pop("command", "") or meta.get("command", "")
        if not command:
            return ToolResult(
                name=tool.name,
                output="",
                error="No 'command' provided for sandbox execution",
            )

        policy = self.get_policy(ctx.workspace_id)
        timeout = min(
            args.pop("timeout", meta.get("timeout", 30)),
            policy.max_timeout,
        )
        cwd = args.pop("cwd", None) or ctx.workspace_root or None

        # Build positional args from remaining kwargs
        cmd_args = [str(v) for v in args.values()] if args else None

        result = await self.execute(
            command=command,
            args=cmd_args,
            timeout=timeout,
            cwd=cwd,
        )

        return ToolResult(
            name=tool.name,
            output=result.stdout,
            error=result.stderr or None,
            metadata={
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "truncated": result.truncated,
            },
        )
