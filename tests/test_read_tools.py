"""Read-tool tests: exact slices, capped output, useful errors."""

from __future__ import annotations

import pytest

from glassbox.registry import ToolError, ToolRegistry


@pytest.fixture
def tools(workspace):
    from glassbox.tools.read_tools import register_read_tools

    registry = ToolRegistry()
    register_read_tools(registry, workspace)
    return registry


def test_list_dir_shows_relative_paths(tools):
    out = tools.get("list_dir").handler(path=".")
    paths = {e["path"] for e in out["entries"]}
    assert "pkg" in paths and "notes.txt" in paths


def test_read_range_numbers_lines_and_reports_total(tools):
    out = tools.get("read_range").handler(path="pkg/mod.py", start=1, end=2)
    assert out["start"] == 1 and out["end"] == 2
    assert out["total_lines"] == 6
    assert out["lines"][0] == {"line": 1, "text": "def alpha(x):"}


def test_read_range_caps_wide_requests(tools):
    out = tools.get("read_range").handler(path="pkg/mod.py", start=1, end=10_000)
    assert out["end"] - out["start"] + 1 <= 400


def test_read_range_rejects_bad_window(tools):
    with pytest.raises(ToolError, match="before start"):
        tools.get("read_range").handler(path="pkg/mod.py", start=5, end=2)


def test_grep_returns_file_and_line_not_whole_files(tools):
    out = tools.get("grep").handler(pattern=r"\balpha\b")
    assert out["files_matched"] == ["notes.txt", "pkg/mod.py"]
    assert all(set(h) == {"file", "line", "text"} for h in out["hits"])


def test_grep_rejects_invalid_regex(tools):
    with pytest.raises(ToolError, match="invalid regex"):
        tools.get("grep").handler(pattern="(")


def test_find_symbol_separates_definitions_from_references(tools):
    out = tools.get("find_symbol").handler(name="alpha")
    assert [d["line"] for d in out["definitions"]] == [1]
    assert out["reference_count"] >= 2


def test_tool_errors_surface_as_toolerror_not_crash(tools):
    with pytest.raises(ToolError, match="no such path"):
        tools.get("list_dir").handler(path="nope")
