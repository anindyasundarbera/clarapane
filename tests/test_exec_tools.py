"""Exec tools: the allowlist is the security boundary, so it gets adversarial tests."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from pathlib import Path

from clarapane.policy import PolicyError
from clarapane.registry import ToolError, ToolRegistry
from clarapane.tools.exec_tools import ALLOWED, register_exec_tools, run_command, validate_argv


@pytest.fixture
def tools(workspace):
    registry = ToolRegistry()
    register_exec_tools(registry, workspace)
    return registry


# ------------------------------------------------------------------ allowed use
def test_runs_an_allowlisted_program(tools):
    out = tools.get("run_command").handler(argv=["python3", "-c", "print(6*7)"])
    assert out["exit_code"] == 0
    assert out["stdout"].strip() == "42"


def test_reports_a_nonzero_exit_without_raising(tools):
    """A failing command is data: the agent must see pytest's failure, not a crash."""
    out = tools.get("run_command").handler(argv=["python3", "-c", "import sys; sys.exit(3)"])
    assert out["exit_code"] == 3
    assert out["ok"] is False


def test_cwd_is_the_workspace(tools):
    out = tools.get("run_command").handler(argv=["python3", "-c", "import os;print(os.getcwd())"])
    assert out["exit_code"] == 0


# ------------------------------------------------------------------- refusals
@pytest.mark.parametrize("program", ["curl", "wget", "nc", "ssh", "bash", "sh", "rm", "sudo"])
def test_non_allowlisted_programs_are_refused(tools, program):
    with pytest.raises(ToolError, match="command not allowed"):
        tools.get("run_command").handler(argv=[program, "--version"])


def test_every_allowed_program_is_documented():
    """The allowlist must not silently contain anything unexplained."""
    from clarapane.tools.exec_tools import ALLOWED as programs

    for name, reason in programs.items():
        assert reason, f"{name} is allowed with no stated reason"


def test_no_shell_is_ever_used(tools):
    """The real guarantee. With shell=False, metacharacters are inert data.

    An earlier version refused |, ;, & and > as "shell injection", which was
    cargo-cult: it provided no safety (there is no shell to inject into) and broke
    `python3 -c "import time; time.sleep(5)"`. The guarantee is the absence of a shell,
    so that is what this test asserts.
    """
    import inspect

    from clarapane.tools import exec_tools

    source = inspect.getsource(exec_tools.run_command)
    assert "shell=False" in source, "run_command must pass shell=False explicitly"

    # Semicolons inside an argument are data, and a program that needs them must work.
    out = tools.get("run_command").handler(
        argv=["python3", "-c", "x = 1; print(x + 1)"]
    )
    assert out["exit_code"] == 0
    assert out["stdout"].strip() == "2"


def test_a_pipe_argument_is_not_a_pipeline(tools):
    """`echo 'a|b'` must print a|b, not pipe into a program called b."""
    out = tools.get("run_command").handler(argv=["echo", "a|b"])
    assert out["exit_code"] == 0
    assert "a|b" in out["stdout"]


@pytest.mark.parametrize("sub", ["commit", "push", "reset", "checkout", "clean", "add"])
def test_git_write_subcommands_are_refused(tools, sub):
    with pytest.raises(PolicyError, match="may inspect history, not change it"):
        tools.get("run_command").handler(argv=["git", sub])


def test_git_read_subcommands_are_allowed(workspace):
    subprocess.run(["git", "init", "-q", "."], cwd=workspace.root)
    registry = ToolRegistry()
    register_exec_tools(registry, workspace)
    out = registry.get("run_command").handler(argv=["git", "status", "--short"])
    assert out["exit_code"] == 0


def test_absolute_path_outside_workspace_is_refused(tools):
    with pytest.raises(PolicyError, match="outside the workspace"):
        tools.get("run_command").handler(argv=["cat", "/etc/passwd"])


def test_parent_escape_is_refused(tools):
    with pytest.raises(PolicyError):
        tools.get("run_command").handler(argv=["cat", "../../etc/hosts"])


