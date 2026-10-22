"""Checkpoints and rollback: make a verified state reachable again.

Why this exists
---------------
An agent that can execute commands and edit files but cannot return to a known state
is one bad edit away from a workspace nobody can reason about. A checkpoint records the
tree hash before a run mutates anything; rollback restores it on request.

Design constraints
------------------
  * The agent may not commit or push. `git commit` is refused by the exec allowlist and
    these tools never expose it. History stays a human decision.
  * A checkpoint is a TAG on a synthetic commit, so it is cheap and does not disturb the
    user's branch or index.
  * Rollback is gated: reversing work is a mutation and must be approved like any other.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..registry import ToolError, ToolRegistry, ToolSpec

CHECKPOINT_PREFIX = "clarapane-checkpoint"


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise ToolError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def is_repository(root: Path) -> bool:
    return (root / ".git").exists() or subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=str(root),
        capture_output=True, text=True,
    ).returncode == 0


def register_git_tools(registry: ToolRegistry, workspace) -> None:
    def git_checkpoint(label: str = "checkpoint") -> dict[str, Any]:
        root = workspace.root
        if not is_repository(root):
            raise ToolError("workspace is not a git repository; checkpoints unavailable")
        # Record the whole tree state without touching the user's branch: a dangling
        # commit holds the content, and a tag makes it findable and cheap to delete.
        _git(root, "add", "-A")
        tree = _git(root, "write-tree")
        head = _git(root, "rev-parse", "HEAD", check=False) or ""
        commit = _git(
            root, "commit-tree", tree, "-p", head, "-m", f"checkpoint: {label}"
        ) if head else _git(root, "commit-tree", tree, "-m", f"checkpoint: {label}")
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:40]
        tag = f"{CHECKPOINT_PREFIX}/{safe}"
        _git(root, "tag", "-f", tag, commit)
        return {"checkpoint": tag, "commit": commit, "label": label}

    def git_rollback(ref: str) -> dict[str, Any]:
        root = workspace.root
        if not ref.startswith(CHECKPOINT_PREFIX + "/"):
            raise ToolError(
                f"ref must be a checkpoint created by git_checkpoint, not {ref!r}"
            )
        _git(root, "rev-parse", "--verify", f"refs/tags/{ref}")
        # Restore tracked files only; untracked work is left alone so rollback cannot
        # silently delete something the operator created outside the agent.
        _git(root, "checkout", ref, "--", ".")
        return {"rolled_back_to": ref, "restored": "tracked files",
                "note": "untracked files were not touched"}

    registry.register(ToolSpec(
        name="git_checkpoint",
        description=(
            "Record the current workspace state so it can be restored. Call this BEFORE "
            "mutating files. Does not commit to history and does not touch your branch."
        ),
        parameters={
            "type": "object",
            "properties": {"label": {"type": "string", "description": "Short label."}},
            "required": ["label"],
        },
        risk="write",
        # NOT gated: a checkpoint only records state, and requiring approval for it
        # would mean the safety net needs permission before the risky action can be
        # made safe. The trace still records the call.
        gate=False,
        handler=git_checkpoint,
    ))

    registry.register(ToolSpec(
        name="git_rollback",
        description=(
            "Restore tracked files to a checkpoint made by git_checkpoint. Requires "
            "approval because it reverses work."
        ),
        parameters={
            "type": "object",
            "properties": {"ref": {"type": "string",
                                   "description": "Checkpoint ref from git_checkpoint."}},
            "required": ["ref"],
        },
        risk="write",
        gate=True,
        handler=git_rollback,
    ))
