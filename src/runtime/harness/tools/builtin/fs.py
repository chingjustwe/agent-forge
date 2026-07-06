"""ls / read / write / edit / glob / grep — workspace-scoped filesystem tools.

All paths are resolved against ``ctx.workspace_root`` and rejected if
they escape via ``..``. This is the only security boundary in P0; P1
adds ``SandboxManager`` with full policy enforcement.
"""
from __future__ import annotations

import fnmatch
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.runtime.harness.context import HarnessContext


_READ_CAP = 200_000  # 200 KB


# ── Path containment ────────────────────────────────────────────────────


def _resolve(ctx: "HarnessContext", rel: str) -> Path:
    """Resolve ``rel`` against ``ctx.workspace_root`` and enforce containment.

    Raises ``ValueError`` if the resolved path escapes the workspace root.
    """
    root = Path(ctx.workspace_root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Workspace root does not exist: {root}")

    target = (root / rel).resolve() if rel else root
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Path {rel!r} escapes workspace root"
        ) from exc
    return target


# ── ls ──────────────────────────────────────────────────────────────────


async def ls(args: dict, ctx: "HarnessContext") -> dict:
    rel = args.get("path", ".") or "."
    try:
        target = _resolve(ctx, rel)
    except (ValueError, FileNotFoundError) as exc:
        return {"output": "", "error": str(exc)}

    if not target.exists():
        return {"output": "", "error": f"Path not found: {rel}"}
    if not target.is_dir():
        return {"output": "", "error": f"Not a directory: {rel}"}

    try:
        entries = sorted(target.iterdir(), key=lambda p: p.name)
    except PermissionError as exc:
        return {"output": "", "error": f"Permission denied: {exc}"}

    rows: list[dict] = []
    for entry in entries:
        try:
            st = entry.stat()
            rows.append({
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": st.st_size if entry.is_file() else 0,
            })
        except OSError:
            continue

    lines = [f"{r['type'][0]} {r['size']:>10} {r['name']}" for r in rows]
    return {
        "output": "\n".join(lines) if lines else "(empty)",
        "metadata": {"count": len(rows)},
    }


# ── read ────────────────────────────────────────────────────────────────


async def read(args: dict, ctx: "HarnessContext") -> dict:
    rel = args.get("path", "")
    if not rel:
        return {"output": "", "error": "path is required"}
    try:
        target = _resolve(ctx, rel)
    except (ValueError, FileNotFoundError) as exc:
        return {"output": "", "error": str(exc)}

    if not target.exists():
        return {"output": "", "error": f"File not found: {rel}"}
    if not target.is_file():
        return {"output": "", "error": f"Not a file: {rel}"}

    offset = args.get("offset", 1)
    try:
        offset = max(1, int(offset))
    except (TypeError, ValueError):
        offset = 1
    limit = args.get("limit")
    try:
        limit = int(limit) if limit is not None else None
    except (TypeError, ValueError):
        limit = None

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except PermissionError as exc:
        return {"output": "", "error": f"Permission denied: {exc}"}

    truncated = False
    if len(text) > _READ_CAP:
        text = text[:_READ_CAP]
        truncated = True

    lines = text.splitlines()
    start_idx = offset - 1
    if limit is not None:
        lines = lines[start_idx:start_idx + limit]
    else:
        lines = lines[start_idx:]

    numbered = "\n".join(
        f"{i + offset:>6}→{line}" for i, line in enumerate(lines)
    )
    return {
        "output": numbered,
        "metadata": {
            "total_lines": len(text.splitlines()),
            "returned_lines": len(lines),
            "truncated": truncated,
        },
    }


# ── write ───────────────────────────────────────────────────────────────


async def write(args: dict, ctx: "HarnessContext") -> dict:
    rel = args.get("path", "")
    if not rel:
        return {"output": "", "error": "path is required"}
    content = args.get("content", "")
    if not isinstance(content, str):
        return {"output": "", "error": "content must be a string"}

    try:
        target = _resolve(ctx, rel)
    except (ValueError, FileNotFoundError) as exc:
        return {"output": "", "error": str(exc)}

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except (PermissionError, OSError) as exc:
        return {"output": "", "error": f"Write failed: {exc}"}

    return {
        "output": f"Wrote {len(content)} bytes to {rel}",
        "metadata": {"bytes": len(content), "path": rel},
    }


