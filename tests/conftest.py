"""Shared pytest fixtures for the agent's own test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def workspace(tmp_path: Path):
    """A throwaway workspace with a couple of files, jailed like the real one."""
    from glassbox.policy import Workspace

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(
        "def alpha(x):\n    return x + 1\n\n\ndef beta():\n    return alpha(1)\n"
    )
    (tmp_path / "notes.txt").write_text("alpha is used in beta\n")
    return Workspace(root=tmp_path)
