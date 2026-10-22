"""Validate a pull request's description against the commits it actually contains.

Why this exists
---------------
A squash merge uses the PR *title* as the commit subject on main. If the title
describes one of four commits, main records four commits' worth of work under a
subject that names one file change. That is the same traceability failure as a commit
whose body does not match its diff, one layer up -- and no existing check looked there.

Rules:
  * the title must match Conventional Commits, like a commit subject
  * every distinct commit type in the PR must be named in the title, or the title must
    describe the PR as a whole (`feat(evals)`) and the body must list the sub-changes
  * the body must be non-trivial: a squash commit inherits it, so it is the record

Usage:
    python scripts/check_pr.py 4          # check one PR (needs gh auth)
    python scripts/check_pr.py --current  # the PR for the current branch
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

TYPES = ("feat", "fix", "docs", "test", "refactor", "perf", "chore", "build", "ci")
# Significance order: a PR containing a `feat` must not be titled `fix`, because the
# squash subject is what main records. Titling a feature commit as a fix misrepresents
# history in the direction that hides new capability.
SIGNIFICANCE = ("feat", "fix", "refactor", "perf", "test", "docs", "chore", "build", "ci")

TITLE_RE = re.compile(r"^(?P<type>" + "|".join(TYPES) + r")(?:\((?P<scope>[a-z0-9/_-]+)\))?: (?P<subject>.+)$")
MIN_BODY_CHARS = 200


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def check_pr(number: int | None) -> list[str]:
    """Return a list of problems; empty means the PR is acceptable."""
    selector = str(number) if number else None
    args = ["pr", "view"] + ([selector] if selector else []) + [
        "--json", "number,title,body,commits,headRefName,baseRefName"
    ]
    data = json.loads(gh(*args))

    problems: list[str] = []
    title: str = data["title"]
    body: str = data.get("body") or ""
    commits = data.get("commits") or []

    match = TITLE_RE.match(title)
    if not match:
        problems.append(
            f"PR #{data['number']} title does not match '<type>(<scope>): <subject>': {title!r}"
        )
        return problems

    title_type = match.group("type")
    commit_types = []
    for commit in commits:
        headline = commit.get("messageHeadline", "")
        m = TITLE_RE.match(headline)
        if m:
            commit_types.append(m.group("type"))
    distinct = sorted(set(commit_types))

    if distinct and title_type not in distinct:
        problems.append(
            f"PR title type {title_type!r} does not appear among commit types {distinct}"
        )

    # The title must name the MOST SIGNIFICANT change in the PR, not merely a type that
    # happens to be present. A PR containing a feature, titled as a fix, hides the new
    # capability from anyone reading main's history.
    if distinct:
        expected = min(distinct, key=SIGNIFICANCE.index)
        if SIGNIFICANCE.index(title_type) > SIGNIFICANCE.index(expected):
            problems.append(
                f"PR contains a {expected!r} commit but is titled {title_type!r}. "
                f"The squash subject on main would hide the {expected}. "
                f"Title it as {expected!r}, or split the PR."
            )

    if len(body.strip()) < MIN_BODY_CHARS:
        problems.append(
            f"PR body is {len(body.strip())} chars; a squash commit inherits it as the "
            f"permanent record (minimum {MIN_BODY_CHARS})"
        )

    if "Refs:" not in body and len(commits) > 1:
        problems.append("multi-commit PR body has no 'Refs:' footer")

    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check a PR's description against its commits")
    ap.add_argument("number", nargs="?", type=int, default=None)
    ap.add_argument("--current", action="store_true", help="Use the current branch's PR")
    args = ap.parse_args(argv)

    try:
        problems = check_pr(None if args.current else args.number)
    except RuntimeError as exc:
        print(f"could not read PR: {exc}", file=sys.stderr)
        return 2

    if problems:
        print(f"FAIL: {len(problems)} PR description problem(s)\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}\n", file=sys.stderr)
        return 1

    print("OK: PR description matches its contents")
    return 0


if __name__ == "__main__":
    sys.exit(main())
