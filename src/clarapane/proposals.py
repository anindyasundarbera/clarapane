"""Proposals: the propose-before-mutate handshake.

Why two tools instead of one
----------------------------
`propose_edit` computes a diff and returns a `proposal_id`. It changes nothing.
`apply_edit` consumes that id and writes. Splitting them is what makes the system
glass-box: intent becomes a first-class artifact that can be inspected, approved,
rejected, or replayed, instead of an invisible side effect buried in a write call.

The properties that matter
--------------------------
  * SINGLE USE. A proposal is consumed on apply. Replaying the id fails, so a
    confused or looping model cannot apply the same edit twice.
  * BOUND TO THE CURRENT CONTENT. The proposal stores the file hash it was computed
    against. If the file changed in between, apply refuses rather than clobbering
    concurrent work -- this is a compare-and-swap, not a blind write.
  * PER RUN. The store lives with the run. Nothing survives into the next run, so a
    stale id from an old trace cannot mutate anything.
  * EXACT MATCH. `old_str` must appear exactly once. An ambiguous or absent match is
    an error, never a guess about which occurrence was meant.
"""

from __future__ import annotations

import difflib
import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .registry import ToolError


class ProposalError(ToolError):
    """A proposal could not be created or applied."""


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class Proposal:
    """An unapplied edit, with everything needed to apply it safely."""

    id: str
    path: Path
    relative_path: str
    old_str: str
    new_str: str
    diff: str
    before_hash: str
    created_at_step: int
    # Replaced on apply so a consumed proposal is visibly spent.
    consumed: bool = False

    def summary(self) -> dict[str, object]:
        return {
            "proposal_id": self.id,
            "path": self.relative_path,
            "diff": self.diff,
            "lines_changed": self.diff.count("\n+") + self.diff.count("\n-"),
            "note": "Nothing has been written. Call apply_edit with this id to apply.",
        }


@dataclass
class ProposalStore:
    """Per-run, in-memory record of proposed edits."""

    _items: dict[str, Proposal] = field(default_factory=dict)

    def create(
        self,
        *,
        path: Path,
        relative_path: str,
        old_str: str,
        new_str: str,
        step: int,
    ) -> Proposal:
        if not old_str:
            raise ProposalError(
                "old_str must not be empty; use write_file to create a new file"
            )
        if old_str == new_str:
            raise ProposalError("old_str and new_str are identical; nothing to change")
        if not path.exists() or not path.is_file():
            raise ProposalError(f"not an existing file: {relative_path}")

        text = path.read_text(encoding="utf-8")
        occurrences = text.count(old_str)
        if occurrences == 0:
            raise ProposalError(
                f"old_str not found in {relative_path}. "
                "Read the file with read_range and copy the exact text."
            )
        if occurrences > 1:
            raise ProposalError(
                f"old_str appears {occurrences} times in {relative_path}; "
                "include more surrounding lines so the match is unique."
            )

        updated = text.replace(old_str, new_str, 1)
        diff = "".join(
            difflib.unified_diff(
                text.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
            )
        )

        proposal = Proposal(
            id=f"p_{uuid.uuid4().hex[:8]}",
            path=path,
            relative_path=relative_path,
            old_str=old_str,
            new_str=new_str,
            diff=diff,
            before_hash=file_digest(path),
            created_at_step=step,
        )
        self._items[proposal.id] = proposal
        return proposal

    def get(self, proposal_id: str) -> Proposal:
        try:
            return self._items[proposal_id]
        except KeyError:
            raise ProposalError(
                f"unknown proposal id {proposal_id!r}. "
                "Call propose_edit first; ids are valid only within this run."
            ) from None

    def consume(self, proposal_id: str) -> Proposal:
        """Mark a proposal spent and return it. Rejects a second consumption."""
        proposal = self.get(proposal_id)
        if proposal.consumed:
            raise ProposalError(f"proposal {proposal_id} was already applied")
        spent = Proposal(**{**proposal.__dict__, "consumed": True})
        self._items[proposal_id] = spent
        return spent

    def verify_unchanged(self, proposal: Proposal) -> None:
        """Compare-and-swap guard: the file must still match what we diffed."""
        if not proposal.path.exists():
            raise ProposalError(f"{proposal.relative_path} no longer exists")
        current = file_digest(proposal.path)
        if current != proposal.before_hash:
            raise ProposalError(
                f"{proposal.relative_path} changed after the proposal was created; "
                "re-read it and propose the edit again"
            )

    def pending(self) -> list[Proposal]:
        return [p for p in self._items.values() if not p.consumed]

    def __len__(self) -> int:
        return len(self._items)
