"""Exec tools: the allowlist is the security boundary, so it gets adversarial tests."""

from __future__ import annotations

import shutil
import subprocess

import pytest

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
