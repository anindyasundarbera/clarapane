"""CLI: run the raw-SDK agent on a task.

    .venv/bin/python -m clarapane.cli "where is add defined?"
    .venv/bin/python -m clarapane.cli "find the bug in add" --model glm-5.3

Phase 2 is read-only: the tool set contains no mutating tool, so an agent that
tries to edit will get a clear `unknown tool` error rather than a surprise write.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import identity, profiles
from .backlog import BacklogError, load as load_backlog, reminder as backlog_reminder
from .events import EventLog
from .policy import PolicyError, Workspace
from .profiles import ProfileName
from .provider import Provider
from .registry import ToolRegistry
from .sdk_agent import Agent
from .gate import InteractiveGate, PolicyGate
from .proposals import ProposalStore
from .tools.finish_tool import register_finish_tool
from .tools.read_tools import register_read_tools
from .tools.write_tools import register_write_tools

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_WORKSPACE = PROJECT_ROOT / "sandbox"
RUNS_DIR = PROJECT_ROOT / "runs"


def current_plan_step() -> str | None:
    """The active training step, so each run states what it is for."""
    import json as _json

    try:
        data = _json.loads((PROJECT_ROOT / "plan.json").read_text())
    except (OSError, ValueError):
        return None
    for step in data.get("steps", []):
        if step.get("status") == "in_progress":
            return step.get("title", step.get("id"))
    return None


def build_agent(
    *,
    workspace_root: Path,
    model: str | None,
    max_steps: int,
    profile: ProfileName | None = None,
    task: str = "",
    verbose: bool = True,
    log_path: Path | None = None,
    app_name: str | None = None,
    gate: Any = None,
) -> tuple[Agent, EventLog]:
    provider = Provider(env_path=str(PROJECT_ROOT / ".env"))
    workspace = Workspace(root=workspace_root)

    # Register everything that exists, then let the profile decide what the model
    # is allowed to see. Registration is a superset; the profile is the policy.
    registry = ToolRegistry()
    register_read_tools(registry, workspace)
    register_write_tools(registry, workspace, ProposalStore(), step_getter=lambda: 0)
    register_finish_tool(registry)

    chosen: ProfileName = profile or profiles.infer(task)
    scoped = profiles.select(registry, chosen)

    log = EventLog(log_path or RUNS_DIR / "runs.jsonl")

    agent = Agent(
        provider, scoped, workspace, log,
        model=model, max_steps=max_steps, verbose=verbose,
        app_name=identity.app_name(app_name),
        gate=gate if gate is not None else PolicyGate(allow_writes=False),
        # The profile the run was launched under, recorded on run_start so the
        # trace answers "what was this run allowed to do?" in one record.
        run_start_fields={
            "profile": chosen,
            "profile_source": "cli" if profile else "inferred",
        },
    )
    return agent, log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Clarapane coding agent (raw SDK)")
    ap.add_argument("task", help="Natural-language task for the agent")
    ap.add_argument("--model", help="Override DEFAULT_OLLAMA_MODEL")
    ap.add_argument("--workspace", default=str(DEFAULT_WORKSPACE),
                    help="Workspace root the agent is jailed to")
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument("--profile", choices=["read_only", "edit", "exec"],
                    help="Capability profile. Defaults to inference from the task.")
    ap.add_argument("--operator", default=None,
                    help="Accountable human for approvals (default: $CLARAPANE_OPERATOR_ID or OS login)")
    ap.add_argument("--yes", action="store_true",
                    help="Headless approval of writes. Recorded as actor=policy, not a human.")
    ap.add_argument("--name", default=None,
                    help=f"Display name for this run (default: ${identity.ENV_VAR} or Clarapane)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.yes:
        chosen_gate = PolicyGate(allow_writes=True, reason="--yes")
    elif args.profile in ("edit", "exec"):
        chosen_gate = InteractiveGate(operator=args.operator)
    else:
        chosen_gate = PolicyGate(allow_writes=False, reason="read-only profile")

    try:
        agent, log = build_agent(
            workspace_root=Path(args.workspace),
            model=args.model,
            max_steps=args.max_steps,
            profile=args.profile,
            task=args.task,
            app_name=args.name,
            gate=chosen_gate,
            verbose=not args.quiet,
        )
    except (PolicyError, RuntimeError) as exc:
        print(f"setup failed: {exc}", file=sys.stderr)
        return 2

    print(identity.banner(args.name))
    print(f"operator  : {identity.operator(args.operator)}")
    step = current_plan_step()
    if step:
        print(f"plan step : {step}")
    print(f"task      : {args.task}")
    print(f"model     : {agent.model}")
    print(f"workspace : {agent.workspace.root}")
    print(f"tools     : {', '.join(agent.registry.names())}")

    # Surface outstanding deferred work on every run. Decisions NOT to build
    # something are easy to lose; this keeps them in front of whoever is driving.
    try:
        nudge = backlog_reminder(load_backlog())
    except (BacklogError, OSError, ValueError):
        nudge = None
    if nudge:
        print(f"{nudge}")

    print(f"run_id    : {log.run_id}\n")

    try:
        result = agent.run(args.task)
    finally:
        log.close()

    print(f"\nfinished  : {result.finished} ({result.reason})")
    print(f"steps     : {len(result.steps)}")
    print(f"trace     : {log.path}  (run_id={log.run_id})")
    return 0 if result.finished else 1


if __name__ == "__main__":
    sys.exit(main())
