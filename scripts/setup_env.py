"""Set up this machine from .env, and optionally push the values to CI.

The problem this solves
-----------------------
After a migration, three things must hold and none are in git: a `.env` with real
values, an active hooks path, and a working venv. Then CI needs the same provider
values again, typed a second time into GitHub settings, where a typo produces a red
build that looks like broken code.

This script does the whole chain from one source -- your `.env`:

    make setup              # verify .env, install deps, enable hooks, report status
    make setup-apply        # also push provider values + CI secrets
    make setup-apply DRY=1  # show exactly what would change, change nothing

Safety rules, because this handles secrets
------------------------------------------
  * It never prints a secret value. Names and lengths only.
  * It never writes a secret to a file other than `.env`, and never to stdout.
  * It never commits, and it refuses to touch `.env.example`.
  * `gh secret set` reads the value from stdin, so it never appears in a process
    argument list (which is world-readable via /proc on Linux).
  * Dry run is the default for anything that changes remote state.

Usage:
    python scripts/setup_env.py             # local setup + verification
    python scripts/setup_env.py --apply     # also set GitHub Actions secrets
    python scripts/setup_env.py --apply --dry-run
    python scripts/setup_env.py --check     # verification only, change nothing
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
EXAMPLE_PATH = PROJECT_ROOT / ".env.example"

# The keys CI needs. Deliberately the same three provider values, because CI runs the
# same eval suite that reads them -- a second set of names is a second thing to drift.
REQUIRED = ("PROVIDER_BASE_URL", "PROVIDER_API_KEY", "PROVIDER_MODEL")
OPTIONAL = ("CLARAPANE_APP_NAME", "CLARAPANE_OPERATOR_ID")

# Legacy names still accepted, so this script also works on an un-migrated .env.
LEGACY = {
    "PROVIDER_BASE_URL": "OPENAI_BASE_URL",
    "PROVIDER_API_KEY": "OPENAI_API_KEY",
    "PROVIDER_MODEL": "DEFAULT_OLLAMA_MODEL",
}


@dataclass
class Status:
    ok: bool
    detail: str


def read_env(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines, ignoring comments. Values are never logged."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def resolve(values: dict[str, str], key: str) -> str | None:
    """Canonical name first, then the legacy alias."""
    for name in (key, LEGACY.get(key, "")):
        if name and values.get(name):
            return values[name]
    return None


def mask(value: str | None) -> str:
    """Report presence and shape, never the value."""
    if not value:
        return "<missing>"
    return f"<set, {len(value)} chars>"


# ------------------------------------------------------------------- checks
def check_env(values: dict[str, str]) -> list[Status]:
    out: list[Status] = []
    if not ENV_PATH.exists():
        out.append(Status(False, f"{ENV_PATH.name} missing -- copy {EXAMPLE_PATH.name}"))
        return out

    for key in REQUIRED:
        value = resolve(values, key)
        if not value:
            out.append(Status(False, f"{key} is {mask(value)}"))
            continue
        if key == "PROVIDER_API_KEY" and len(value) < 16:
            out.append(Status(False, f"{key} looks too short ({mask(value)})"))
            continue
        out.append(Status(True, f"{key} {mask(value)}"))

    # A duplicated secret is a drift hazard, not a convenience.
    key_value = resolve(values, "PROVIDER_API_KEY")
    if key_value and values.get("OLLAMA_API_KEY") == key_value:
        out.append(Status(
            True,
            "note: OLLAMA_API_KEY duplicates PROVIDER_API_KEY and is unused; "
            "safe to remove",
        ))

    for key in OPTIONAL:
        value = values.get(key)
        out.append(Status(True, f"{key} {mask(value) if value else '<default>'}"))
    return out


def check_template() -> list[Status]:
    """Validate `.env.example` alone -- meaningful in CI, where there is no `.env`.

    CI cannot require a real key to verify the setup tooling. It CAN require that the
    template declares every key the code reads, which is the failure that would
    otherwise reach a user as "set a variable that no longer exists".
    """
    out: list[Status] = []
    if not EXAMPLE_PATH.exists():
        return [Status(False, f"{EXAMPLE_PATH.name} missing")]

    declared = read_env(EXAMPLE_PATH)
    commented = {
        line.strip().lstrip("#").split("=", 1)[0].strip()
        for line in EXAMPLE_PATH.read_text().splitlines()
        if "=" in line
    }
    for key in REQUIRED:
        present = key in declared or key in commented
        out.append(Status(present, f"template declares {key}" if present
                                 else f"template does not declare {key}"))

    # The dead keys must stay dead: they were required-but-unread, and a template that
    # reintroduces them recreates the confusion this rename removed.
    for dead in ("OLLAMA_HOST", "OLLAMA_API_KEY"):
        out.append(Status(dead not in commented, f"{dead} is not required"
                          if dead not in commented else f"{dead} reintroduced"))
    return out


def check_venv() -> Status:
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        return Status(False, ".venv missing -- run: uv venv .venv")
    probe = subprocess.run(
        [str(python), "-c", "import clarapane, pytest"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    if probe.returncode != 0:
        return Status(False, ".venv cannot import clarapane+pytest -- run: make install")
    return Status(True, ".venv imports clarapane and pytest")


def check_git_config() -> list[Status]:
    out: list[Status] = []
    for key, expected in (("core.hooksPath", ".githooks"),
                          ("commit.template", ".gitmessage")):
        result = subprocess.run(
            ["git", "config", key], cwd=PROJECT_ROOT, capture_output=True, text=True
        )
        actual = result.stdout.strip()
        out.append(Status(actual == expected,
                          f"{key} = {actual or '<unset>'} (want {expected})"))
    return out


def check_provider(values: dict[str, str]) -> Status:
    """One real call. A key that parses is not a key that works."""
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        return Status(False, "skipped: no .venv")
    probe = subprocess.run(
        [str(python), "-c",
         "from clarapane.provider import Provider;"
         "p=Provider(env_path='.env');"
         "n=len(p.list_models());"
         "print(f'{n} models')"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    if probe.returncode != 0:
        # The value must never be echoed, but the failure reason is safe to show.
        reason = (probe.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        return Status(False, f"provider call failed: {reason[0][:120]}")
    return Status(True, f"provider reachable, {probe.stdout.strip()}")


# -------------------------------------------------------------------- apply
def _has_gh() -> bool:
    return shutil.which("gh") is not None


def apply_github_secrets(values: dict[str, str], *, dry_run: bool) -> list[Status]:
    """Set CI secrets from .env. Values travel on stdin, never in argv."""
    out: list[Status] = []
    if not _has_gh():
        return [Status(False, "gh CLI not found -- cannot set CI secrets")]

    auth = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if auth.returncode != 0:
        return [Status(False, "gh is not authenticated -- run: gh auth login")]

    for key in REQUIRED:
        value = resolve(values, key)
        if not value:
            out.append(Status(False, f"{key}: nothing to set ({mask(value)})"))
            continue
        if dry_run:
            out.append(Status(True, f"would set secret {key} {mask(value)}"))
            continue
        proc = subprocess.run(
            ["gh", "secret", "set", key],
            cwd=PROJECT_ROOT, input=value, text=True, capture_output=True,
        )
        if proc.returncode != 0:
            out.append(Status(False, f"failed to set {key}: {proc.stderr.strip()[:100]}"))
        else:
            out.append(Status(True, f"set secret {key} {mask(value)}"))
    return out


def install_dependencies(*, dry_run: bool) -> list[Status]:
    """Create the venv and install the package plus pytest."""
    out: list[Status] = []
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    commands: list[list[str]] = []

    if not python.exists():
        commands.append(["uv", "venv", ".venv"])
    commands.append(["uv", "pip", "install", "--python", ".venv/bin/python", "-e", "."])
    commands.append(["uv", "pip", "install", "--python", ".venv/bin/python", "pytest"])

    for command in commands:
        if dry_run:
            out.append(Status(True, f"would run: {' '.join(command)}"))
            continue
        proc = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            out.append(Status(False, f"{' '.join(command[:3])} failed: "
                                    f"{proc.stderr.strip().splitlines()[-1:] or ''}"))
        else:
            out.append(Status(True, f"ran: {' '.join(command[:3])}"))
    return out


def enable_hooks(*, dry_run: bool) -> list[Status]:
    out: list[Status] = []
    for key, value in (("core.hooksPath", ".githooks"),
                       ("commit.template", ".gitmessage")):
        if dry_run:
            out.append(Status(True, f"would run: git config {key} {value}"))
            continue
        proc = subprocess.run(["git", "config", key, value],
                              cwd=PROJECT_ROOT, capture_output=True, text=True)
        out.append(Status(proc.returncode == 0, f"git config {key} {value}"))
    return out


# --------------------------------------------------------------------- main
def report(title: str, statuses: list[Status]) -> bool:
    print(f"\n{title}")
    for status in statuses:
        print(f"  {'ok  ' if status.ok else 'FAIL'} {status.detail}")
    return all(s.ok for s in statuses)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Set up this machine from .env and optionally sync CI secrets"
    )
    ap.add_argument("--apply", action="store_true",
                    help="change things: install deps, enable hooks, set CI secrets")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what --apply would do, change nothing")
    ap.add_argument("--check", action="store_true",
                    help="verify only; never change anything")
    ap.add_argument("--skip-provider-check", action="store_true",
                    help="do not make a live call to the provider")
    ap.add_argument("--template-only", action="store_true",
                    help="validate .env.example only; usable in CI without secrets")
    args = ap.parse_args(argv)

    dry_run = args.dry_run or not args.apply
    values = read_env(ENV_PATH)

    print(f"project: {PROJECT_ROOT}")
    print(f"mode   : {'check only' if args.check else ('dry run' if dry_run else 'APPLY')}")
    print("secrets are never printed; only their presence and length")

    ok = True
    if args.template_only:
        # CI mode: no .env, no secret, but the template must still be honest.
        ok &= report(".env.example", check_template())
        print("\n" + ("TEMPLATE OK" if ok else "TEMPLATE INVALID"))
        return 0 if ok else 1

    ok &= report("environment (.env)", check_env(values))

    if args.check:
        ok &= report(".venv", [check_venv()])
        ok &= report("git config", check_git_config())
        if not args.skip_provider_check and ENV_PATH.exists():
            ok &= report("provider", [check_provider(values)])
        return 0 if ok else 1

    ok &= report("dependencies", install_dependencies(dry_run=dry_run))
    ok &= report("git config", enable_hooks(dry_run=dry_run))

    if args.apply:
        ok &= report("GitHub Actions secrets",
                     apply_github_secrets(values, dry_run=dry_run))

    # Re-verify after applying, so the script reports the state it produced rather
    # than the state it intended.
    if not dry_run:
        ok &= report(".venv after install", [check_venv()])
        ok &= report("git config after apply", check_git_config())
        if not args.skip_provider_check:
            ok &= report("provider", [check_provider(values)])

    print("\n" + ("SETUP OK" if ok else "SETUP INCOMPLETE -- see FAIL lines above"))
    if not args.apply:
        print("To push provider values and CI secrets: python scripts/setup_env.py --apply")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
