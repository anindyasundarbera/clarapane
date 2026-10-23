"""Product identity: the agent's name, resolved at runtime and overridable.

Why a module instead of a constant
----------------------------------
The name is part of the harness, not just branding: it appears in the CLI banner,
the system prompt, log records, and trace file names. Hardcoding it in a dozen
places makes it expensive to change and easy to get inconsistent.

Resolution order (first non-empty wins):
    1. explicit argument        -- build_agent(app_name="...")
    2. CLARAPANE_APP_NAME       -- .env or the process environment
    3. "Clarapane"              -- the default

The environment variable is read at CALL time, not import time, so a test can
change it with monkeypatch and a running process can be relaunched with a
different identity without a code change.

This is deliberately *cosmetic*: renaming does not change behaviour, log schema,
or policy. Keeping identity separate from behaviour is what makes it safe to vary.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_APP_NAME = "Clarapane"
ENV_VAR = "CLARAPANE_APP_NAME"

# The human accountable for approving mutations. Approvals are recorded against
# this identifier, so the trace answers "who allowed this write?" and not merely
# "a human did". Defaults to the OS login: an empty identity would make every
# approval anonymous, which defeats the purpose of asking.
OPERATOR_ENV_VAR = "CLARAPANE_OPERATOR_ID"
DEFAULT_OPERATOR = "unknown-operator"

# Short tag used in filenames (lowercase, no spaces). Kept separate from the
# display name so a long name cannot produce awkward paths.
DEFAULT_TAG = "clarapane"


def app_name(explicit: str | None = None) -> str:
    """Return the display name for this run."""
    return (explicit or os.environ.get(ENV_VAR) or DEFAULT_APP_NAME).strip() or DEFAULT_APP_NAME


def operator(explicit: str | None = None) -> str:
    """Return the accountable human identifier for this run.

    Resolution order: explicit argument, then CLARAPANE_OPERATOR_ID, then the OS
    login, then a loud placeholder. Never returns an empty string -- an anonymous
    approval is not traceable and would silently weaken the audit trail.
    """
    from getpass import getuser

    candidate = explicit or os.environ.get(OPERATOR_ENV_VAR)
    if candidate and candidate.strip():
        return candidate.strip()

    try:
        login = getuser()
    except Exception:  # noqa: BLE001 - getuser can fail in a bare container
        login = ""
    return login.strip() or DEFAULT_OPERATOR


def app_tag(explicit: str | None = None) -> str:
    """Return a filename-safe slug for this run's artifacts."""
    name = app_name(explicit)
    slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    # Collapse repeats without a regex so the logic reads plainly.
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or DEFAULT_TAG


# The version, resolved without assuming the package was pip-installed.
#
# Why not `importlib.metadata.version("clarapane")`: this project runs from the tree
# via `PYTHONPATH=src` and is not installed as a distribution, so that call raises
# PackageNotFoundError. A version that only works after `pip install -e .` is a
# version that is missing exactly in the situation that matters most -- a fresh clone.
#
# Resolution order (first non-empty wins):
#   1. the installed distribution metadata, when the package *is* installed
#   2. the nearest pyproject.toml, walking up from this file
#   3. "0+unknown" -- explicit rather than a plausible-looking lie
DEFAULT_VERSION = "0+unknown"


def version() -> str:
    """The single source of truth is pyproject.toml; this only reads it."""
    try:
        from importlib.metadata import PackageNotFoundError, version as _dist_version

        try:
            return _dist_version("clarapane")
        except PackageNotFoundError:
            pass
    except Exception:  # noqa: BLE001 - metadata lookup must never break a run
        pass

    for parent in Path(__file__).resolve().parents:
        candidate = parent / "pyproject.toml"
        if not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("version") and "=" in stripped:
                value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
        break
    return DEFAULT_VERSION


def banner(explicit: str | None = None, *, version: str | None = None) -> str:
    """One-line identity string for the CLI.

    `version` is an optional override for tests; the default is read from the single
    source of truth rather than duplicated here, so the banner cannot drift from
    pyproject.toml.
    """
    resolved = version if version is not None else globals()["version"]()
    return f"{app_name(explicit)} v{resolved} -- a glass-box coding agent"

