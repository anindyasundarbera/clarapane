"""Approval gate: decides whether a mutating tool may run.

Why this is separate from the tools
-----------------------------------
If a tool knew how to ask for approval, a model could reason about the tool's
description and argue its way past it. The gate sits in the harness, between
registry lookup and handler execution, and the tool has no say in the outcome.

Two modes, chosen per run:
  * INTERACTIVE (default) -- prompt on a TTY, showing the diff when there is one.
  * POLICY (headless)     -- a non-interactive answer, recorded with its reason.

Either way the decision is logged with `actor` and `reason`, so the trace answers
"who allowed this write?" rather than merely "a write happened".
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, TextIO


@dataclass(frozen=True)
class Decision:
    approved: bool
    actor: str          # "human" | "policy"
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"approved": self.approved, "actor": self.actor, "reason": self.reason}


# ---------------------------------------------------------------- renderers
def render_diff_preview(tool: str, args: dict[str, Any]) -> str:
    """Human-readable preview of what the gated call will do."""
    lines = [f"  tool: {tool}"]
    if "path" in args:
        lines.append(f"  path: {args['path']}")
    if tool == "apply_edit" and "proposal_id" in args:
        lines.append(f"  proposal: {args['proposal_id']}")
    if "_diff" in args:
        lines.append("")
        lines.extend("  " + line for line in str(args["_diff"]).splitlines())
    elif "content" in args:
        preview = str(args["content"]).splitlines()[:12]
        lines.append("  content:")
        lines.extend("    " + line for line in preview)
    return "\n".join(lines)


# -------------------------------------------------------------------- gates
class PolicyGate:
    """Non-interactive gate for headless runs. Denies by default."""

    def __init__(self, *, allow_writes: bool = False, reason: str = "policy") -> None:
        self.allow_writes = allow_writes
        self.reason = reason

    def decide(self, tool: str, args: dict[str, Any]) -> Decision:
        verdict = self.allow_writes
        return Decision(
            approved=verdict,
            actor="policy",
            reason=f"{self.reason}: writes {'allowed' if verdict else 'denied'}",
        )


class InteractiveGate:
    """Prompt on a TTY for each gated call."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdin
        self._allow_all = False

    def decide(self, tool: str, args: dict[str, Any]) -> Decision:
        if self._allow_all:
            return Decision(approved=True, actor="human", reason="previously allowed all")

        if not self.stream.isatty():
            return Decision(
                approved=False,
                actor="harness",
                reason="no TTY available for approval; denying by default",
            )

        print("\n--- approval required ---")
        print(render_diff_preview(tool, args))
        print("-------------------------")
        try:
            answer = input("apply this change? [y/N/a=allow all] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return Decision(approved=False, actor="human", reason="interrupted")

        if answer == "a":
            self._allow_all = True
            return Decision(approved=True, actor="human", reason="approved for this run")
        if answer in {"y", "yes"}:
            return Decision(approved=True, actor="human", reason="approved")
        return Decision(approved=False, actor="human", reason="rejected by operator")
