"""Validate commit messages in the repository history.

Run manually:
    python scripts/check_commits.py
    python scripts/check_commits.py --range origin/main..HEAD

This is also exercised by the test suite, so a malformed message fails CI even
when the commit-msg hook was bypassed with --no-verify.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

TYPES = ("feat", "fix", "docs", "test", "refactor", "perf", "chore", "build", "ci")
SUBJECT_RE = re.compile(
    r"^(?P<type>" + "|".join(TYPES) + r")(?:\((?P<scope>[a-z0-9/_-]+)\))?: (?P<subject>.+)$"
)
EXEMPT_PREFIXES = ("Merge ", "Revert ", "fixup! ", "squash! ")
MAX_SUBJECT_LINE = 72
REQUIRED_FOOTER_HINTS = ("Refs:",)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "--no-pager", *args], capture_output=True, text=True, check=True
    ).stdout


def commits(revision_range: str | None) -> list[tuple[str, str]]:
    rev = revision_range or "HEAD"
    raw = git("log", "--format=%H%x00%B%x00", rev)
    parts = [p for p in raw.split("\x00") if p.strip()]
    out: list[tuple[str, str]] = []
    for i in range(0, len(parts) - 1, 2):
        out.append((parts[i].strip(), parts[i + 1].strip()))
    return out


def check(sha: str, message: str) -> list[str]:
    """Return a list of problems; empty means the message is acceptable."""
    problems: list[str] = []
    lines = message.splitlines()
    if not lines:
        return [f"{sha[:8]}: empty commit message"]

    subject = lines[0]
    if subject.startswith(EXEMPT_PREFIXES):
        return []

    match = SUBJECT_RE.match(subject)
    if not match:
        return [
            f"{sha[:8]}: subject does not match '<type>(<scope>): <subject>'\n"
            f"           got: {subject!r}\n"
            f"           allowed types: {', '.join(TYPES)}"
        ]

    if len(subject) > MAX_SUBJECT_LINE:
        problems.append(
            f"{sha[:8]}: subject line is {len(subject)} chars (max {MAX_SUBJECT_LINE})"
        )

    detail = match.group("subject")
    if detail[0].isupper():
        problems.append(f"{sha[:8]}: subject should start lowercase: {detail!r}")
    if detail.endswith("."):
        problems.append(f"{sha[:8]}: subject should not end with a period")

    # Traceability: a body that explains itself is the whole point.
    body = "\n".join(lines[1:]).strip()
    if not body:
        problems.append(f"{sha[:8]}: missing body (explain Why / What / Decision)")
    elif not any(hint in body for hint in REQUIRED_FOOTER_HINTS):
        problems.append(
            f"{sha[:8]}: body has no 'Refs:' footer (backlog id, phase, or issue link)"
        )

    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check commit message format")
    ap.add_argument("--range", dest="revision_range", default=None,
                    help="Git revision range, e.g. origin/main..HEAD")
    args = ap.parse_args(argv)

    try:
        entries = commits(args.revision_range)
    except subprocess.CalledProcessError as exc:
        print(f"git failed: {exc.stderr.strip()}", file=sys.stderr)
        return 2

    if not entries:
        print("no commits to check")
        return 0

    problems: list[str] = []
    for sha, message in entries:
        problems.extend(check(sha, message))

    if problems:
        print(f"FAIL: {len(problems)} commit message problem(s)\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}\n", file=sys.stderr)
        return 1

    print(f"OK: {len(entries)} commit message(s) conform")
    return 0


if __name__ == "__main__":
    sys.exit(main())
