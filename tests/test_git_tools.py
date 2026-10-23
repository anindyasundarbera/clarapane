"""Checkpoint and rollback: the state an agent can return to.

There was no test for these tools before this file, and docs/CAPABILITIES.md cited one
anyway -- a citation of a test that did not exist. The docs checker caught it.
"""

from __future__ import annotations

import subprocess

import pytest

from clarapane.policy import PolicyError
from clarapane.registry import ToolError, ToolRegistry
from clarapane.tools.git_tools import register_git_tools


@pytest.fixture
def repo(workspace):
    """The workspace as a git repository with one commit."""
    for args in (["init", "-q", "."], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=workspace.root, capture_output=True)
    registry = ToolRegistry()
    register_git_tools(registry, workspace)
    return workspace, registry


def test_checkpoint_records_state_without_committing(repo):
    workspace, registry = repo
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace.root,
                            capture_output=True, text=True).stdout.strip()

    out = registry.get("git_checkpoint").handler(label="before edit")

    assert out["checkpoint"].startswith("clarapane-checkpoint/")
    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace.root,
                           capture_output=True, text=True).stdout.strip()
    assert before == after, "checkpoint must not move the branch"


def test_rollback_restores_a_modified_file(repo):
    workspace, registry = repo
    target = workspace.root / "pkg" / "mod.py"
    original = target.read_text()

    checkpoint = registry.get("git_checkpoint").handler(label="pre")["checkpoint"]
    target.write_text("# wrecked\n")
    registry.get("git_rollback").handler(ref=checkpoint)

    assert target.read_text() == original


def test_rollback_only_accepts_a_checkpoint_ref(repo):
    """An arbitrary ref would let the agent rewind the user's real history."""
    _, registry = repo
    with pytest.raises(ToolError, match="must be a checkpoint"):
        registry.get("git_rollback").handler(ref="HEAD~5")


def test_checkpoint_outside_a_repository_is_refused(workspace):
    registry = ToolRegistry()
    register_git_tools(registry, workspace)
    with pytest.raises(ToolError, match="not a git repository"):
        registry.get("git_checkpoint").handler(label="x")


def test_rollback_is_gated_but_checkpoint_is_not(repo):
    """A safety net must not need permission before the risky action is made safe."""
    _, registry = repo
    assert registry.get("git_checkpoint").gate is False
    assert registry.get("git_rollback").gate is True
    assert registry.get("git_rollback").risk == "write"
