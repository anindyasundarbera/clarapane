"""Verify that documentation claims match the code, or fail.

This is the seed of the documentation harness, not the harness itself. It does the
one thing that would have prevented the drift this repository already has: it checks
claims instead of trusting them.

Why a script and not a docs generator
-------------------------------------
The measured problem was never "we have no docs". It was:

  * docs/BACKLOG.md was missing 7 of 10 backlog items;
  * identity.banner() hardcoded a version that pyproject.toml also declared;
  * the hooks are inert in a fresh clone, and nothing said so.

All three are *claims that disagree with the code*. A generator would help only the
first; a checker catches all three. So the checker comes first, and generation is
added later only for content that is 100% derivable (see the research plan, G2).

Usage:
    python scripts/check_docs.py            # verify, exit 1 on drift
    python scripts/check_docs.py --list     # show what is checked
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


@dataclass(frozen=True)
class Finding:
    doc: str
    problem: str

    def __str__(self) -> str:
        return f"{self.doc}: {self.problem}"


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


# --------------------------------------------------------------------- checks
def _declared_env_keys() -> set[str]:
    """Every key `.env.example` declares, including deliberately commented-out ones.

    Optional keys are documented by commenting them out (`# CLARAPANE_APP_NAME=...`),
    so a checker that only reads active `KEY=` lines reports them as undeclared and
    produces a false positive. A false positive is worse than a miss here: it trains
    the reader to ignore the check.
    """
    example = _read(PROJECT_ROOT / ".env.example")
    active = set(re.findall(r"^([A-Z_]+)=", example, re.M))
    optional = set(re.findall(r"^#\s*([A-Z_]+)=", example, re.M))
    return active | optional


def check_readme_exists() -> list[Finding]:
    """A repository with no entry point is documented only for its author."""
    if not (PROJECT_ROOT / "README.md").exists():
        return [Finding("README.md", "missing -- there is no entry point")]
    return []


def check_profile_table() -> list[Finding]:
    """The README's profile table must list the profiles that actually exist.

    Scoped to the section that documents profiles: an earlier version matched every
    a table row in the file and reported the environment keys, the divergence
    sources and the profile names all as "profiles". A checker with false positives
    gets muted, which is worse than no checker.
    """
    from clarapane import profiles

    readme = _read(PROJECT_ROOT / "README.md")
    section = re.search(
        r"^#+ .*[Cc]apability profiles.*?$(.*?)(?=^#+ |\Z)", readme, re.S | re.M
    )
    if not section:
        return [Finding("README.md", "no capability-profile section found to verify")]

    body = section.group(1)
    table_rows = re.findall(r"^\| `(\w+)` \|", body, re.M)
    missing = [name for name in profiles.PROFILES if name not in table_rows]
    extra = [name for name in table_rows if name not in profiles.PROFILES]

    out = [Finding("README.md", f"profile {n!r} exists but is not documented") for n in missing]
    out += [Finding("README.md", f"profile {n!r} is documented but does not exist") for n in extra]
    return out


def check_event_list() -> list[Finding]:
    """The trace event list must match the declared schema, excluding legacy events."""
    from clarapane.events_schema import LEGACY_EVENTS, SCHEMAS

    readme = _read(PROJECT_ROOT / "README.md")
    match = re.search(r"```\n(run_start.*?)\n```", readme, re.S)
    if not match:
        return [Finding("README.md", "no trace-event block found to verify")]

    documented = sorted(match.group(1).split())
    actual = sorted(set(SCHEMAS) - set(LEGACY_EVENTS))
    if documented != actual:
        return [Finding(
            "README.md",
            f"event list drift: documented={documented} actual={actual}. "
            "Undocumented events cannot be consumed programmatically.",
        )]
    return []


def check_make_targets() -> list[Finding]:
    """Every `make <target>` named in the docs must exist."""
    makefile = _read(PROJECT_ROOT / "Makefile")
    defined = set(re.findall(r"^([a-z][\w-]*):", makefile, re.M))

    out: list[Finding] = []
    for doc in ("README.md", "AGENTS.md", "CHANGELOG.md"):
        text = _read(PROJECT_ROOT / doc)
        for target in set(re.findall(r"`make ([a-z][\w-]*)", text)):
            if target not in defined:
                out.append(Finding(doc, f"`make {target}` is documented but not defined"))
    return out


def check_backlog_ids() -> list[Finding]:
    """Deferred work must not silently vanish from the human-facing view.

    This is the drift measured before the harness existed: 7 of 10 backlog ids were
    absent from docs/BACKLOG.md while it presented a table of current items.

    Two honest resolutions are accepted, and duplication is not one of them:

      1. the doc lists every open id, or
      2. the doc states that `backlog.json` is authoritative and points at
         `make backlog`, so the list cannot drift because it is not duplicated.

    Option 2 is preferred: a hand-maintained copy of machine-readable data always
    drifts eventually. A checker must accept the fix that removes the cause, not
    demand the fix that recreates it.
    """
    import json

    data = json.loads(_read(PROJECT_ROOT / "backlog.json") or "{}")
    items = data.get("items", [])
    if not items:
        return [Finding("backlog.json", "no items to cross-check")]

    doc = _read(PROJECT_ROOT / "docs" / "BACKLOG.md")
    if not doc:
        return [Finding("docs/BACKLOG.md", "missing")]

    delegates = (
        "make backlog" in doc
        and "`backlog.json`" in doc
        and re.search(r"authoritative", doc, re.I) is not None
    )

    # Only open work must be discoverable; done items are history, and `rejected`
    # items stay for a different reason (don't re-propose without evidence).
    open_ids = [
        i["id"] for i in items
        if i.get("status") in ("planned", "in_progress", "blocked")
    ]
    missing = [i for i in open_ids if i not in doc]

    if delegates:
        return []
    return [
        Finding(
            "docs/BACKLOG.md",
            f"open item {i!r} is neither listed nor delegated. Either list it, or "
            "state that backlog.json is authoritative and point at `make backlog`; "
            "a hand-maintained duplicate will drift.",
        )
        for i in missing
    ]


def check_version_single_source() -> list[Finding]:
    """The version must be declared once, in pyproject.toml, and read from there."""
    pyproject = _read(PROJECT_ROOT / "pyproject.toml")
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    if not match:
        return [Finding("pyproject.toml", "declares no version")]

    out: list[Finding] = []
    identity = _read(PROJECT_ROOT / "src" / "clarapane" / "identity.py")
    if re.search(r'banner\([^)]*version:\s*str\s*=\s*"[0-9]', identity):
        out.append(Finding(
            "src/clarapane/identity.py",
            "banner() hardcodes a version, creating a second source of truth",
        ))

    changelog = _read(PROJECT_ROOT / "CHANGELOG.md")
    if changelog and f"## [{match.group(1)}]" not in changelog:
        out.append(Finding(
            "CHANGELOG.md", f"no section for the declared version {match.group(1)!r}"
        ))
    if changelog:
        try:
            if changelog.index("## [Unreleased]") > changelog.index(f"## [{match.group(1)}]"):
                out.append(Finding("CHANGELOG.md", "Unreleased is not the first section"))
        except ValueError:
            pass
    return out


def check_setup_document_matches_code() -> list[Finding]:
    """docs/SETUP.md is the migration guide, so a wrong step is worse than no step.

    It documents things that are deliberately NOT in git -- credentials, local git
    config, CI secrets -- which makes it the document most likely to rot silently,
    because nothing fails when it is wrong. It is checked like any other claim.
    """
    setup = _read(PROJECT_ROOT / "docs" / "SETUP.md")
    if not setup:
        return [Finding("docs/SETUP.md", "missing -- migration steps are unrecorded")]

    out: list[Finding] = []

    documented = set(re.findall(r"^\| `([A-Z_]+)` \|", setup, re.M))
    undeclared = documented - _declared_env_keys()
    if undeclared:
        out.append(Finding(
            "docs/SETUP.md",
            f"documents env keys that .env.example does not declare: {sorted(undeclared)}",
        ))

    # Verify in the direction that catches a gap: every real hook must be mentioned.
    # Scraping names out of the prose only catches typos, and misses a hook that was
    # added to the repository and never documented.
    hook_dir = PROJECT_ROOT / ".githooks"
    if hook_dir.exists():
        for hook in sorted(p.name for p in hook_dir.iterdir() if p.is_file()):
            if hook not in setup:
                out.append(Finding(
                    "docs/SETUP.md",
                    f"hook {hook!r} exists but the setup guide never mentions it",
                ))

    for script in set(re.findall(r"scripts/([a-z_]+\.py)", setup)):
        if not (PROJECT_ROOT / "scripts" / script).exists():
            out.append(Finding("docs/SETUP.md", f"invokes scripts/{script} which does not exist"))

    workflow = _read(PROJECT_ROOT / ".github" / "workflows" / "ci.yml")
    # Search the whole guide, not only the env-key table: the `gh secret set` block is
    # where a reader is actually told to create them, and requiring the table alone
    # would report a documented secret as missing.
    for secret in set(re.findall(r"secrets\.([A-Z_]+)", workflow)):
        if secret not in setup:
            out.append(Finding(
                "docs/SETUP.md", f"CI uses secret {secret} but the setup guide omits it"
            ))

    pyproject = _read(PROJECT_ROOT / "pyproject.toml")
    match = re.search(r'requires-python\s*=\s*">=([\d.]+)"', pyproject)
    if match and f">= {match.group(1)}" not in setup:
        out.append(Finding(
            "docs/SETUP.md", f"does not state the Python floor >= {match.group(1)}"
        ))
    return out


def check_agent_facing_reference_is_bounded() -> list[Finding]:
    """An agent reading a full reference would blow its context.

    Requirement from the research plan (G13): the agent-facing rendering must be
    bounded like every other tool. Until a renderer exists, this asserts the
    documented intent, so the constraint cannot be silently dropped.
    """
    if not (PROJECT_ROOT / "src" / "clarapane" / "docs_render.py").exists():
        return []  # renderer not built yet; nothing to bound
    render = _read(PROJECT_ROOT / "src" / "clarapane" / "docs_render.py")
    if "MAX_" not in render and "limit" not in render:
        return [Finding(
            "src/clarapane/docs_render.py",
            "agent-facing renderer has no output cap; prose in context is unbounded",
        )]
    return []


def check_capability_tools_exist() -> list[Finding]:
    """Every tool named in the capability document must actually be registered.

    docs/CAPABILITIES.md claims the agent can do things. A tool removed from the code
    but left in the document is the same defect class as the `exec` profile that
    advertised tools which did not exist -- the document then promises capability the
    system does not have.
    """
    doc = _read(PROJECT_ROOT / "docs" / "CAPABILITIES.md")
    if not doc:
        return [Finding("docs/CAPABILITIES.md", "missing")]

    from clarapane.policy import Workspace
    from clarapane.proposals import ProposalStore
    from clarapane.registry import ToolRegistry
    from clarapane.tools.exec_tools import register_exec_tools
    from clarapane.tools.finish_tool import register_finish_tool
    from clarapane.tools.git_tools import register_git_tools
    from clarapane.tools.read_tools import register_read_tools
    from clarapane.tools.write_tools import register_write_tools

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        registry = ToolRegistry()
        workspace = Workspace(root=Path(tmp))
        register_read_tools(registry, workspace)
        register_write_tools(registry, workspace, ProposalStore())
        register_exec_tools(registry, workspace)
        register_git_tools(registry, workspace)
        register_finish_tool(registry)
        registered = set(registry.names())

    # Only prose inside the capability tables is treated as a tool claim. The document
    # also names profiles (`read_only`, `edit`, `exec`) and commands (`make test`), and
    # an earlier prefix-based filter both missed invented tools (`teleport_files`) and
    # risked flagging non-tools. Table rows are the unambiguous place a tool can appear.
    table_names: set[str] = set()
    for line in doc.splitlines():
        if not line.startswith("|"):
            continue
        first_cell = line.split("|")[1].strip()
        for name in re.findall(r"`([a-z_][a-z0-9_]*)`", first_cell):
            table_names.add(name)

    out: list[Finding] = []
    for name in sorted(table_names):
        if name not in registered:
            out.append(Finding(
                "docs/CAPABILITIES.md",
                f"table names tool {name!r}, which is not registered",
            ))
    return out


def check_capability_cites_real_proof() -> list[Finding]:
    """A capability claim must point at a test file that exists.

    "Verified by tests/foo.py" is only meaningful if the file is there. Docs that cite
    deleted tests are worse than docs with no citations, because they look rigorous.
    """
    doc = _read(PROJECT_ROOT / "docs" / "CAPABILITIES.md")
    if not doc:
        return []

    out: list[Finding] = []
    for ref in sorted(set(re.findall(r"tests/test_[a-z_]+\.py", doc))):
        if not (PROJECT_ROOT / ref).exists():
            out.append(Finding("docs/CAPABILITIES.md", f"cites {ref}, which does not exist"))
    return out


def check_ci_claims_match_reality() -> list[Finding]:
    """A document must not claim CI is absent when a workflow exists.

    Real instance: README said "No CI exists yet. Nothing runs on push" for months after
    .github/workflows/ci.yml was added. No check looked at prose claims about CI, so the
    drift survived -- and a stale claim about the safety net is exactly what makes people
    stop trusting the safety net.
    """
    workflows = list((PROJECT_ROOT / ".github" / "workflows").glob("*.yml"))
    has_ci = bool(workflows)

    out: list[Finding] = []
    for name in ("README.md", "AGENTS.md", "docs/CAPABILITIES.md", "docs/ARCHITECTURE.md"):
        text = _read(PROJECT_ROOT / name)
        if not text:
            continue
        lowered = text.lower()
        claims_absent = (
            "no ci exists" in lowered
            or "no ci yet" in lowered
            or "nothing runs on push" in lowered
        )
        if claims_absent and has_ci:
            out.append(Finding(name, "claims CI does not exist, but a workflow is present"))
    return out


def check_intent_documents_goal() -> list[Finding]:
    """The project must state what it is for, or scope drift has no reference point.

    Section 0a of AGENTS.md makes deviation a defect; `plan.json` orders the steps; this
    document says why any of it matters. Without it, "should we build this?" has no
    answer better than taste.
    """
    doc = _read(PROJECT_ROOT / "docs" / "INTENT.md")
    if not doc:
        return [Finding("docs/INTENT.md", "missing")]
    out: list[Finding] = []
    for required in ("## The goal", "## Explicitly not the goal", "## What \"done\" looks like"):
        if required not in doc:
            out.append(Finding("docs/INTENT.md", f"missing section {required!r}"))
    return out


CHECKS = (
    check_readme_exists,
    check_profile_table,
    check_event_list,
    check_make_targets,
    check_backlog_ids,
    check_version_single_source,
    check_setup_document_matches_code,
    check_agent_facing_reference_is_bounded,
    check_capability_tools_exist,
    check_capability_cites_real_proof,
    check_ci_claims_match_reality,
    check_intent_documents_goal,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify documentation claims against code")
    ap.add_argument("--list", action="store_true", help="Show what is checked, then exit")
    args = ap.parse_args(argv)

    if args.list:
        for check in CHECKS:
            print(f"{check.__name__}: {(check.__doc__ or '').strip().splitlines()[0]}")
        return 0

    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check())

    if findings:
        print(f"{len(findings)} documentation drift finding(s):")
        for finding in findings:
            print(f"  - {finding}")
        print("\nFix the docs, or change the code so the doc stops claiming it.")
        return 1

    print(f"documentation verified: {len(CHECKS)} check(s), no drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
