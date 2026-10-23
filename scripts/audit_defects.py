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
PLAN = PROJECT_ROOT / "plan.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Each check that can be invalidated by old data names the instant its fix landed.
# Runs started at or before that instant are reported as historical, not as live
# defects: rewriting old trace records to satisfy a new rule would erase the very
# evidence of the bug the rule exists for.
FIX_CUTOFFS: dict[str, str] = {
    # run_end emitted on every exit path (commit ece5b5d)
    "trace-terminal": "2026-10-21T23:43:24+05:30",
    # one `finish` event per run, always carrying `verified`
    "one-finish-per-run": "2026-10-22T00:45:00+05:30",
    # approvals must name an operator and bind to a payload fingerprint
    "approval-binding": "2026-10-22T01:00:00+05:30",
    # every commit on main must arrive through a pull request (existence, not review)
    "no-direct-main-commits": "2026-10-22T00:40:00+05:30",
}


def _predates(run_id: str, check: str) -> bool:
    """True when this run started before the fix named by `check`."""
    cutoff_text = FIX_CUTOFFS.get(check)
    if cutoff_text is None:
        return False
    try:
        from datetime import datetime

        started = datetime.strptime(run_id.split("-", 1)[0], "%Y%m%dT%H%M%SZ")
        cutoff = datetime.fromisoformat(cutoff_text)
        return started <= (cutoff.replace(tzinfo=None) - cutoff.utcoffset())
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
    live = [b for b in bad if not _predates(b[0], "trace-terminal")]
    legacy = [b for b in bad if _predates(b[0], "trace-terminal")]
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

    # A verified=False event is CORRECT when the run also reports failure. The
    # defect is a run whose terminal reason claims success while its own finish
    # event says the claim was unproven.
    SUCCESS_REASONS = {"finish", "finish_recovered"}
    finishes: dict[str, dict] = {}
    endings: dict[str, str] = {}
    for line in TRACE.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["event"] == "finish":
            finishes[record["run_id"]] = record
        elif record["event"] == "run_end":
            endings[record["run_id"]] = record.get("reason", "")

    offenders = [
        run_id for run_id, record in finishes.items()
        if record.get("verified") is False
        and endings.get(run_id) in SUCCESS_REASONS
        and not _predates(run_id, "no-unverified-success")
    ]

    if offenders:
        report.add(
            "BLOCKER", "no-unverified-success",
            f"{len(set(offenders))} run(s) claim success while finish.verified=False: "
            f"{sorted(set(offenders))}",
            "a run must not be reported as finished when its claim is unproven",
        )
    else:
        report.ok("no-unverified-success")


def check_trace_schema(report: Report) -> None:
    """The committed trace must satisfy the declared event schema."""
    if not TRACE.exists():
        report.ok("trace-schema: no trace to check")
        return

    from clarapane import events_schema

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
    from clarapane import backlog

    try:
        data = backlog.load()
    except Exception as exc:  # noqa: BLE001 - report, do not crash the audit
        report.add("MAJOR", "backlog-integrity", f"backlog unreadable: {exc}",
                   "run python -m clarapane.backlog to inspect")
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
    agent_src = (SRC / "clarapane" / "sdk_agent.py").read_text()

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


def check_one_finish_event_per_run(report: Report) -> None:
    """A run must emit at most one `finish` event, and it must carry `verified`.

    A run emitted two: one from tool dispatch without `verified`, then one from the
    loop with it. Two records for one decision make "did the agent prove it?" a
    question with several answers, which is worse than one wrong answer.
    """
    if not TRACE.exists():
        report.ok("one-finish-per-run: no trace to check")
        return

    import collections

    finishes: dict[str, list[dict]] = collections.defaultdict(list)
    for line in TRACE.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["event"] == "finish" and not _predates(record["run_id"], "one-finish-per-run"):
            finishes[record["run_id"]].append(record)

    problems = []
    for run_id, records in finishes.items():
        if len(records) > 1:
            problems.append(f"{run_id} has {len(records)} finish events")
        elif "verified" not in records[0]:
            problems.append(f"{run_id} finish lacks 'verified'")

    if problems:
        report.add(
            "MAJOR", "one-finish-per-run",
            "; ".join(problems[:3]),
            "emit the finish event once, from the loop, with the verified flag",
        )
    else:
        report.ok("one-finish-per-run")


