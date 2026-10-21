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
