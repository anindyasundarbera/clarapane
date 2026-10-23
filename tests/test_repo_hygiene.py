"""Repository hygiene tests: commit messages and secret hygiene.

These run with the normal test suite so that guardrails hold even when someone
(or some agent) bypasses the commit-msg hook with --no-verify.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import check_commits  # noqa: E402


def test_commit_messages_conform():
    """Every commit in history must follow the agreed format."""
    try:
        entries = check_commits.commits(None)
    except subprocess.CalledProcessError:
        pytest.skip("not a git repository, or no commits yet")

    problems: list[str] = []
    for sha, message in entries:
        problems.extend(check_commits.check(sha, message))

    assert not problems, "\n".join(problems)


def test_env_is_ignored_and_never_tracked():
    """The .env file must be gitignored and must not be in the index."""
    env = PROJECT_ROOT / ".env"
    if env.exists():
        ignored = subprocess.run(
            ["git", "check-ignore", ".env"], cwd=PROJECT_ROOT, capture_output=True, text=True
        )
        assert ignored.returncode == 0, ".env is NOT gitignored -- secret risk"

    tracked = subprocess.run(
        ["git", "ls-files", ".env"], cwd=PROJECT_ROOT, capture_output=True, text=True
    )
    assert tracked.stdout.strip() == "", ".env is tracked by git -- rotate the key"


def test_env_example_contains_no_secret_values():
    example = PROJECT_ROOT / ".env.example"
    assert example.exists(), ".env.example must document the required keys"

    # Endpoints and identifiers are not secrets; anything else must be blank.
    public_keys = {"OPENAI_BASE_URL", "OLLAMA_HOST", "DEFAULT_OLLAMA_MODEL"}

    for line in example.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in public_keys:
            continue
        assert value.strip() == "", (
            f"{key} has a value in .env.example; templates must leave secrets blank"
        )


def test_agents_md_exists():
    """The rules must be discoverable at the repo root."""
    text = (PROJECT_ROOT / "AGENTS.md").read_text()
    for required in ("Never commit `.env`", "Conventional Commits", "Refs:"):
        assert required in text, f"AGENTS.md is missing guidance about {required!r}"


def test_defect_audit_has_no_blockers():
    """The defect policy is only real if it fails the suite.

    Per AGENTS.md section 0: a BLOCKER stops all other work. Running the audit here
    means a blocking defect cannot be merged, regardless of whether a human or an
    agent remembers to run it.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import audit_defects

    report = audit_defects.check_all()
    assert not report.blockers, "BLOCKER defects must be fixed before any other work:\n" + "\n".join(
        f"  {d}" for d in report.blockers
    )


def test_defect_audit_reports_no_defects_at_all():
    """Stronger guard: the tree should be free of MAJOR defects too."""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import audit_defects

    report = audit_defects.check_all()
    assert not report.defects, "\n".join(f"  {d}" for d in report.defects)


def test_agents_md_documents_defect_policy():
    """Future sessions must be able to find the policy without being told."""
    text = (PROJECT_ROOT / "AGENTS.md").read_text()
    assert "Defect policy" in text
    assert "BLOCKER" in text
    assert "audit_defects.py" in text


# ------------------------------------------------------------------ identity


def test_identity_defaults_to_clarapane(monkeypatch):
    from clarapane import identity

    monkeypatch.delenv(identity.ENV_VAR, raising=False)
    assert identity.app_name() == "Clarapane"
    assert identity.app_tag() == "clarapane"


def test_identity_env_var_overrides_default(monkeypatch):
    from clarapane import identity

    monkeypatch.setenv(identity.ENV_VAR, "Glass Pane")
    assert identity.app_name() == "Glass Pane"
    assert identity.app_tag() == "glass-pane"  # filename-safe slug


def test_identity_explicit_argument_beats_env(monkeypatch):
    from clarapane import identity

    monkeypatch.setenv(identity.ENV_VAR, "FromEnv")
    assert identity.app_name("Explicit") == "Explicit"


