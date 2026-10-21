"""Policy boundary tests -- the agent must not be able to talk its way out."""

from __future__ import annotations

import pytest

from clarapane.policy import PolicyError, Workspace


def test_relative_path_inside_root(workspace):
    assert workspace.resolve("pkg/mod.py").name == "mod.py"


def test_root_read_allowed_root_write_denied(workspace):
    assert workspace.resolve(".") == workspace.root.resolve()
    with pytest.raises(PolicyError, match="root"):
        workspace.resolve(".", write=True)


@pytest.mark.parametrize("raw", ["../outside.py", "/etc/passwd", "pkg/../../escape"])
def test_escape_denied(workspace, raw):
    with pytest.raises(PolicyError, match="escapes workspace"):
        workspace.resolve(raw)


def test_secret_read_denied(workspace):
    (workspace.root / ".env").write_text("SECRET=1\n")
    with pytest.raises(PolicyError, match="read denied"):
        workspace.resolve(".env")


def test_source_write_denied_but_read_allowed(tmp_path):
    """The agent may read its own source to reason about it, but never edit it."""
    (tmp_path / "src" / "clarapane").mkdir(parents=True)
    (tmp_path / "src" / "clarapane" / "agent.py").write_text("x = 1\n")
    ws = Workspace(root=tmp_path)

    assert ws.resolve("src/clarapane/agent.py").exists()
    with pytest.raises(PolicyError, match="write denied"):
        ws.resolve("src/clarapane/agent.py", write=True)