def check_divergence_taxonomy(report: Report) -> None:
    """Every trace that records divergence must use a declared class.

    Why this is a defect check and not a metric
    -------------------------------------------
    The divergence count is only meaningful if its taxonomy is closed. If an
    undeclared class could be written, the count would grow into a misc bucket and
    stop driving action. So an unknown class in a trace is a BLOCKER: either the
    code is wrong or the taxonomy needs a deliberate decision.

    It also enforces that the emitted `source` matches the ruleset. A trace claiming
    a class is "model" divergence when the taxonomy says it is "harness" would make
    our own bugs invisible in the metric.
    """
    from clarapane import divergence

    runs = sorted((SRC.parent / "runs").glob("*.jsonl"))
    if not runs:
        report.ok("divergence-taxonomy: no trace to check")
        return

    problems: list[str] = []
    for path in runs:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("event") != "divergence":
                continue
            name = record.get("class_")
            rule = divergence.RULES.get(name)
            if rule is None:
                problems.append(f"{path.name}: undeclared class {name!r}")
                continue
            if record.get("source") != rule.source:
                problems.append(
                    f"{path.name}: {name} recorded as source={record.get('source')!r} "
                    f"but the taxonomy says {rule.source!r}"
                )

    if problems:
        report.add(
            "BLOCKER", "divergence-taxonomy", "; ".join(problems[:3]),
            "declare the class in divergence.py with an owner and a remedy, "
            "or fix the emitting code",
        )
    else:
        report.ok("divergence-taxonomy")


def check_plan_integrity(report: Report) -> None:
    """The training plan must exist, be well-formed, and have exactly one active step.

    Section 0a of AGENTS.md makes deviation a defect. A defect needs a reference point:
    without a machine-readable plan, "did we deviate?" is an opinion. This check keeps
    the reference point honest -- exactly one step in progress, steps in order, and no
    step marked done without saying what it taught.
    """
    if not PLAN.exists():
        report.add("MAJOR", "plan-integrity", "plan.json is missing",
                   "restore the training plan; see AGENTS.md section 0a")
        return

    try:
        data = json.loads(PLAN.read_text())
    except json.JSONDecodeError as exc:
        report.add("MAJOR", "plan-integrity", f"plan.json is invalid JSON: {exc}", "")
        return

    steps = data.get("steps", [])
    if not steps:
        report.add("MAJOR", "plan-integrity", "plan.json declares no steps", "")
        return

    problems: list[str] = []
    in_progress = [s["id"] for s in steps if s.get("status") == "in_progress"]
    if len(in_progress) != 1:
        problems.append(f"expected exactly one in_progress step, found {in_progress}")

    current = data.get("current_step")
    if in_progress and current not in in_progress:
        problems.append(f"current_step={current!r} does not match in_progress={in_progress}")

    # A step cannot start while an earlier one is unfinished: that is the deviation
    # this whole mechanism exists to catch.
    statuses = [s.get("status") for s in steps]
    for index, status in enumerate(statuses):
        if status in ("in_progress", "done") and index > 0:
            earlier = statuses[:index]
            if any(st not in ("done",) for st in earlier):
                problems.append(
                    f"step {steps[index]['id']!r} is {status} while an earlier step is not done"
                )

    for step in steps:
        if step.get("status") == "done" and not step.get("covered"):
            problems.append(f"done step {step['id']!r} does not say what it covered")

    if problems:
        report.add("MAJOR", "plan-integrity", "; ".join(problems[:3]),
                   "fix plan.json; deviation from the plan is a defect (AGENTS.md 0a)")
    else:
        report.ok(f"plan-integrity (current: {current})")