def test_identity_blank_env_falls_back(monkeypatch):
    from clarapane import identity

    monkeypatch.setenv(identity.ENV_VAR, "   ")
    assert identity.app_name() == "Clarapane"


def test_identity_is_read_at_call_time_not_import_time(monkeypatch):
    """A running process must be able to change identity without a code change."""
    from clarapane import identity

    monkeypatch.setenv(identity.ENV_VAR, "First")
    assert identity.app_name() == "First"
    monkeypatch.setenv(identity.ENV_VAR, "Second")
    assert identity.app_name() == "Second"


def test_no_stale_package_name_remains():
    """The old package name must not survive anywhere in tracked source."""
    offenders = []
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or ".venv" in path.parts:
            continue
        if path.suffix not in {".py", ".md", ".toml", ".json", ".sh", ""}:
            continue
        try:
            if "glass" + "box" in path.read_text().lower():
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
        except (UnicodeDecodeError, OSError):
            continue
    assert not offenders, f"stale package references: {offenders}"


# ------------------------------------------------- config propagation guard


def _env_keys(text: str) -> set[str]:
    """Key names declared in a dotenv file.

    Only real assignments and commented-out assignments count. Free-form prose in
    comments is ignored: a comment containing "PYTHONPATH=src ... make run" is an
    example command, not a setting, and treating it as one produces a false alarm.
    """
    keys: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or "=" not in stripped:
            continue

        commented = stripped.startswith("#")
        body = stripped.lstrip("#").strip()
        if commented and not body.startswith(tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")):
            continue  # prose comment, not a documented key
        key, _, value = body.partition("=")
        key = key.strip()
        # A real setting is a bare identifier with a single-token value. An example
        # command (`PYTHONPATH=src .venv/bin/python ...`) has spaces in the value.
        if key.isidentifier() and key.upper() == key and " " not in value.strip():
            keys.add(key)
    return keys


def test_local_env_has_every_key_the_template_declares():
    """Every key in .env.example must also exist in the real .env.

    This is the check for a class of failure we actually hit: a new setting was
    added to the template and to the code, but not to the running configuration, so
    the feature was documented and inert. One agent action was completed and
    adopted while the peer surface was never touched.

    Presence is all that is asserted -- values are never read, printed, or compared.
    """
    example = PROJECT_ROOT / ".env.example"
    env = PROJECT_ROOT / ".env"
    if not env.exists():
        pytest.skip("no local .env (CI or fresh clone)")

    declared = _env_keys(example.read_text())
    present = _env_keys(env.read_text())

    missing = sorted(declared - present)
    assert not missing, (
        f".env.example declares keys missing from .env: {missing}. "
        "Add them to .env so the feature is not inert at runtime."
    )


def test_identity_variable_is_documented_and_wired():
    """The name override must exist in the template, the module, and the CLI."""
    from clarapane import identity

    example = (PROJECT_ROOT / ".env.example").read_text()
    assert identity.ENV_VAR in example, f"{identity.ENV_VAR} missing from .env.example"

    cli = (PROJECT_ROOT / "src" / "clarapane" / "cli.py").read_text()
    assert '"--name"' in cli, "CLI must expose --name"
    assert "identity.app_name" in cli, "CLI must resolve the name through identity"


def test_no_run_artifact_is_abandoned():
    """A commit must not leave tracked evidence behind (rule 1a in AGENTS.md).

    Twice-confirmed failure: source changes were committed while runs/runs.jsonl
    stayed unstaged, so the commit's own Evidence: section cited a trace it did not
    contain. The pre-commit hook blocks this, but hooks are not installed in CI, so
    the test suite must enforce it too.
    """
    import subprocess

    out = subprocess.run(
        ["git", "diff", "--name-only"], cwd=PROJECT_ROOT, capture_output=True, text=True
    ).stdout
    abandoned = [f for f in out.splitlines() if f.strip().startswith("runs/")]
    assert not abandoned, (
        f"tracked run artifact(s) modified but unstaged: {abandoned}. "
        "Use `make commit MSG=...` so evidence is included."
    )


def test_pre_commit_hook_is_installed_and_executable():
    """The hook must exist, be executable, and not be configured elsewhere.

    Why an unconfigured hooksPath is a SKIP and a wrong one is a FAIL
    ----------------------------------------------------------------
    `core.hooksPath` lives in local git config, which is not committed. A fresh clone
    and every CI checkout therefore report it empty -- probed in a clean clone:
    `hooksPath is ''`. Failing there would make the suite red on every CI run for a
    condition the repository cannot control.

    So this test distinguishes two different situations:
      * empty      -> the hooks are simply not installed here; report by skipping,
                      because the controls that DO travel (tests, audit, CI) still ran
      * a wrong    -> someone pointed hooks at the wrong directory; that is a bug,
        path          because the rules silently stop applying
    """
    import os
    import subprocess

    hook = PROJECT_ROOT / ".githooks" / "pre-commit"
    assert hook.exists(), "pre-commit hook missing"
    assert os.access(hook, os.X_OK), "pre-commit hook is not executable"

    configured = subprocess.run(
        ["git", "config", "core.hooksPath"], cwd=PROJECT_ROOT,
        capture_output=True, text=True,
    ).stdout.strip()

    if not configured:
        pytest.skip(
            "core.hooksPath is not set in this checkout (expected in a fresh clone "
            "and in CI). The hooks are inert here; tests and the audit are the "
            "controls that still apply. Enable locally with: "
            "git config core.hooksPath .githooks"
        )

    assert configured == ".githooks", (
        f"core.hooksPath is {configured!r}, not '.githooks' -- the commit rules "
        "silently do not apply"
    )


# ---------------------------------------------------- approval integrity (rule)


def test_no_gate_accepts_a_bare_assent():
    """A permissive word must never constitute approval (AGENTS.md rule 1b).

    Regression guard for a real defect: "proceed" was treated as consent for work
    the operator had not seen. Consent must bind to the exact payload.
    """
    import builtins

    from clarapane.gate import InteractiveGate, fingerprint

    args = {"path": "a.py", "old_str": "x", "new_str": "y"}
    fp = fingerprint("apply_edit", args)
    permissive = ["y", "yes", "proceed", "ok", "sure", "", "lgtm", "approve"]

    class TTY:
        def isatty(self):
            return True

    original = builtins.input
    try:
        for answer in permissive:
            builtins.input = lambda prompt="", a=answer: a
            gate = InteractiveGate(stream=TTY(), operator="tester")
            decision = gate.decide("apply_edit", args)
            assert decision.approved is False, f"{answer!r} was accepted as approval"
            assert decision.fingerprint == fp
    finally:
        builtins.input = original


def test_approval_decisions_always_carry_operator_and_fingerprint():
    """No gate may produce a decision that cannot be attributed or bound."""
    from clarapane.gate import InteractiveGate, PolicyGate

    args = {"path": "a.py", "old_str": "x", "new_str": "y"}

    class NoTTY:
        def isatty(self):
            return False

    for gate in (
        PolicyGate(allow_writes=True, reason="t"),
        PolicyGate(allow_writes=False, reason="t"),
        InteractiveGate(stream=NoTTY(), operator="tester"),
    ):
        decision = gate.decide("apply_edit", args)
        assert decision.operator, "decision has no accountable operator"
        assert len(decision.fingerprint) == 12, "decision is not bound to a payload"


# ------------------------------------------------------- scope lock (rule 0a)


def test_plan_exists_and_declares_its_purpose():
    """Deviation is only measurable against a written plan (AGENTS.md 0a)."""
    import json

    plan_file = PROJECT_ROOT / "plan.json"
    assert plan_file.exists(), "plan.json is missing"

    data = json.loads(plan_file.read_text())
    assert data.get("purpose"), "plan.json must state why this project exists"
    assert data.get("steps"), "plan.json must list the steps"
    assert data.get("current_step"), "plan.json must name the current step"
    assert data.get("not_in_plan"), "plan.json must list what is explicitly out of scope"


def test_exactly_one_step_is_in_progress():
    """Two active steps means two tracks, which is how a plan derails."""
    import json

    data = json.loads((PROJECT_ROOT / "plan.json").read_text())
    active = [s["id"] for s in data["steps"] if s.get("status") == "in_progress"]
    assert len(active) == 1, f"expected one in-progress step, found {active}"


def test_plan_steps_are_sequential():
    """A later step cannot start before an earlier one is done."""
    import json

    data = json.loads((PROJECT_ROOT / "plan.json").read_text())
    seen_unfinished = None
    for step in data["steps"]:
        if step.get("status") in ("done", "in_progress"):
            assert seen_unfinished is None, (
                f"step {step['id']!r} advanced while {seen_unfinished!r} was unfinished"
            )
        if step.get("status") != "done":
            seen_unfinished = seen_unfinished or step["id"]


def test_agents_md_documents_the_scope_lock():
    text = (PROJECT_ROOT / "AGENTS.md").read_text()
    assert "Purpose and scope lock" in text
    assert "Deviation is a defect" in text
    assert "plan.json" in text


def test_cli_surfaces_the_current_plan_step():
    """Every run should state what it is for, so drift is visible immediately."""
    cli = (PROJECT_ROOT / "src" / "clarapane" / "cli.py").read_text()
    assert "current_plan_step" in cli
    assert "plan step" in cli


# ------------------------------------------------- review honesty (rule 1c)


def test_no_document_claims_the_pr_path_guarantees_review():
    """A PR with zero required approvals is a record, not a review.

    Reworded after the operator asked "who is the reviewer?" and the answer was
    nobody. A document claiming review that never happens manufactures evidence of
    scrutiny -- the same defect as accepting "proceed" as consent.
    """
    import re

    misleading = [
        r"reviewed PR",
        r"diff is reviewed",
        r"evidence of review",
        r"review is required",
    ]
    offenders: list[str] = []
    for path in (PROJECT_ROOT / "AGENTS.md", PROJECT_ROOT / ".githooks" / "pre-commit",
                 PROJECT_ROOT / ".githooks" / "pre-push",
                 PROJECT_ROOT / "scripts" / "audit_defects.py"):
        text = path.read_text()
        for pattern in misleading:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                # Quoted as a thing NOT to say is fine. Look at the whole
                # sentence containing the match, not a fixed window, because the
                # disclaiming clause can sit on either side of it.
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                line = text[line_start: line_end if line_end != -1 else len(text)]
                prev = text[max(0, line_start - 200):line_start]
                context = prev + line
                if any(marker in context for marker in (
                    "Do not describe", "not that it was reviewed",
                    "not accepted as a substitute", "Calling it evidence of review",
                )):
                    continue
                offenders.append(f"{path.name}: {match.group(0)!r}")
    assert not offenders, f"documents overclaim review: {offenders}"


def test_ruleset_approval_count_matches_the_documented_claim():
    """If the ruleset later requires approvals, the docs must be updated with it."""
    import re

    agents = (PROJECT_ROOT / "AGENTS.md").read_text()
    match = re.search(r"required_approving_review_count:\s*(\d+)", agents)
    assert match, "AGENTS.md must state the actual approval requirement"
    count = int(match.group(1))
    assert count == 0, (
        "AGENTS.md documents 0 approvals; if the ruleset now requires approvals, "
        "update section 1c so the claim and the control agree"
    )


# --------------------------------------------- reviewer-found defect (regression)


def test_current_plan_step_survives_malformed_plan(tmp_path, monkeypatch):
    """Regression: found by four independent reviewer models, confirmed by hand.

    json.loads succeeds on [] / null / "x" / 42, so the handler's
    `except (OSError, ValueError)` missed the AttributeError that followed and a
    corrupt plan crashed every CLI run.
    """
    import json as _json

    from clarapane import cli

    plan_file = tmp_path / "plan.json"
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)

    for payload in ("[]", "null", '"hello"', "42", '{"steps":[1,2]}', '{"steps":[]}'):
        plan_file.write_text(payload)
        assert cli.current_plan_step() is None, f"{payload!r} should degrade to None"

    plan_file.write_text(_json.dumps({"steps": [{"id": "x", "status": "in_progress",
                                                 "title": "X"}]}))
    assert cli.current_plan_step() == "X"


