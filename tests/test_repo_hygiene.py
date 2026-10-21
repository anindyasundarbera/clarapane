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
