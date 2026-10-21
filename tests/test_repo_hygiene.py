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
    """The hook must exist, be executable, and be the configured hooks path."""
    import os
    import subprocess

    hook = PROJECT_ROOT / ".githooks" / "pre-commit"
    assert hook.exists(), "pre-commit hook missing"
    assert os.access(hook, os.X_OK), "pre-commit hook is not executable"

    configured = subprocess.run(
        ["git", "config", "core.hooksPath"], cwd=PROJECT_ROOT,
        capture_output=True, text=True,
    ).stdout.strip()
    assert configured == ".githooks", f"core.hooksPath is {configured!r}, not '.githooks'"


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