# --------------------------------------------------- eval suite is specified


def test_eval_suite_backlog_item_is_actionable():
    """A high-priority item must be executable by a future session without rethinking.

    Checks the fields that make a plan actionable rather than aspirational: a concrete
    trigger, per-case assertions, and acceptance criteria that could fail.
    """
    from clarapane import backlog

    item = backlog.find(backlog.load(), "eval-suite")
    assert item["status"] in ("planned", "in_progress")
    assert item["trigger"], "needs a trigger"
    assert len(item.get("cases", [])) >= 6, "too few cases to measure anything"
    assert item.get("runner_design"), "needs a runner design"
    assert item.get("non_goals"), "needs explicit non-goals to prevent scope creep"

    for case in item["cases"]:
        assert case.get("name") and case.get("assert"), f"case lacks assertions: {case}"
        assert len(case["assert"]) >= 2, f"case {case['name']!r} asserts too little"

    # The suite must be able to fail, not just confirm successes.
    expected_failures = [c for c in item["cases"] if "EXPECTED TO FAIL" in c.get("status", "")]
    assert expected_failures, "no case is expected to fail: the suite could be vacuous"
    for case in expected_failures:
        assert case.get("expected_failure_reason"), (
            f"{case['name']!r} must say why it fails, or it becomes a silent known-bug"
        )


