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

DEFAULT_APP_NAME = "Clarapane"
ENV_VAR = "CLARAPANE_APP_NAME"

# Short tag used in filenames (lowercase, no spaces). Kept separate from the
# display name so a long name cannot produce awkward paths.
DEFAULT_TAG = "clarapane"


def app_name(explicit: str | None = None) -> str:
    """Return the display name for this run."""
    return (explicit or os.environ.get(ENV_VAR) or DEFAULT_APP_NAME).strip() or DEFAULT_APP_NAME


def app_tag(explicit: str | None = None) -> str:
    """Return a filename-safe slug for this run's artifacts."""
    name = app_name(explicit)
    slug = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    # Collapse repeats without a regex so the logic reads plainly.
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or DEFAULT_TAG


def banner(explicit: str | None = None, *, version: str = "0.1.0") -> str:
    """One-line identity string for the CLI."""
    return f"{app_name(explicit)} v{version} -- a glass-box coding agent"
