"""Mutation tools: propose_edit, apply_edit, write_file.

Every handler resolves its path through Workspace.resolve(write=True), so the path
jail applies to mutations exactly as it does to reads. Nothing here opens a file
without that check, and the agent cannot write its own source.

The approval gate is NOT here. Tools declare `gate=True`; the agent loop decides
whether to ask, so that policy stays in one place and the tool cannot be tricked
into thinking it was approved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..policy import PolicyError, Workspace
from ..proposals import ProposalError, ProposalStore
from ..registry import ToolError, ToolRegistry, ToolSpec


def register_write_tools(
    registry: ToolRegistry,
    workspace: Workspace,
    store: ProposalStore,
    *,
    step_getter: Callable[[], int] = lambda: 0,
) -> None:
    """Attach mutation tools. `step_getter` records which turn proposed an edit."""

    def propose_edit(path: str, old_str: str, new_str: str) -> dict[str, Any]:
        target_path = workspace.resolve(path, write=True)
        proposal = store.create(
            path=target_path,
            relative_path=workspace.relative(target_path),
            old_str=old_str,
            new_str=new_str,
            step=step_getter(),
        )
        return proposal.summary()

    def apply_edit(proposal_id: str) -> dict[str, Any]:
        # Check single-use BEFORE the hash guard: a second apply would otherwise
        # report "file changed" and hide the real reason (already applied).
        proposal = store.get(proposal_id)
        if proposal.consumed:
            raise ProposalError(f"proposal {proposal_id} was already applied")
        store.verify_unchanged(proposal)
        spent = store.consume(proposal_id)

        # Re-resolve at apply time: the jail is checked again, not inherited.
        target = workspace.resolve(spent.relative_path, write=True)
        text = target.read_text(encoding="utf-8")
        if text.count(spent.old_str) != 1:
            raise ProposalError(
                f"{spent.relative_path} changed in a way that makes the edit ambiguous"
            )
        target.write_text(text.replace(spent.old_str, spent.new_str, 1), encoding="utf-8")

        return {
            "applied": True,
            "proposal_id": spent.id,
            "path": spent.relative_path,
            "after_hash": _digest(target),
            "bytes_written": len(spent.new_str),
        }

    def write_file(path: str, content: str) -> dict[str, Any]:
        target = workspace.resolve(path, write=True)
        if target.exists():
            raise ProposalError(
                f"{workspace.relative(target)} already exists. "
                "Use propose_edit to change an existing file."
            )
        if not target.parent.exists():
            raise ProposalError(f"parent directory does not exist: {target.parent.name}")
        target.write_text(content, encoding="utf-8")
        return {
            "created": True,
            "path": workspace.relative(target),
            "after_hash": _digest(target),
            "bytes_written": len(content),
        }

    registry.register(ToolSpec(
        name="propose_edit",
        description=(
            "Propose a change to an existing file. Returns a diff and a proposal_id; "
            "NOTHING is written. Read the file first and copy the exact text for old_str."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File to change."},
                "old_str": {
                    "type": "string",
                    "description": "Exact existing text, including indentation. Must occur exactly once.",
                },
                "new_str": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_str", "new_str"],
        },
        risk="read",  # proposing mutates nothing
        handler=propose_edit,
    ))

    registry.register(ToolSpec(
        name="apply_edit",
        description=(
            "Apply a previously proposed edit by id. This is the only tool that "
            "changes an existing file, and it requires human approval."
        ),
        parameters={
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string", "description": "Id from propose_edit."},
            },
            "required": ["proposal_id"],
        },
        risk="write",
        gate=True,
        handler=apply_edit,
    ))

    registry.register(ToolSpec(
        name="write_file",
        description=(
            "Create a NEW file. Fails if the path already exists; use propose_edit "
            "for existing files. Requires human approval."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "New file path."},
                "content": {"type": "string", "description": "Full file content."},
            },
            "required": ["path", "content"],
        },
        risk="write",
        gate=True,
        handler=write_file,
    ))


def _digest(path: Path) -> str:
    from ..proposals import file_digest

    return file_digest(path)[:16]
