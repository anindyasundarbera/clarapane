"""Capability-profile tests.

These pin the properties we actually rely on:
  * selection is least-privilege and fails loudly on a bad profile
  * inference distinguishes questions from imperatives
  * a scoped registry cannot dispatch a tool it was not given
"""

from __future__ import annotations

import pytest

from clarapane import profiles
from clarapane.registry import ToolError, ToolRegistry
from clarapane.tools.finish_tool import register_finish_tool
from clarapane.tools.read_tools import register_read_tools


@pytest.fixture
def full_registry(workspace):
    """Everything the codebase can register.

    The original fixture registered only read tools, so it silently could not
    represent the "full" registry its name claimed -- which is why nothing noticed
    that the exec profile named tools that do not exist.
    """
    from clarapane.proposals import ProposalStore
    from clarapane.tools.exec_tools import register_exec_tools
    from clarapane.tools.git_tools import register_git_tools
    from clarapane.tools.write_tools import register_write_tools

    registry = ToolRegistry()
    register_read_tools(registry, workspace)
    register_write_tools(registry, workspace, ProposalStore())
    register_exec_tools(registry, workspace)
    register_git_tools(registry, workspace)
    register_finish_tool(registry)

    # The fixture must stay in step with build_agent(). It drifted once already,
    # which is why the exec profile's broken tools went unnoticed.
    from clarapane.cli import build_agent  # noqa: F401  (imported for symmetry)
    return registry


def test_read_only_profile_excludes_every_mutation_tool(full_registry):
    scoped = profiles.select(full_registry, "read_only")
    assert "finish" in scoped
    for forbidden in ("propose_edit", "apply_edit", "write_file", "run_command"):
        assert forbidden not in scoped


def test_profile_is_a_strict_subset(full_registry):
    scoped = profiles.select(full_registry, "read_only")
    assert set(scoped.names()) <= set(full_registry.names())


def test_missing_tool_is_a_startup_error_not_a_silent_gap(workspace):
    """A profile naming an unregistered tool must fail loudly at build time.

    Built an intentionally incomplete registry rather than reusing the full one: the
    original version of this test passed only because the fixture was missing the
    write tools, so it asserted a bug instead of the contract.
    """
    read_only_registry = ToolRegistry()
    register_read_tools(read_only_registry, workspace)
    register_finish_tool(read_only_registry)

    with pytest.raises(ValueError, match="needs unregistered tools"):
        profiles.select(read_only_registry, "edit")


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
        # No exec profile exists yet, so execution tasks degrade to edit.
        ("run pytest and report", "edit"),
        ("implement multiply then run tests", "edit"),
    ],
)
def test_infer_profile(task, expected):
    assert profiles.infer(task) == expected


def test_infer_treats_symbol_named_add_as_a_question():
    """"add" is both a verb and a plausible function name; leading verb decides."""
    assert profiles.infer("where is add defined?") == "read_only"
    assert profiles.infer("add a docstring to average") == "edit"


def test_every_advertised_profile_can_actually_be_built(full_registry):
    """Regression: `exec` was advertised but unconstructible.

    The CLI accepted --profile exec, select() then raised ValueError because
    run_command / git_checkpoint / git_rollback were never registered. A profile must
    never name a tool the codebase cannot supply.
    """
    for name in profiles.PROFILES:
        profiles.select(full_registry, name)  # must not raise


def test_cli_profile_choices_come_from_the_registry():
    """The advertised list must be derived, not hardcoded, or they will drift again."""
    from pathlib import Path

    cli_src = (Path(__file__).resolve().parent.parent / "src" / "clarapane" / "cli.py").read_text()
    assert "choices=list(profiles.PROFILES)" in cli_src
    assert '"exec"' not in cli_src


def test_execution_tasks_do_not_select_a_nonexistent_profile():
    for task in ("run pytest and fix failures", "install deps", "commit the change"):
        assert profiles.infer(task) in profiles.PROFILES
