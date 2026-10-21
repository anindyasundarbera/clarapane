"""Detect serious defects: the checks that must never be skipped.

Why this exists
---------------
"You must fix blocking bugs first" is not a rule unless something enforces it. This
module turns that policy into executable checks, so the answer to "are there
blocking bugs?" is a command, not an opinion.

Three severities
----------------
  BLOCKER  The agent can do the wrong thing and report success, or the trace
            cannot be trusted. Work stops until fixed.
  MAJOR    A capability is unreliable or a claim is unverifiable. Fix before
            building on top of it.
  MINOR    Cosmetic or ergonomic. May be deferred, with a backlog entry.

Run it:
    python scripts/audit_defects.py            # human-readable report
    python scripts/audit_defects.py --strict   # exit 1 if any BLOCKER exists
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
TRACE = PROJECT_ROOT / "runs" / "runs.jsonl"
BACKLOG = PROJECT_ROOT / "backlog.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Runs started before this instant predate the terminal-event fix (commit ece5b5d,
# 2026-10-21T23:43+05:30). Violations are reported as historical rather than as
# live defects, because rewriting the trace to hide the gap would defeat the point.
TERMINAL_EVENT_FIX_CUTOFF = "2026-10-21T23:43:24+05:30"


def _is_pre_fix(run_id: str) -> bool:
    """run_id format: YYYYMMDDTHHMMSSZ-xxxxxx (UTC). Compare on the UTC instant."""
    try:
        stamp = run_id.split("-", 1)[0]  # 20261021T170125Z
        from datetime import datetime
        started = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
        cutoff = datetime.fromisoformat(TERMINAL_EVENT_FIX_CUTOFF)
        cutoff_utc = cutoff.replace(tzinfo=None) - cutoff.utcoffset()
        return started <= cutoff_utc
    except (ValueError, AttributeError):
        return False


@dataclass
class Defect:
    severity: str  # BLOCKER | MAJOR | MINOR
    check: str
    detail: str
    fix: str = ""

    def __str__(self) -> str:
        line = f"[{self.severity:<7}] {self.check}: {self.detail}"
        return line + (f"\n           fix: {self.fix}" if self.fix else "")


@dataclass
class Report:
    defects: list[Defect] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    def add(self, severity: str, check: str, detail: str, fix: str = "") -> None:
        self.defects.append(Defect(severity, check, detail, fix))

    def ok(self, check: str) -> None:
        self.passed.append(check)

    @property
    def blockers(self) -> list[Defect]:
        return [d for d in self.defects if d.severity == "BLOCKER"]

    def exit_code(self, strict: bool) -> int:
        if strict:
            return 1 if self.blockers else 0
        return 1 if any(d.severity in ("BLOCKER", "MAJOR") for d in self.defects) else 0


# --------------------------------------------------------------------- checks
def check_trace_terminal_events(report: Report) -> None:
    """Every run must end with exactly one terminal event.

    A run with run_start but no run_end is indistinguishable from a truncated
    trace, which makes "did it finish?" unanswerable.
    """
    if not TRACE.exists():
        report.ok("trace-terminal: no trace to check")
        return

    import collections

    per_run: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for line in TRACE.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        per_run[record["run_id"]][record["event"]] += 1

    bad = [
        (rid, counts["run_start"], counts["run_end"])
        for rid, counts in per_run.items()
        if counts["run_start"] != counts["run_end"]
    ]
    live = [b for b in bad if not _is_pre_fix(b[0])]
    legacy = [b for b in bad if _is_pre_fix(b[0])]
    if live:
        report.add(
            "MAJOR", "trace-terminal",
            f"{len(live)} run(s) started AFTER the fix still lack a matching run_end: "
            + ", ".join(f"{r} ({s}/{e})" for r, s, e in live),
            "a regression in the terminal-event invariant",
        )
    else:
        note = f" ({len(legacy)} pre-fix run(s) excluded)" if legacy else ""
        report.ok(f"trace-terminal{note}")


def check_no_unverified_success(report: Report) -> None:
    """No run may report finish while its own trace marks the claim unverified.

    This is the false-success guard: a confident summary without a mutating tool
    call is a claim, not evidence.
    """
    if not TRACE.exists():
        report.ok("no-unverified-success: no trace to check")
        return

    offenders = []
    for line in TRACE.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["event"] == "finish" and record.get("verified") is False:
            offenders.append(record["run_id"])

    if offenders:
        report.add(
            "BLOCKER", "no-unverified-success",
            f"{len(set(offenders))} run(s) recorded finish.verified=False: {sorted(set(offenders))}",
            "a run must not be reported as finished when its claim is unproven",
        )
    else:
        report.ok("no-unverified-success")


def check_trace_schema(report: Report) -> None:
    """The committed trace must satisfy the declared event schema."""
    if not TRACE.exists():
        report.ok("trace-schema: no trace to check")
        return

    from glassbox import events_schema

    problems = events_schema.audit(TRACE.read_text().splitlines())
    if problems:
        report.add(
            "MAJOR", "trace-schema",
            f"{len(problems)} schema problem(s): " + "; ".join(
                f"line {p.lineno} [{p.event}] {p.detail}" for p in problems[:3]
            ),
            "fix the emit site or declare the field in events_schema.py",
        )
    else:
        report.ok("trace-schema")


def check_single_log_writer(report: Report) -> None:
    """Only EventLog may open the trace file.

    If a second writer appears, the trace stops being a complete record.
    """
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "events.py":
            continue
        text = path.read_text()
        if "open(" in text and ("runs.jsonl" in text or '"a", encoding' in text):
            offenders.append(str(path.relative_to(PROJECT_ROOT)))
    if offenders:
        report.add(
            "BLOCKER", "single-log-writer",
            f"possible second trace writer(s): {offenders}",
            "route all events through EventLog.emit()",
        )
    else:
        report.ok("single-log-writer")


def check_backlog_integrity(report: Report) -> None:
    """Open work must be tracked with a trigger and acceptance criteria."""
    from glassbox import backlog

    try:
        data = backlog.load()
    except Exception as exc:  # noqa: BLE001 - report, do not crash the audit
        report.add("MAJOR", "backlog-integrity", f"backlog unreadable: {exc}",
                   "run python -m glassbox.backlog to inspect")
        return

    unstarted = [
        i["id"] for i in data["items"]
        if i["status"] in backlog.OPEN_STATUSES and i.get("priority") == "high"
        and any("<define" in a for a in i.get("acceptance", []))
    ]
    if unstarted:
        report.add(
            "MAJOR", "backlog-integrity",
            f"high-priority items have placeholder acceptance criteria: {unstarted}",
            "define acceptance criteria before starting the work",
        )
    else:
        report.ok("backlog-integrity")


def check_env_not_tracked(report: Report) -> None:
    """A committed .env is a permanent secret leak."""
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", ".env"], cwd=PROJECT_ROOT, capture_output=True, text=True
    ).stdout.strip()
    if tracked:
        report.add(
            "BLOCKER", "env-not-tracked",
            ".env is tracked by git; the key is in history",
            "rotate the key, then rewrite history",
        )
    else:
        report.ok("env-not-tracked")


def check_agent_invariants(report: Report) -> None:
    """Invariants that tests also cover, checked statically as a backstop."""
    agent_src = (SRC / "glassbox" / "sdk_agent.py").read_text()

    if 'self.log.emit("stall"' in agent_src and agent_src.count('"run_end"') < 2:
        report.add(
            "BLOCKER", "agent-invariants",
            "a stall path exists without a matching run_end",
            "every exit must emit exactly one terminal event",
        )
    else:
        report.ok("agent-invariants")

    if "tool_choice" in agent_src and "verified" not in agent_src:
        report.add(
            "BLOCKER", "agent-invariants",
            "forced finish without a verification guard allows false success",
            "refuse success when mutation was offered but never used",
        )


def check_all() -> Report:
    report = Report()
    for check in (
        check_env_not_tracked,
        check_trace_terminal_events,
        check_no_unverified_success,
        check_trace_schema,
        check_single_log_writer,
        check_backlog_integrity,
        check_agent_invariants,
    ):
        check(report)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Audit for serious and blocking defects")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero only when a BLOCKER is found")
    args = ap.parse_args(argv)

    report = check_all()

    print(f"checks passed: {len(report.passed)}")
    for name in report.passed:
        print(f"  ok  {name}")

    if report.defects:
        print(f"\ndefects: {len(report.defects)} "
              f"({len(report.blockers)} blocker)")
        for defect in report.defects:
            print(f"\n  {defect}")
    else:
        print("\nno defects found")

    return report.exit_code(args.strict)


if __name__ == "__main__":
    sys.exit(main())