# -------------------------------------------- PR description integrity (rule 1d)


def test_pr_title_must_reflect_its_most_significant_commit():
    """A squash merge uses the PR title as main's commit subject.

    Regression guard for a real gap: PR #4 was titled `fix(profiles)` while containing
    `docs(plan)`, `docs(backlog)` and `feat(evals)`. Merging it would have recorded a
    feature under a fix subject, hiding new capability from anyone reading history.
    """
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import check_pr

    commits = [
        {"messageHeadline": "fix(profiles): remove the exec profile"},
        {"messageHeadline": "docs(plan): repurpose the project"},
        {"messageHeadline": "feat(evals): measure the agent"},
    ]
    body = "x" * 300 + "\nRefs: phase:4.0"

    import json as _json

    original = check_pr.gh

    def fake(title):
        def _gh(*args):
            return _json.dumps({
                "number": 4, "title": title, "body": body, "commits": commits,
                "headRefName": "feat/x", "baseRefName": "main",
            })
        return _gh

    try:
        check_pr.gh = fake("fix(profiles): remove the exec profile")
        problems = check_pr.check_pr(4)
        assert problems, "stale title was accepted"
        assert any("feat" in p for p in problems), f"problem did not name the feat: {problems}"

        check_pr.gh = fake("feat(evals): measure the agent")
        assert not check_pr.check_pr(4), "correct title was rejected"
    finally:
        check_pr.gh = original


