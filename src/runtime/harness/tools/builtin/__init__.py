"""Builtin tool handlers.

Each module exposes async functions matching the handler identifiers in
``BUILTIN_HANDLERS``. Handlers receive ``(args: dict, ctx: HarnessContext)``
and return a ``ToolResult``, ``dict``, or ``str``.
"""
