"""Capability-profile tests.

These pin the properties we actually rely on:
  * selection is least-privilege and fails loudly on a bad profile
  * inference distinguishes questions from imperatives
  * a scoped registry cannot dispatch a tool it was not given
"""

from __future__ import annotations

import pytest

from glassbox import profiles
from glassbox.registry import ToolError, ToolRegistry
from glassbox.tools.finish_tool import register_finish_tool
from glassbox.tools.read_tools import register_read_tools


@pytest.fixture
def full_registry(workspace):
    registry = ToolRegistry()
    register_read_tools(registry, workspace)
    register_finish_tool(registry)
    return registry


def test_read_only_profile_excludes_every_mutation_tool(full_registry):
    scoped = profiles.select(full_registry, "read_only")
    assert "finish" in scoped
    for forbidden in ("propose_edit", "apply_edit", "write_file", "run_command"):
        assert forbidden not in scoped


def test_profile_is_a_strict_subset(full_registry):
    scoped = profiles.select(full_registry, "read_only")
    assert set(scoped.names()) <= set(full_registry.names())


def test_missing_tool_is_a_startup_error_not_a_silent_gap(full_registry):
    """A profile naming an unregistered tool must fail loudly at build time."""
    with pytest.raises(ValueError, match="needs unregistered tools"):
        profiles.select(full_registry, "edit")


def test_unknown_profile_name_rejected(full_registry):
    with pytest.raises(ValueError, match="unknown profile"):
        profiles.select(full_registry, "god_mode")


def test_scoped_registry_refuses_a_tool_it_was_not_given(full_registry):
    scoped = profiles.select(full_registry, "read_only")
    with pytest.raises(ToolError, match="unknown tool"):
        scoped.get("propose_edit")


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("where is add defined?", "read_only"),
        ("find the definition of add", "read_only"),
        ("explain the add function", "read_only"),
        ("what does grep do?", "read_only"),
        ("show me the bug", "read_only"),
        ("list files", "read_only"),
        ("fix the clamp bug", "edit"),
        ("add a docstring to average", "edit"),
        ("create a new module", "edit"),
        ("remove the clamping", "edit"),
        ("run pytest and report", "exec"),
        ("implement multiply then run tests", "exec"),
    ],
)
def test_infer_profile(task, expected):
    assert profiles.infer(task) == expected


def test_infer_treats_symbol_named_add_as_a_question():
    """"add" is both a verb and a plausible function name; leading verb decides."""
    assert profiles.infer("where is add defined?") == "read_only"
    assert profiles.infer("add a docstring to average") == "edit"
