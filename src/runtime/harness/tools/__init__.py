"""P3a: Builtin tool registry.

Defines ``BUILTIN_TOOLS`` (handler dispatch table) and
``BUILTIN_TOOL_DEFINITIONS`` (ToolDefinition list for ToolRegistry).

Each builtin handler has the signature ``async def fn(args: dict, ctx:
HarnessContext) -> ToolResult | dict | str`` and is registered in
``BUILTIN_TOOLS`` keyed by its ``handler`` identifier (e.g. ``"todo.write"``).
"""
from __future__ import annotations

from src.runtime.harness.tool_engine import ToolDefinition

from .builtin import (
    fs as _fs,
    memory as _memory,
    shell as _shell,
    todo as _todo,
    compact as _compact,
)


# ── Handler dispatch table ──────────────────────────────────────────────
# key: tool.handler value → async callable(args, ctx) -> result
BUILTIN_HANDLERS = {
    "todo.write": _todo.write,
    "todo.read": _todo.read,
    "compact.run": _compact.run,
    "memory.save": _memory.save,
    "memory.recall": _memory.recall,
    "shell.exec": _shell.exec,
    "fs.ls": _fs.ls,
    "fs.read": _fs.read,
    "fs.write": _fs.write,
    "fs.edit": _fs.edit,
    "fs.glob": _fs.glob,
    "fs.grep": _fs.grep,
}


# ── Tool definitions (registered into ToolRegistry at startup) ──────────
BUILTIN_TOOL_DEFINITIONS: list[ToolDefinition] = [
    ToolDefinition(
        name="todo_write",
        description="Replace the agent's task list. Each task has content + status (pending/in_progress/completed).",
        input_schema={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                        },
                        "required": ["content", "status"],
                    },
                }
            },
            "required": ["todos"],
        },
        handler="todo.write",
    ),
    ToolDefinition(
        name="todo_read",
        description="Read the agent's current task list.",
        input_schema={"type": "object", "properties": {}},
        handler="todo.read",
    ),
    ToolDefinition(
        name="compact",
        description="Compress conversation history. Replaces prior messages with a summary message; the agent should pass a concise recap.",
        input_schema={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Recap of the conversation so far, retained as a single system message.",
                }
            },
            "required": ["summary"],
        },
        handler="compact.run",
    ),
    ToolDefinition(
        name="save_memory",
        description=(
            "Persist a memory record for long-term recall. "
            "CALL THIS PROACTIVELY whenever the user shares durable personal "
            "details they would likely want you to remember in future "
            "conversations — e.g. their name, role, title, preferences, "
            "constraints, goals, or any stable fact about them. "
            "Use scope='user' with memory_type='profile' for stable user "
            "traits/preferences (these are always injected into your system "
            "prompt on every turn); use scope='user' with memory_type="
            "'episodic' for specific conversational facts recalled by topic. "
            "Scope: session | user | workspace | agent. memory_type: 'profile' "
            "for user prefs/config (always injected into prompt) or 'episodic' "
            "for conversational facts (recalled by topic query)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "content": {"type": "string"},
                "scope": {
                    "type": "string",
                    "enum": ["session", "user", "workspace", "agent"],
                    "default": "session",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["profile", "episodic"],
                    "default": "episodic",
                },
            },
            "required": ["content"],
        },
        handler="memory.save",
    ),
    ToolDefinition(
        name="recall_memory",
        description="Retrieve memories by query. Defaults to session scope. Use memory_type='profile' to fetch only always-inject profile records, 'episodic' for topic-relevant facts only, or omit to search all types.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "scope": {
                    "type": "string",
                    "enum": ["session", "user", "workspace", "agent"],
                    "default": "session",
                },
                "limit": {"type": "integer", "default": 5},
                "memory_type": {
                    "type": "string",
                    "enum": ["profile", "episodic"],
                },
            },
            "required": ["query"],
        },
        handler="memory.recall",
    ),
    ToolDefinition(
        name="shell_exec",
        description="Execute a shell command via the sandbox (subprocess, timeout, output cap). Network egress disabled by default.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "default": 30},
            },
            "required": ["command"],
        },
        handler="shell.exec",
        requires_sandbox=False,  # P0: direct subprocess; P1 routes via SandboxManager
    ),
    ToolDefinition(
        name="ls",
        description="List directory contents within the workspace root.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
        },
        handler="fs.ls",
    ),
    ToolDefinition(
        name="read",
        description="Read a file within the workspace root. Returns content (truncated to 200KB).",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "default": 1},
                "limit": {"type": "integer"},
            },
            "required": ["path"],
        },
        handler="fs.read",
    ),
    ToolDefinition(
        name="write",
        description="Write a file within the workspace root. Overwrites if exists.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        handler="fs.write",
    ),
    ToolDefinition(
        name="edit",
        description="String-replace edit within a file in the workspace root. Fails if old_string is not unique.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        handler="fs.edit",
    ),
    ToolDefinition(
        name="glob",
        description="Glob-pattern file search within the workspace root. Returns matching paths.",
        input_schema={
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
        handler="fs.glob",
    ),
    ToolDefinition(
        name="grep",
        description="Regex content search within the workspace root. Returns matching file paths + line numbers.",
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "default": "."},
            },
            "required": ["pattern"],
        },
        handler="fs.grep",
    ),
]