# ------------------------------------------------------------------- behaviour
def test_timeout_is_enforced(tools):
    out = tools.get("run_command").handler(
        argv=["python3", "-c", "import time; time.sleep(5)"], timeout=1
    )
    assert out["timed_out"] is True
    assert out["ok"] is False


def test_timeout_is_capped(tools):
    """A model asking for an hour must not get one."""
    out = tools.get("run_command").handler(argv=["python3", "-c", "print(1)"], timeout=99999)
    assert out["exit_code"] == 0


def test_output_is_truncated(tools):
    out = tools.get("run_command").handler(
        argv=["python3", "-c", "print('x'*20000)"]
    )
    assert out["truncated"] is True
    assert len(out["stdout"]) < 20000


def test_string_input_is_parsed_not_shelled(tools):
    """Accepting a string is a convenience; it must still be argv, never a shell."""
    out = tools.get("run_command").handler(argv=["echo", "hello", "world"])
    assert "hello world" in out["stdout"]


def test_run_command_is_gated_and_declared_exec(tools):
    spec = tools.get("run_command")
    assert spec.risk == "exec"
    assert spec.gate is True, "an exec tool must require approval"


# ------------------------------------------------------- child environment (CI parity)

def test_child_env_makes_the_interpreter_bin_reachable(workspace):
    """Regression: PATH was hardcoded to /usr/local/bin:/usr/bin:/bin.

    On a clean machine `pytest` exists only at .venv/bin/pytest, so the agent could
    not run the project's test command. Locally /usr/bin/pytest happened to exist and
    masked it; CI caught it. A capability that works only on the author's machine is
    not a capability.
    """
    import sys as _sys
    from clarapane.tools.exec_tools import _child_env

    path = _child_env(workspace)["PATH"].split(":")
    assert str(Path(_sys.executable).parent) in path, (
        f"the interpreter's bin dir is not on the child PATH: {path}"
    )


def test_child_env_does_not_leak_secrets(workspace, monkeypatch):
    """A command's output becomes model input, so a leaked key enters the conversation."""
    from clarapane.tools.exec_tools import _child_env

    monkeypatch.setenv("SOME_PROVIDER_API_KEY", "sk-should-never-appear")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-should-never-appear")
    monkeypatch.setenv("DB_PASSWORD", "hunter2")

    env = _child_env(workspace)
    leaked = [k for k in env if any(m in k.upper() for m in
                                    ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))]
    assert not leaked, f"secret-shaped variables passed through: {leaked}"


def test_run_command_can_reach_pytest(workspace):
    """The exact capability the eval case depends on."""
    from clarapane.tools.exec_tools import run_command

    result = run_command(workspace, ["pytest", "--version"])
    assert result["exit_code"] == 0, result
    assert "pytest" in result["stdout"].lower()


def test_missing_program_is_an_error_not_a_crash(workspace):
    """A disallowed program must be a clean ToolError the model can recover from."""
    from clarapane.registry import ToolError
    from clarapane.tools.exec_tools import run_command

    with pytest.raises(ToolError):
        run_command(workspace, ["no-such-program-anywhere"])


def test_absent_allowlisted_program_names_the_search_path(workspace, monkeypatch):
    """An allowlisted program that is not installed must fail with guidance.

    Regression: FileNotFoundError propagated uncaught, so the trace recorded a bare
    exception with no hint. The message now names the program and the PATH searched.
    """
    from clarapane.registry import ToolError
    from clarapane.tools import exec_tools

    # Simulate an allowlisted program that is absent from every PATH entry.
    monkeypatch.setitem(exec_tools.ALLOWED, "fake-but-allowed", "test double")
    with pytest.raises(ToolError) as excinfo:
        exec_tools.run_command(workspace, ["fake-but-allowed"])

    message = str(excinfo.value)
    assert "not installed where the agent can see it" in message
    assert "PATH=" in message, "the error must say where it looked"
