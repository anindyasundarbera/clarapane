"""Command execution behind an allowlist.

The threat, stated plainly
--------------------------
An exec tool is the one capability that turns a prompt injection into a real incident.
Every other tool is confined to the workspace and to declared file operations; a shell
is confined by nothing. This module does not pretend to solve that. It narrows the
window and states the limits:

  * ALLOWLIST, NOT A SHELL. argv is built from a fixed program list; there is no
    `shell=True`, no pipes, no redirects, no `&&`, no command substitution. A string
    containing `;` or `|` is a single argument to a program, not two commands.
  * NO NETWORK PROGRAMS. curl, wget, nc, ssh and friends are absent by construction, so
    an injected URL has nothing to resolve it.
  * WORKSPACE JAIL ON cwd. The subprocess runs inside the workspace and cannot be
    pointed elsewhere.
  * OUTPUT IS UNTRUSTED. Command output can echo file contents, so it goes through the
    same trust marker and injection scan as any read tool.
  * TIMEOUT AND OUTPUT CAP. A hanging or flooding command fails the call, not the run.

Honest limits: an allowlisted program may still have dangerous flags (`git` can write,
`python` can do anything). This is defence in depth, not a sandbox. A container is the
only real answer, and it is out of scope for this project.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..policy import PolicyError, Workspace
from ..registry import ToolError, ToolRegistry, ToolSpec

MAX_OUTPUT_CHARS = 6000
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120

# Programs the agent may run, with the reason each is safe enough to offer. Anything
# not listed here is refused before a process is created.
ALLOWED: dict[str, str] = {
    "python": "run tests or a module inside the workspace",
    "python3": "run tests or a module inside the workspace",
    "pytest": "run the fixture test suite",
    "ls": "list files (read tools exist, but pipelines are common)",
    "cat": "read a file (read tools exist; some shells prefer cat)",
    "git": "inspect history and status; write subcommands are refused below",
    "echo": "trivial output, useful for smoke tests",
}

# git subcommands that change state. Verification needs `status`, `diff`, `log`, `show`.
GIT_WRITE_SUBCOMMANDS = frozenset({
    "commit", "push", "reset", "checkout", "switch", "clean", "revert",
    "merge", "rebase", "cherry-pick", "tag", "add", "rm", "mv", "stash",
})

# NOTE: there is deliberately no metacharacter refusal here.
#
# An earlier version refused argv containing |, ;, &, >, <, ` or $( as "shell
# injection". That was cargo-cult security: subprocess runs with shell=False, so those
# characters are inert data, not syntax. The check provided no safety and broke real
# use -- `python3 -c "import time; time.sleep(5)"` was rejected because the Python
# program itself contained a semicolon.
#
# The actual guarantee is `shell=False`, asserted in a test. A future change that
# introduced a shell would be caught there, which is where the risk really lives.


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    truncated: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "argv": self.argv,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "truncated": self.truncated,
            "ok": self.exit_code == 0 and not self.timed_out,
        }


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    return text[:MAX_OUTPUT_CHARS] + "\n...[output truncated]", True


def validate_argv(argv: list[str], workspace: Workspace) -> None:
    """Refuse an argv that the allowlist does not cover. Raises ToolError."""
    if not argv:
        raise ToolError("argv must not be empty")

    program = argv[0]
    if program not in ALLOWED:
        raise ToolError(
            f"command not allowed: {program!r}. Allowed: {sorted(ALLOWED)}. "
            "This is an allowlist; there is no shell."
        )
    # Search the PATH the CHILD will receive, not this process's PATH. Using the
    # parent PATH here approved programs the child could not find -- exactly the
    # mismatch that made `pytest` work locally (present in /usr/bin) and fail in CI
    # (present only in .venv/bin, which the child PATH did not include).
    child_path = _child_env(workspace)["PATH"]
    if shutil.which(program, path=child_path) is None:
        raise ToolError(
            f"{program!r} is allowed but not installed where the agent can see it. "
            f"Searched PATH={child_path}"
        )

    if program == "git":
        sub = next((a for a in argv[1:] if not a.startswith("-")), "")
        if sub in GIT_WRITE_SUBCOMMANDS:
            raise PolicyError(
                f"git {sub} is refused: the agent may inspect history, not change it. "
                "Use checkpoint/rollback for state changes."
            )

    # A path argument must not escape the workspace, even though cwd is jailed: a
    # program can follow an absolute path out.
    for arg in argv[1:]:
        if arg.startswith("/") or arg.startswith(".."):
            try:
                workspace.resolve(arg)
            except PolicyError:
                raise PolicyError(f"argument {arg!r} points outside the workspace") from None



# Environment handed to a child process.
#
# Why this is not "just inherit everything"
# -----------------------------------------
# Two failure modes pull in opposite directions, and the previous hardcoded
# PATH got the balance wrong in a way that only showed up in CI:
#
#   * Inheriting the full parent environment lets the agent read every secret this
#     process holds (provider keys, tokens). A command's output becomes model input,
#     so a leak here is a leak into the conversation.
#   * Hardcoding /usr/local/bin:/usr/bin:/bin guarantees the agent cannot see its own
#     virtualenv. `pytest` exists only at .venv/bin/pytest on a clean machine, so
#     "prove your fix works" failed in CI while passing locally -- the worst kind of
#     defect, because the local signal said the capability was fine.
#
# The compromise: a minimal, explicit allowlist of variables, plus the interpreter's
# own bin directory so the venv is reachable. Nothing else is passed through, so a
# secret is absent by construction rather than by a denylist that must be kept current.
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def _child_env(workspace: Workspace) -> dict[str, str]:
    """The environment a command may see: enough to run, not enough to leak."""
    bin_dirs = [str(Path(sys.executable).parent)]  # the venv (or system) bin
    venv_bin = Path(sys.executable).parent
    if (venv_bin.parent / "bin").is_dir():
        bin_dirs.append(str(venv_bin.parent / "bin"))
    bin_dirs += ["/usr/local/bin", "/usr/bin", "/bin"]

    seen: list[str] = []
    for entry in bin_dirs:
        if entry not in seen:
            seen.append(entry)

    env = {
        "PATH": ":".join(seen),
        "HOME": str(workspace.root),
        "PYTHONDONTWRITEBYTECODE": "1",
        # Git needs an identity to commit in a test repository; without one a command
        # fails for a reason unrelated to the agent's work.
        "GIT_AUTHOR_NAME": "clarapane",
        "GIT_AUTHOR_EMAIL": "clarapane@localhost",
        "GIT_COMMITTER_NAME": "clarapane",
        "GIT_COMMITTER_EMAIL": "clarapane@localhost",
    }

    # Locale: python can crash writing output under a non-UTF-8 default.
    for name in ("LANG", "LC_ALL", "TZ"):
        if name in os.environ:
            env[name] = os.environ[name]

    # Never pass a secret-looking variable. This is a backstop, not the guarantee --
    # the guarantee is that the list above is explicit and short.
    for name, value in os.environ.items():
        if any(marker in name.upper() for marker in _SECRET_MARKERS):
            continue
        if name in env:
            continue
        # A command may legitimately need PYTHONPATH or a proxy; allow those alone.
        if name in ("PYTHONPATH", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
            env[name] = value

    return env


def run_command(
    workspace: Workspace,
    argv: list[str] | str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Run an allowlisted program inside the workspace. Never uses a shell."""
    if isinstance(argv, str):
        # Accept a string only to reject it clearly: accepting one would invite the
        # model to pass "pytest && rm -rf /" and expect shell semantics.
        try:
            parsed = shlex.split(argv)
        except ValueError as exc:
            raise ToolError(f"could not parse command: {exc}") from None
        argv = parsed

    validate_argv(list(argv), workspace)

    bounded_timeout = max(1, min(int(timeout), MAX_TIMEOUT))
    started = time.perf_counter()
    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            cwd=str(workspace.root),
            capture_output=True,
            text=True,
            timeout=bounded_timeout,
            shell=False,          # explicit: this is the whole point
            env=_child_env(workspace),
        )
        code = completed.returncode
        out, err = completed.stdout, completed.stderr
    except FileNotFoundError as exc:
        # Before this, a missing program propagated as an uncaught exception and the
        # trace recorded "FileNotFoundError" with no guidance. Name the program and
        # the PATH that was searched, so the failure explains itself.
        raise ToolError(
            f"program not found: {argv[0]!r}. "
            f"Searched PATH={_child_env(workspace)['PATH']}. "
            "Use an allowlisted program, or `python -m <module>` so the workspace "
            "interpreter resolves it."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        code = -1
        out = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = f"timed out after {bounded_timeout}s"

    duration = int((time.perf_counter() - started) * 1000)
    stdout, t1 = _truncate(out or "")
    stderr, t2 = _truncate(err or "")

    return CommandResult(
        argv=list(argv),
        exit_code=code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration,
        timed_out=timed_out,
        truncated=t1 or t2,
    ).as_dict()


def register_exec_tools(registry: ToolRegistry, workspace: Workspace) -> None:
    def handler(argv: list[str] | str, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
        return run_command(workspace, argv, timeout)

    registry.register(ToolSpec(
        name="run_command",
        description=(
            "Run an allowlisted program inside the workspace and return its output. "
            "There is NO shell: pass program and arguments as a list, no pipes, "
            "redirects or &&. Allowed programs: " + ", ".join(sorted(ALLOWED)) + ". "
            "Use this to prove an edit works, e.g. [\"pytest\", \"calculator\", \"-q\"]."
        ),
        parameters={
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Program and arguments, e.g. ['pytest', 'calculator', '-q'].",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Seconds, 1-{MAX_TIMEOUT}.",
                    "default": DEFAULT_TIMEOUT,
                },
            },
            "required": ["argv"],
        },
        risk="exec",
        gate=True,
        handler=handler,
    ))
