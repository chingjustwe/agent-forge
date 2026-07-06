"""shell_exec — subprocess execution.

P1: routes through ``SandboxManager`` when available in the context.
Falls back to direct ``asyncio.create_subprocess_shell`` when no
sandbox is configured (e.g., in tests). The handler signature stays
the same so adapters do not need to change.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext


_MAX_OUTPUT = 1_000_000  # 1 MB cap


async def exec(args: dict, ctx: "HarnessContext") -> dict:
    command = args.get("command", "")
    if not isinstance(command, str) or not command.strip():
        return {"output": "", "error": "command must be a non-empty string"}

    timeout = args.get("timeout", 30)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 30
    timeout = max(1, min(timeout, 300))

    # P1: route through SandboxManager if available
    sandbox = getattr(ctx, "sandbox", None)
    if sandbox is not None:
        result = await sandbox.execute(
            command=command,
            timeout=timeout,
            cwd=ctx.workspace_root or None,
        )
        return {
            "output": result.stdout,
            "error": result.stderr or None,
            "metadata": {
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "truncated": result.truncated,
            },
        }

    # Fallback: direct subprocess (P0 behavior)
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=ctx.workspace_root,
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
            return {
                "output": "",
                "error": f"Command timed out after {timeout}s",
                "metadata": {
                    "timeout": timeout,
                    "duration_ms": int((time.monotonic() - start) * 1000),
                },
            }
    except Exception as exc:
        return {
            "output": "",
            "error": f"Failed to spawn command: {type(exc).__name__}: {exc}",
        }

    duration_ms = int((time.monotonic() - start) * 1000)
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    truncated = len(stdout) > _MAX_OUTPUT
    if truncated:
        stdout = stdout[:_MAX_OUTPUT] + "\n... [output truncated]"

    return {
        "output": stdout,
        "error": stderr or None,
        "metadata": {
            "exit_code": proc.returncode,
            "duration_ms": duration_ms,
            "truncated": truncated,
        },
    }