def test_check_pr_script_exists_and_is_documented():
    assert (PROJECT_ROOT / "scripts" / "check_pr.py").exists()
    text = (PROJECT_ROOT / "AGENTS.md").read_text()
    assert "check_pr.py" in text, "AGENTS.md does not mention the PR check"


# ------------------------------------------------------------------ versioning


def test_version_has_exactly_one_source_of_truth():
    """The version must come from pyproject.toml, not be duplicated in code.

    Regression: `banner()` defaulted to a hardcoded `version="0.1.0"`, so bumping
    pyproject.toml would leave the CLI banner reporting the old version -- a
    user-facing claim that silently disagreed with the package metadata.
    """
    import re

    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    assert match, "pyproject.toml has no version"

    src = (PROJECT_ROOT / "src" / "clarapane" / "identity.py").read_text()
    literal = re.search(r'banner\([^)]*version:\s*str\s*=\s*"([0-9][^"]*)"', src)
    assert literal is None, (
        f"banner() hardcodes version {literal.group(1)!r}; read it from "
        "pyproject.toml via identity.version()"
    )


def test_reported_version_matches_pyproject():
    """A banner that disagrees with the package metadata is a false claim."""
    import re
    import sys as _sys

    _sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from clarapane import identity

    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
    expected = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1)
    assert identity.version() == expected, (
        f"version()={identity.version()!r} but pyproject says {expected!r}"
    )
    assert expected in identity.banner()


