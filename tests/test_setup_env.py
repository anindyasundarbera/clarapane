"""Tests for provider env naming and the setup script.

Two properties matter here, and both were defects before:

  1. The key names must not claim a provider we are not using. `OPENAI_API_KEY`
     holding an Ollama key is a mislabelling that confuses every new machine.
  2. The setup script must not be able to fail silently. A setup script that reports
     success while missing a key is worse than no script.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))


# ------------------------------------------------------------------- naming
def test_provider_accepts_the_generic_names(monkeypatch, tmp_path):
    """The canonical names are provider-agnostic, not OpenAI-specific."""
    from clarapane.provider import Provider

    env = tmp_path / ".env"
    env.write_text(
        "PROVIDER_BASE_URL=https://example.invalid/v1\n"
        "PROVIDER_API_KEY=generic-key-value\n"
        "PROVIDER_MODEL=some-model\n"
    )
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEFAULT_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("PROVIDER_BASE_URL", raising=False)
    monkeypatch.delenv("PROVIDER_API_KEY", raising=False)
    monkeypatch.delenv("PROVIDER_MODEL", raising=False)

    provider = Provider(env_path=str(env))
    assert provider.base_url == "https://example.invalid/v1"
    assert provider.api_key == "generic-key-value"
    assert provider.default_model == "some-model"


def test_provider_still_reads_the_legacy_names(monkeypatch, tmp_path):
    """Renaming must not break an existing .env on someone else's machine."""
    from clarapane.provider import Provider

    env = tmp_path / ".env"
    env.write_text(
        "OPENAI_BASE_URL=https://legacy.invalid/v1\n"
        "OPENAI_API_KEY=legacy-key-value\n"
        "DEFAULT_OLLAMA_MODEL=legacy-model\n"
    )
    for name in ("PROVIDER_BASE_URL", "PROVIDER_API_KEY", "PROVIDER_MODEL",
                 "OPENAI_BASE_URL", "OPENAI_API_KEY", "DEFAULT_OLLAMA_MODEL"):
        monkeypatch.delenv(name, raising=False)

    provider = Provider(env_path=str(env))
    assert provider.base_url == "https://legacy.invalid/v1"
    assert provider.api_key == "legacy-key-value"
    assert provider.default_model == "legacy-model"


def test_generic_names_win_over_legacy(monkeypatch, tmp_path):
    """A half-migrated .env must resolve deterministically, not by load order."""
    from clarapane.provider import Provider

    env = tmp_path / ".env"
    env.write_text(
        "OPENAI_BASE_URL=https://legacy.invalid/v1\n"
        "PROVIDER_BASE_URL=https://new.invalid/v1\n"
        "PROVIDER_API_KEY=new-key\n"
        "OPENAI_API_KEY=old-key\n"
    )
    for name in ("PROVIDER_BASE_URL", "PROVIDER_API_KEY", "OPENAI_BASE_URL",
                 "OPENAI_API_KEY", "PROVIDER_MODEL", "DEFAULT_OLLAMA_MODEL"):
        monkeypatch.delenv(name, raising=False)

    provider = Provider(env_path=str(env))
    assert provider.base_url == "https://new.invalid/v1"
    assert provider.api_key == "new-key"


def test_one_key_is_used_for_chat_and_model_listing():
    """Two variables holding one secret is a way to make them drift apart."""
    source = (PROJECT_ROOT / "src" / "clarapane" / "provider.py").read_text()
    assert "self.api_key = key" in source, "the resolved key is not stored"
    assert "OLLAMA_API_KEY" not in source, (
        "provider still reads a second key variable; the capability probe should use "
        "the same credential as chat"
    )


def test_dead_keys_are_not_required_by_the_template():
    """OLLAMA_HOST/OLLAMA_API_KEY were required by the template and read by nothing."""
    template = (PROJECT_ROOT / ".env.example").read_text()
    for dead in ("OLLAMA_HOST", "OLLAMA_API_KEY"):
        assert dead not in template, f"{dead} is required but unread -- remove it"


# -------------------------------------------------------------------- script
def _run(args: list[str], cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "setup_env.py"), *args],
        cwd=cwd, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "PYTHONPATH": "src",
             "HOME": str(Path.home())},
    )


def test_setup_reports_failure_for_a_missing_key(tmp_path):
    """A setup script that exits 0 while a key is missing is worse than none."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".env").write_text(
        "PROVIDER_BASE_URL=x\nPROVIDER_API_KEY=\nPROVIDER_MODEL=\n"
    )
    (tmp_path / ".env.example").write_text("PROVIDER_URL=x\n")

    # Run from inside the temp project so PROJECT_ROOT-relative paths point there.
    result = subprocess.run(
        [sys.executable, "-c", (
            f"import sys; sys.argv=['setup','--check','--skip-provider-check'];"
            f"sys.path.insert(0,{str(PROJECT_ROOT / 'scripts')!r});"
            "import setup_env;"
            "setup_env.PROJECT_ROOT=__import__('pathlib').Path("
            f"{str(tmp_path)!r});"
            "setup_env.ENV_PATH=setup_env.PROJECT_ROOT/'.env';"
            "sys.exit(setup_env.main())"
        )],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 1, (
        f"missing keys did not fail the check:\n{result.stdout}\n{result.stderr}"
    )
    assert "PROVIDER_API_KEY" in result.stdout


def test_template_only_mode_is_usable_without_secrets():
    """CI has no .env; the template still has to be verified."""
    result = _run(["--template-only"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TEMPLATE OK" in result.stdout


def test_template_only_fails_when_a_required_key_is_undeclared(tmp_path):
    """The CI gate must be able to fail, or it gates nothing."""
    example = tmp_path / ".env.example"
    example.write_text("PROVIDER_BASE_URL=x\n")  # missing key and model

    result = subprocess.run(
        [sys.executable, "-c", (
            "import sys; sys.argv=['setup','--template-only'];"
            f"sys.path.insert(0,{str(PROJECT_ROOT / 'scripts')!r});"
            "import setup_env;"
            "setup_env.PROJECT_ROOT=__import__('pathlib').Path("
            f"{str(tmp_path)!r});"
            "setup_env.EXAMPLE_PATH=setup_env.PROJECT_ROOT/'.env.example';"
            "sys.exit(setup_env.main())"
        )],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 1, result.stdout
    assert "does not declare" in result.stdout


def test_setup_never_prints_a_secret_value(tmp_path):
    """The script's output is pasted into issues and logs; a key must never appear."""
    secret = "super-secret-value-that-must-not-leak-123456"
    (tmp_path / ".env").write_text(
        f"PROVIDER_BASE_URL=https://x.invalid/v1\n"
        f"PROVIDER_API_KEY={secret}\nPROVIDER_MODEL=m\n"
    )
    (tmp_path / ".env.example").write_text("PROVIDER_BASE_URL=x\n")

    result = subprocess.run(
        [sys.executable, "-c", (
            f"import sys; sys.argv=['setup','--check','--skip-provider-check'];"
            f"sys.path.insert(0,{str(PROJECT_ROOT / 'scripts')!r});"
            "import setup_env;"
            "setup_env.PROJECT_ROOT=__import__('pathlib').Path("
            f"{str(tmp_path)!r});"
            "setup_env.ENV_PATH=setup_env.PROJECT_ROOT/'.env';"
            "sys.exit(setup_env.main())"
        )],
        cwd=tmp_path, capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    assert secret not in output, "the setup script printed a secret value"
    assert "char" in output, "it should report presence and length instead"
