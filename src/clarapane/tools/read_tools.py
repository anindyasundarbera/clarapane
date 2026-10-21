"""Atomic read tools: list, grep, read a range, find a symbol.

Every handler returns a compact dict -- never a whole file. That is the
'fetch exact context for one atomic action' rule from the design: the model asks
for a specific slice, gets exactly that slice, and decides from evidence.

All results are capped so a runaway grep cannot blow the context window.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..policy import PolicyError, Workspace
from ..registry import ToolError, ToolRegistry, ToolSpec

# Skip binaries, VCS data, environments, and caches during traversal.
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".so", ".dylib", ".png", ".jpg", ".jpeg", ".gif",
                 ".webp", ".zip", ".gz", ".tar", ".whl", ".pdf", ".lock"}

MAX_GREP_HITS = 60
MAX_LINE_LEN = 400


def _iter_files(root: Path) -> list[Path]:
    """All text-ish regular files under root, sorted for deterministic output."""
    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        out.append(path)
    return out


def _clip(text: str, limit: int = MAX_LINE_LEN) -> str:
    text = text.rstrip("\n")
    return text if len(text) <= limit else text[:limit] + " ...[truncated]"


# --------------------------------------------------------------------- handlers
def list_dir(workspace: Workspace, path: str = ".", depth: int = 1) -> dict[str, Any]:
    target = workspace.resolve(path)
    if not target.exists():
        raise ToolError(f"no such path: {path}")
    if not target.is_dir():
        raise ToolError(f"not a directory: {path}")

    entries: list[dict[str, Any]] = []
    base_depth = len(target.parts)
    for child in sorted(target.rglob("*")):
        if any(part in SKIP_DIRS for part in child.parts):
            continue
        rel_depth = len(child.parts) - base_depth
        if rel_depth > max(1, min(depth, 6)):
            continue
        entries.append({
            "path": workspace.relative(child),
            "type": "dir" if child.is_dir() else "file",
            "bytes": 0 if child.is_dir() else child.stat().st_size,
            "depth": rel_depth,
        })
        if len(entries) >= 200:
            break
    return {"dir": workspace.relative(target), "count": len(entries), "entries": entries}


def grep(workspace: Workspace, pattern: str, path: str = ".", glob: str = "*") -> dict[str, Any]:
    target = workspace.resolve(path)
    if not pattern:
        raise ToolError("pattern must not be empty")
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"invalid regex {pattern!r}: {exc}") from None

    files = [target] if target.is_file() else [p for p in _iter_files(target) if p.match(glob)]
    hits: list[dict[str, Any]] = []
    matched_files: set[str] = set()

    for file in files:
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            if rx.search(line):
                rel = workspace.relative(file)
                matched_files.add(rel)
                if len(hits) < MAX_GREP_HITS:
                    hits.append({"file": rel, "line": lineno, "text": _clip(line.strip())})
        if len(hits) >= MAX_GREP_HITS:
            break

    return {
        "pattern": pattern,
        "hits": hits,
        "truncated": len(hits) >= MAX_GREP_HITS,
        "files_matched": sorted(matched_files),
    }


def read_range(workspace: Workspace, path: str, start: int = 1, end: int | None = None) -> dict[str, Any]:
    target = workspace.resolve(path)
    if not target.is_file():
        raise ToolError(f"not a file: {path}")

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    start = max(1, start)
    end = total if end is None else min(end, total)
    if start > total:
        raise ToolError(f"start line {start} beyond end of file ({total} lines)")
    if end < start:
        raise ToolError(f"end line {end} is before start line {start}")

    # Cap the slice so one call cannot dump a 10k-line file.
    capped_end = min(end, start + 399)
    numbered = [
        {"line": n, "text": _clip(lines[n - 1])}
        for n in range(start, capped_end + 1)
    ]
    return {
        "path": workspace.relative(target),
        "start": start,
        "end": capped_end,
        "total_lines": total,
        "truncated": capped_end < end,
        "lines": numbered,
    }


SYMBOL_PATTERNS = (
    re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>\w+)"),
    re.compile(r"^\s*class\s+(?P<name>\w+)"),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>\w+)"),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*=\s*(?:async\s*)?\("),
)


def find_symbol(workspace: Workspace, name: str, path: str = ".") -> dict[str, Any]:
    if not name:
        raise ToolError("name must not be empty")
    target = workspace.resolve(path)
    files = [target] if target.is_file() else _iter_files(target)
    word = re.compile(rf"\b{re.escape(name)}\b")

    definitions: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []

    for file in files:
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = workspace.relative(file)
        for lineno, line in enumerate(lines, start=1):
            if any(p.match(line) and p.match(line).group("name") == name
                   for p in SYMBOL_PATTERNS if p.match(line)):
                definitions.append({"file": rel, "line": lineno, "text": _clip(line.strip())})
            elif word.search(line) and len(references) < MAX_GREP_HITS:
                references.append({"file": rel, "line": lineno, "text": _clip(line.strip())})

    return {
        "symbol": name,
        "definitions": definitions,
        "references": references,
        "reference_count": len(references),
        "references_truncated": len(references) >= MAX_GREP_HITS,
    }


# ---------------------------------------------------------------------- schemas
def register_read_tools(registry: ToolRegistry, workspace: Workspace) -> None:
    """Attach the read-only tool set to a registry."""

    def bind(fn):  # noqa: ANN001, ANN202 - tiny local adapter
        return lambda **kw: fn(workspace, **kw)

    registry.register(ToolSpec(
        name="list_dir",
        description="List files and directories under a path in the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory relative to the workspace root.", "default": "."},
                "depth": {"type": "integer", "description": "Recursion depth, 1-6.", "default": 1},
            },
            "required": [],
        },
        risk="read",
        handler=bind(list_dir),
    ))

    registry.register(ToolSpec(
        name="grep",
        description="Regex search file contents. Returns file:line hits, not whole files.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Python regular expression."},
                "path": {"type": "string", "description": "File or directory to search.", "default": "."},
                "glob": {"type": "string", "description": "Filename filter, e.g. '*.py'.", "default": "*"},
            },
            "required": ["pattern"],
        },
        risk="read",
        handler=bind(grep),
    ))

    registry.register(ToolSpec(
        name="read_range",
        description="Read numbered lines from a file. Use this to get exact context before editing.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File to read."},
                "start": {"type": "integer", "description": "First line, 1-based.", "default": 1},
                "end": {"type": "integer", "description": "Last line, inclusive. Defaults to EOF."},
            },
            "required": ["path"],
        },
        risk="read",
        handler=bind(read_range),
    ))

    registry.register(ToolSpec(
        name="find_symbol",
        description="Locate a Python/JS symbol: where it is defined and where it is referenced.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact symbol name."},
                "path": {"type": "string", "description": "Search root.", "default": "."},
            },
            "required": ["name"],
        },
        risk="read",
        handler=bind(find_symbol),
    ))