def test_version_does_not_require_an_installed_distribution():
    """This project runs from the tree via PYTHONPATH=src, not pip-installed.

    `importlib.metadata.version()` raises PackageNotFoundError in that state, so a
    version reader that depends on it fails exactly where it matters most.
    """
    import subprocess

    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'src');"
         "from clarapane import identity; print(identity.version())"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() and "unknown" not in out.stdout


# ------------------------------------------------------------ documentation drift


def test_documentation_matches_the_code():
    """Docs that disagree with code are a false claim, so they fail the suite.

    This is the check that would have caught the drift measured before it existed:
    docs/BACKLOG.md listing a stale subset of the backlog, and identity.banner()
    hardcoding a version that pyproject.toml also declared.
    """
    import check_docs

    findings: list[check_docs.Finding] = []
    for check in check_docs.CHECKS:
        findings.extend(check())
    assert not findings, "documentation drift:\n" + "\n".join(f"  - {f}" for f in findings)


def test_every_documentation_check_can_fail():
    """A check that has only ever passed proves nothing.

    The repo learned this three times during the AOP research, where a gate read
    total infrastructure failure as a clean negative result. Each check is exercised
    against a deliberately broken copy and must report a finding.
    """
    import check_docs

    original = check_docs.PROJECT_ROOT
    try:
        def run(mutate_name: str, mutate, check) -> bool:
            tmp = Path(__import__("tempfile").mkdtemp())
            for rel in ("README.md", "Makefile", "pyproject.toml", "CHANGELOG.md",
                        "backlog.json", "docs/BACKLOG.md",
                        "src/clarapane/identity.py"):
                src = original / rel
                if src.exists():
                    (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
                    (tmp / rel).write_text(src.read_text())
            target = tmp / mutate_name
            target.write_text(mutate(target.read_text()))
            check_docs.PROJECT_ROOT = tmp
            return bool(check())

        assert run("README.md", lambda t: t, check_docs.check_readme_exists) is False
        assert run("README.md", lambda t: t.replace("| `exec` |", "| `nope` |"),
                   check_docs.check_profile_table)
        assert run("README.md", lambda t: t.replace("divergence", ""),
                   check_docs.check_event_list)
        assert run("Makefile", lambda t: t.replace("\neval:", "\nremoved:"),
                   check_docs.check_make_targets)
        assert run("docs/BACKLOG.md",
                   lambda t: t.replace("authoritative", "a list").replace(
                       "make backlog", "the cli"),
                   check_docs.check_backlog_ids)
        assert run("src/clarapane/identity.py",
                   lambda t: t.replace('version: str | None = None',
                                       'version: str = "0.1.0"'),
                   check_docs.check_version_single_source)
    finally:
        check_docs.PROJECT_ROOT = original
