"""Path jail and permission policy.

The model never decides its own permissions. Every path the agent touches is
resolved here first, and the allow/deny rules are plain data that a human can
audit in one screen.

Two separate boundaries, deliberately different:
  * write boundary -- strict; the agent may not edit its own source
  * read boundary  -- narrower secret list (.env), but the agent MAY read its
                      own source so it can explain or reason about itself
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class PolicyError(RuntimeError):
    """Raised when an action violates the workspace policy."""


@dataclass(frozen=True)
class Workspace:
    root: Path
    # Never writable: agent source, VCS internals, virtualenv, secrets.
    protected_write: tuple[str, ...] = ("src/glassbox", ".git", ".venv", ".env")
    # Never readable: secrets and machine-local state.
    denied_read: tuple[str, ...] = (".env", ".venv", ".git")

    def resolve(self, raw: str, *, write: bool = False) -> Path:
        """Resolve `raw` against the workspace root, enforcing both boundaries."""
        if not raw or not raw.strip():
            raise PolicyError("path must not be empty")

        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate

        root = self.root.resolve()
        real = candidate.resolve()

        if not real.is_relative_to(root):
            raise PolicyError(f"path escapes workspace: {raw} -> {real}")
        # Reads of the workspace root are legitimate (listing "." is how an agent
        # orients itself); writes to the root itself are not a meaningful action.
        if real == root:
            if write:
                raise PolicyError("refusing to write to the workspace root itself")
            return real

        rel = real.relative_to(root)
        denied = self.protected_write if write else self.denied_read
        for prefix in denied:
            guarded = Path(prefix)
            if rel == guarded or rel.is_relative_to(guarded):
                mode = "write" if write else "read"
                raise PolicyError(f"{mode} denied by policy: {rel} (protected: {prefix})")

        return real

    def relative(self, path: Path) -> str:
        """Display path relative to the workspace root, for logs and tool output."""
        try:
            return str(path.resolve().relative_to(self.root.resolve()))
        except ValueError:
            return str(path)
