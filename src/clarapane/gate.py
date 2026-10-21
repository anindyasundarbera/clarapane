"""Approval gate: decides whether a mutating tool may run, and proves what it allowed.

Three defects this module now closes
------------------------------------
1. A bare "proceed" is not an approval. It authorizes *future* work, not the exact
   work in front of the operator. Approval here requires the operator to type a
   token derived from the *content* being authorized, so consent is bound to one
   specific action.

2. An approval had no accountable person. `actor` said "human", which is a category,
   not an identity. Decisions now carry `operator`, resolved from
   CLARAPANE_OPERATOR_ID or the OS login.

3. An approval was not bound to the payload. A decision recorded as approved could in
   principle have applied to different work than the operator saw. Every decision now
   carries a `fingerprint` of the exact tool, path and content, recorded in the trace
   and re-derivable from it.

Why the gate lives here, not in the tool
---------------------------------------
If a tool knew how to ask for approval, a model could reason about the tool's
description and argue its way past it. The gate sits in the harness between registry
lookup and handler execution. A tool cannot decide whether it was approved.

Honest limit
------------
Binding proves the operator approved *this content*. It does not prove they read and
understood it. Typing a token is a stronger signal than pressing "y", and a recorded
fingerprint makes the consent auditable afterwards, but neither establishes
comprehension.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from typing import Any, TextIO

from . import identity


def fingerprint(tool: str, args: dict[str, Any]) -> str:
    """A stable hash of exactly what is being authorized.

    Only fields that determine the effect are included, so incidental keys (a UI-only
    `_diff` annotation) do not change what the operator is approving.
    """
    material = {k: v for k, v in args.items() if not k.startswith("_")}
    payload = json.dumps(
        {"tool": tool, "args": material}, sort_keys=True, ensure_ascii=False, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Decision:
    approved: bool
    actor: str          # "human" | "policy" | "harness"
    reason: str
    operator: str = ""
    fingerprint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "actor": self.actor,
            "reason": self.reason,
            "operator": self.operator,
            "fingerprint": self.fingerprint,
        }


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
    """Non-interactive gate for headless runs. Denies by default.

    A policy approval is not a human approval and says so: `actor` is "policy" and
    `operator` names the policy, never a person. That distinction stops a headless run
    from manufacturing human consent it never obtained.
    """

    def __init__(self, *, allow_writes: bool = False, reason: str = "policy",
                 operator: str | None = None) -> None:
        self.allow_writes = allow_writes
        self.reason = reason
        self.operator = operator or f"policy:{reason}"

    def decide(self, tool: str, args: dict[str, Any]) -> Decision:
        verdict = self.allow_writes
        return Decision(
            approved=verdict,
            actor="policy",
            reason=f"{self.reason}: writes {'allowed' if verdict else 'denied'}",
            operator=self.operator,
            fingerprint=fingerprint(tool, args),
        )


class InteractiveGate:
    """Prompt on a TTY and require the operator to confirm the exact work.

    Accepted answers:
        <fingerprint>       approve exactly this action (the token is shown above)
        a <fingerprint>     approve this and all later actions in this run
        n / anything else   reject

    A bare "y", "yes" or "proceed" is REJECTED: it does not bind consent to the
    content, so it cannot be audited afterwards.
    """

    def __init__(self, stream: TextIO | None = None, *, operator: str | None = None) -> None:
        self.stream = stream or sys.stdin
        self.operator = identity.operator(operator)
        self._allow_all = False
        self._approved_fingerprints: set[str] = set()

    def decide(self, tool: str, args: dict[str, Any]) -> Decision:
        fp = fingerprint(tool, args)

        if self._allow_all or fp in self._approved_fingerprints:
            return Decision(
                approved=True, actor="human",
                reason="covered by an earlier explicit approval in this run",
                operator=self.operator, fingerprint=fp,
            )

        if not self.stream.isatty():
            return Decision(
                approved=False, actor="harness",
                reason="no TTY available for approval; denying by default",
                operator=self.operator, fingerprint=fp,
            )

        print("\n--- approval required ---")
        print(render_diff_preview(tool, args))
        print(f"  operator: {self.operator}")
        print(f"  approve this exact action by typing: {fp}")
        print(f"  (type 'n' to reject; 'a {fp}' to allow all later actions)")
        print("-------------------------")
        try:
            answer = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return Decision(approved=False, actor="human", reason="interrupted",
                            operator=self.operator, fingerprint=fp)

        if answer == fp:
            self._approved_fingerprints.add(fp)
            return Decision(approved=True, actor="human",
                            reason=f"approved by {self.operator} for fingerprint {fp}",
                            operator=self.operator, fingerprint=fp)
        if answer == f"a {fp}":
            self._allow_all = True
            return Decision(approved=True, actor="human",
                            reason=f"approved by {self.operator}: allow all this run",
                            operator=self.operator, fingerprint=fp)
        if answer in {"y", "yes", "proceed", "ok", "sure", ""}:
            return Decision(
                approved=False, actor="human",
                reason=(
                    f"rejected: {answer!r} is not a binding approval. "
                    "Type the fingerprint to confirm this exact action."
                ),
                operator=self.operator, fingerprint=fp,
            )
        return Decision(approved=False, actor="human",
                        reason="rejected by operator",
                        operator=self.operator, fingerprint=fp)