# ── edit ────────────────────────────────────────────────────────────────


async def edit(args: dict, ctx: "HarnessContext") -> dict:
    rel = args.get("path", "")
    if not rel:
        return {"output": "", "error": "path is required"}
    old_string = args.get("old_string")
    new_string = args.get("new_string", "")
    if old_string is None:
        return {"output": "", "error": "old_string is required"}
    if not isinstance(old_string, str) or not isinstance(new_string, str):
        return {"output": "", "error": "old_string and new_string must be strings"}
    if old_string == new_string:
        return {"output": "", "error": "old_string and new_string must differ"}

    try:
        target = _resolve(ctx, rel)
    except (ValueError, FileNotFoundError) as exc:
        return {"output": "", "error": str(exc)}

    if not target.exists():
        return {"output": "", "error": f"File not found: {rel}"}
    if not target.is_file():
        return {"output": "", "error": f"Not a file: {rel}"}

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except (PermissionError, OSError) as exc:
        return {"output": "", "error": f"Read failed: {exc}"}

    occurrences = text.count(old_string)
    if occurrences == 0:
        return {"output": "", "error": "old_string not found in file"}
    if occurrences > 1:
        return {
            "output": "",
            "error": f"old_string appears {occurrences} times; must be unique",
        }

    new_text = text.replace(old_string, new_string, 1)
    try:
        target.write_text(new_text, encoding="utf-8")
    except (PermissionError, OSError) as exc:
        return {"output": "", "error": f"Write failed: {exc}"}

    return {
        "output": f"Edited {rel}: 1 replacement",
        "metadata": {"path": rel, "occurrences": 1},
    }


# ── glob ────────────────────────────────────────────────────────────────


async def glob(args: dict, ctx: "HarnessContext") -> dict:
    pattern = args.get("pattern", "")
    if not pattern:
        return {"output": "", "error": "pattern is required"}

    try:
        root = Path(ctx.workspace_root).resolve()
        if not root.exists():
            return {"output": "", "error": f"Workspace root missing: {root}"}
    except Exception as exc:
        return {"output": "", "error": str(exc)}

    matches: list[str] = []
    for dirpath, _dirs, files in os.walk(root):
        for fname in files:
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root)
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(fname, pattern):
                matches.append(rel)
        # Also match directories themselves for patterns like "src/**"
        for dname in _dirs:
            full = os.path.join(dirpath, dname)
            rel = os.path.relpath(full, root)
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(dname, pattern):
                matches.append(rel + "/")

    matches.sort()
    return {
        "output": "\n".join(matches) if matches else "(no matches)",
        "metadata": {"count": len(matches)},
    }


# ── grep ────────────────────────────────────────────────────────────────


async def grep(args: dict, ctx: "HarnessContext") -> dict:
    pattern = args.get("pattern", "")
    if not pattern:
        return {"output": "", "error": "pattern is required"}
    rel_path = args.get("path", ".") or "."

    try:
        target = _resolve(ctx, rel_path)
    except (ValueError, FileNotFoundError) as exc:
        return {"output": "", "error": str(exc)}

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return {"output": "", "error": f"Invalid regex: {exc}"}

    files_to_search: list[Path] = []
    if target.is_file():
        files_to_search = [target]
    elif target.is_dir():
        for dirpath, _dirs, files in os.walk(target):
            for fname in files:
                files_to_search.append(Path(dirpath) / fname)
    else:
        return {"output": "", "error": f"Path not found: {rel_path}"}

    root = Path(ctx.workspace_root).resolve()
    matches: list[str] = []
    total_hits = 0
    for fpath in files_to_search:
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except (PermissionError, OSError):
            continue
        rel = fpath.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{rel}:{lineno}:{line}")
                total_hits += 1
                if total_hits >= 500:
                    matches.append("... [truncated at 500 hits]")
                    break
        if total_hits >= 500:
            break

    return {
        "output": "\n".join(matches) if matches else "(no matches)",
        "metadata": {"hits": total_hits, "files_searched": len(files_to_search)},
    }
