"""Deferred-work backlog: a tracked, machine-readable list of things not built yet.

Why this exists
---------------
Decisions to *not* build something are as important as the code, but they are
invisible once the conversation ends. This module turns "we'll do that later"
into a record with a trigger, a design sketch, and acceptance criteria, so a
future session can pick it up without re-deriving the reasoning.

Design rules
------------
  * ``backlog.json`` is the single source of truth. Nothing is hardcoded here.
  * Statuses are a closed set, so an item cannot silently rot in limbo.
  * Every ``planned`` item must have a ``trigger`` and at least one acceptance
    criterion -- otherwise it is a wish, not a plan.
  * ``rejected`` items keep their ``why`` so nobody re-proposes them. The
    dynamic-tool-selection entry exists precisely to stop that.

CLI
---
    python -m clarapane.backlog                 # everything, grouped by status
    python -m clarapane.backlog due             # only what is actionable now
    python -m clarapane.backlog show <id>
    python -m clarapane.backlog start <id>      # planned -> in_progress
    python -m clarapane.backlog done <id>
    python -m clarapane.backlog add --title "..." --why "..." --area agent-loop
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Literal

Status = Literal["planned", "in_progress", "done", "rejected", "blocked"]

VALID_STATUSES: tuple[Status, ...] = ("planned", "in_progress", "done", "rejected", "blocked")

# Statuses that represent outstanding work.
OPEN_STATUSES: tuple[Status, ...] = ("planned", "in_progress", "blocked")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BACKLOG_PATH = PROJECT_ROOT / "backlog.json"

STATUS_MARK = {
    "planned": "[ ]",
    "in_progress": "[~]",
    "blocked": "[!]",
    "done": "[x]",
    "rejected": "[-]",
}


class BacklogError(RuntimeError):
    """The backlog file is missing or malformed."""


# --------------------------------------------------------------------- loading
def load(path: Path | None = None) -> dict[str, Any]:
    # Resolve at call time, not at import time, so tests and callers can point
    # the tracker at another file without monkeypatching bound defaults.
    path = path or BACKLOG_PATH
    if not path.exists():
        raise BacklogError(f"no backlog at {path}")
    data = json.loads(path.read_text())
    validate(data)
    return data


def save(data: dict[str, Any], path: Path | None = None) -> None:
    path = path or BACKLOG_PATH
    data["updated"] = date.today().isoformat()
    validate(data)
    path.write_text(json.dumps(data, indent=2) + "\n")


def validate(data: dict[str, Any]) -> None:
    """Reject a malformed backlog early -- a broken tracker is worse than none."""
    if not isinstance(data.get("items"), list):
        raise BacklogError("backlog must contain an 'items' list")

    seen: set[str] = set()
    for item in data["items"]:
        item_id = item.get("id")
        if not item_id:
            raise BacklogError(f"item without id: {item!r}")
        if item_id in seen:
            raise BacklogError(f"duplicate id: {item_id}")
        seen.add(item_id)

        status = item.get("status")
        if status not in VALID_STATUSES:
            raise BacklogError(f"{item_id}: invalid status {status!r}")
        if not item.get("why"):
            raise BacklogError(f"{item_id}: needs a 'why'")
        if not item.get("area"):
            raise BacklogError(f"{item_id}: needs an 'area'")

        if status in OPEN_STATUSES:
            if not item.get("trigger"):
                raise BacklogError(f"{item_id}: open items need a 'trigger'")
            if not item.get("acceptance") and status != "blocked":
                raise BacklogError(f"{item_id}: open items need acceptance criteria")
        if status == "done" and not item.get("resolved_on"):
            raise BacklogError(f"{item_id}: done items need 'resolved_on'")


# ------------------------------------------------------------------- selection
def due(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Items that are actionable now: open, and not deprioritised to 'low'."""
    return [i for i in data["items"] if i["status"] in OPEN_STATUSES and i.get("priority") != "low"]