def check_no_direct_main_commits(report: Report) -> None:
    """Commits that landed on main without going through a pull request.

    A local hook prevents this and a GitHub ruleset blocks it, but both can be
    bypassed (--no-verify, another clone, an admin override). This is the detective
    control: it reports what actually happened rather than what was supposed to.

    A commit counts as having gone through a PR when its message carries a PR
    reference in the Refs: footer, which `gh pr merge --squash` produces.

    This verifies that a PR EXISTED. It does not verify that anyone reviewed it --
    the repository has one collaborator and the ruleset requires zero approvals.
    Naming it "unreviewed" would overstate what the check knows.
    """
    import re
    import subprocess

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=PROJECT_ROOT,
                              capture_output=True, text=True).stdout

    # Only commits reachable from main count; a feature branch may hold whatever.
    main_ref = "origin/main"
    if subprocess.run(["git", "rev-parse", "--verify", main_ref], cwd=PROJECT_ROOT,
                      capture_output=True).returncode != 0:
        main_ref = "main"
    if subprocess.run(["git", "rev-parse", "--verify", main_ref], cwd=PROJECT_ROOT,
                      capture_output=True).returncode != 0:
        report.ok("no-direct-main-commits: no main branch")
        return

    raw = git("log", "--format=%H%x00%s%x00%b%x00", main_ref)
    parts = [x for x in raw.split("\x00")]
    # Bootstrap commits created before the policy are exempt by design.
    BOOTSTRAP = set()
    for line in git("log", "--format=%h %s", main_ref).splitlines():
        if line.split(" ", 1)[-1].startswith("chore: establish repo guardrails"):
            BOOTSTRAP.add(line.split(" ", 1)[0])

    # Commits made before the policy existed are history, not violations. Their
    # timestamps, not their subjects, decide this, so a bootstrap-shaped subject
    # cannot be used to sneak a later commit past the check.
    cutoff_text = FIX_CUTOFFS["no-direct-main-commits"]
    from datetime import datetime

    cutoff = datetime.fromisoformat(cutoff_text)

    unreviewed: list[str] = []
    for i in range(0, len(parts) - 2, 3):
        sha, subject, body = parts[i].strip(), parts[i + 1], parts[i + 2]
        if not sha:
            continue
        committed = git("show", "-s", "--format=%cI", sha).strip()
        try:
            when = datetime.fromisoformat(committed)
        except ValueError:
            when = cutoff
        if when <= cutoff:
            continue
        # `gh pr merge --squash` appends " (#N)" to the SUBJECT, not the body. Accept
        # both, because the PR reference is what proves a review happened, and where
        # GitHub puts it is not something we control.
        if re.search(r"\(#\d+\)\s*$", subject) or re.search(
            r"^(Refs:.*)?(#|pull/)\d+", body, re.MULTILINE
        ):
            continue
        unreviewed.append(f"{sha[:7]} {subject[:48]}")

    if unreviewed:
        report.add(
            "MAJOR", "no-direct-main-commits",
            f"{len(unreviewed)} commit(s) on {main_ref} carry no PR reference: "
            + "; ".join(unreviewed[:3]),
            "land changes via a pull request; the squash commit records the PR number",
        )
    else:
        report.ok("no-direct-main-commits")


def check_approval_binding(report: Report) -> None:
    """Every approval must name an accountable operator and bind to a payload.

    Two defects this closes: an approval recorded only `actor="human"`, which is a
    category rather than a person; and an approval with no fingerprint, so the trace
    could not prove which action the operator actually authorized.
    """
    if not TRACE.exists():
        report.ok("approval-binding: no trace to check")
        return

    problems: list[str] = []
    for line in TRACE.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["event"] != "approval":
            continue
        if _predates(record["run_id"], "approval-binding"):
            continue
        missing = [f for f in ("operator", "fingerprint") if not record.get(f)]
        if missing:
            problems.append(f"{record['run_id']} approval missing {missing}")

    if problems:
        report.add(
            "MAJOR", "approval-binding",
            "; ".join(problems[:3]),
            "record who approved and a fingerprint of the exact payload",
        )
    else:
        report.ok("approval-binding")


def check_evidence_not_abandoned(report: Report) -> None:
    """No tracked run artifact may be modified but unstaged.

    Twice-confirmed failure: a commit staged source changes and left runs/runs.jsonl
    behind, so its own "Evidence:" section cited a trace that was not in the commit.
    The pre-commit hook catches this at commit time; this check catches it whenever
    the audit runs, including in CI where hooks are not installed.
    """
    import subprocess

    def git(*args: str) -> list[str]:
        out = subprocess.run(["git", *args], cwd=PROJECT_ROOT,
                             capture_output=True, text=True).stdout
        return [line for line in out.splitlines() if line.strip()]

    abandoned = [f for f in git("diff", "--name-only") if f.startswith("runs/")]
    if abandoned:
        report.add(
            "MAJOR", "evidence-abandoned",
            f"tracked run artifact(s) modified but unstaged: {abandoned}",
            "git add runs/ so the commit carries the evidence it cites",
        )
    else:
        report.ok("evidence-abandoned")


def check_env_matches_template(report: Report) -> None:
    """Every key in .env.example must exist in .env (names only, never values)."""
    example = PROJECT_ROOT / ".env.example"
    env = PROJECT_ROOT / ".env"
    if not env.exists():
        report.ok("env-matches-template: no local .env")
        return

    def keys(text: str) -> set[str]:
        found: set[str] = set()
        for line in text.splitlines():
            body = line.strip().lstrip("#").strip()
            if "=" not in body:
                continue
            key, _, value = body.partition("=")
            key = key.strip()
            if key.isidentifier() and key.upper() == key and " " not in value.strip():
                found.add(key)
        return found

    missing = sorted(keys(example.read_text()) - keys(env.read_text()))
    if missing:
        report.add(
            "MAJOR", "env-matches-template",
            f".env is missing keys declared in .env.example: {missing}",
            "add them to .env or the feature is documented but inert",
        )
    else:
        report.ok("env-matches-template")


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
        check_evidence_not_abandoned,
        check_env_matches_template,
        check_one_finish_event_per_run,
        check_approval_binding,
        check_no_direct_main_commits,
        check_plan_integrity,
        check_divergence_taxonomy,
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