def find(data: dict[str, Any], item_id: str) -> dict[str, Any]:
    for item in data["items"]:
        if item["id"] == item_id:
            return item
    raise BacklogError(f"no backlog item with id {item_id!r}")


def reminder(data: dict[str, Any]) -> str | None:
    """One-line nudge for other tools to print. None when nothing is outstanding."""
    items = due(data)
    if not items:
        return None
    high = [i for i in items if i.get("priority") == "high"]
    lead = high[0]["title"] if high else items[0]["title"]
    noun = "item" if len(items) == 1 else "items"
    return (
        f"backlog: {len(items)} open {noun} "
        f"(next: {lead!r}) -- `python -m clarapane.backlog due`"
    )


# ---------------------------------------------------------------------- output
def render(data: dict[str, Any], items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        mark = STATUS_MARK.get(item["status"], "[?]")
        prio = item.get("priority", "medium")
        lines.append(f"{mark} {item['id']}")
        lines.append(f"      {item['title']}  ({item['area']}, {prio})")
        lines.append(f"      why: {item['why']}")
        if item["status"] in OPEN_STATUSES and item.get("trigger"):
            lines.append(f"      trigger: {item['trigger']}")
        if item["status"] == "rejected":
            lines.append("      rejected -- do not re-propose without new evidence")
        lines.append("")
    return "\n".join(lines).rstrip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Manage the deferred-work backlog")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("due", help="Show actionable items only")
    p_show = sub.add_parser("show", help="Show one item in full")
    p_show.add_argument("id")

    for name, help_text in (("start", "Mark planned -> in_progress"),
                            ("done", "Mark an item done"),
                            ("block", "Mark an item blocked")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("id")
        p.add_argument("--note", default="", help="Optional context recorded with the change")

    p_add = sub.add_parser("add", help="Add a planned item")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--why", required=True)
    p_add.add_argument("--area", required=True)
    p_add.add_argument("--id", dest="item_id", default=None)
    p_add.add_argument("--trigger", default="unspecified -- refine before starting")
    p_add.add_argument("--priority", default="medium", choices=["high", "medium", "low"])

    ap.add_argument("--file", default=None, help="Backlog file (defaults to backlog.json)")
    args = ap.parse_args(argv)

    global BACKLOG_PATH
    if args.file:
        BACKLOG_PATH = Path(args.file)

    try:
        data = load()
    except (BacklogError, json.JSONDecodeError) as exc:
        print(f"backlog error: {exc}", file=sys.stderr)
        return 2

    if args.cmd in (None, "due"):
        items = data["items"] if args.cmd is None else due(data)
        header = "ALL ITEMS" if args.cmd is None else "ACTIONABLE NOW"
        print(f"{header}  (updated {data.get('updated', 'unknown')})\n")
        print(render(data, items) if items else "  nothing outstanding")
        return 0

    if args.cmd == "show":
        try:
            item = find(data, args.id)
        except BacklogError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(json.dumps(item, indent=2))
        return 0

    if args.cmd == "add":
        item_id = args.item_id or args.title.lower().replace(" ", "-")[:48]
        if any(i["id"] == item_id for i in data["items"]):
            print(f"id already exists: {item_id}", file=sys.stderr)
            return 1
        data["items"].append({
            "id": item_id,
            "title": args.title,
            "status": "planned",
            "priority": args.priority,
            "area": args.area,
            "deferred_on": date.today().isoformat(),
            "why": args.why,
            "trigger": args.trigger,
            "design": [],
            "touchpoints": [],
            "acceptance": ["<define before starting>"],
        })
        save(data)
        print(f"added {item_id}")
        return 0

    try:
        item = find(data, args.id)
    except BacklogError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.cmd == "start":
        item["status"] = "in_progress"
    elif args.cmd == "done":
        item["status"] = "done"
        item["resolved_on"] = date.today().isoformat()
    elif args.cmd == "block":
        item["status"] = "blocked"
    if args.note:
        item.setdefault("notes", []).append(f"{date.today().isoformat()}: {args.note}")

    save(data)
    print(f"{args.id} -> {item['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
