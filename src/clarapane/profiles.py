"""Capability profiles: which tools a run is allowed to see.

Measured against the live provider (numbers, not intuitions)
------------------------------------------------------------
  * 11 tool schemas cost ~1060 prompt tokens (~20x the 46-token bare prompt),
    BUT ~88% is served from the provider's prompt cache on repeat calls.
    Schema billing is a small, mostly one-time cost.
  * Tool count did NOT predict latency: 11 tools 1.7s vs 5 tools 2.4s.
  * Step count DID predict cost: a looping run reached 5484 prompt tokens by
    step 12; a progressing run finished at 2497 by step 3.

A hypothesis we tested and REJECTED
-----------------------------------
We suspected that exposing write tools would change the model's second action
after reading a buggy file (i.e. that a read-only agent would loop instead of
progressing). A 5x-per-condition trial (scripts/tool_scope_experiment.py) gave:

    read+finish      : 5/5 re-read the file
    read+edit+finish : 5/5 re-read the file

No measurable difference. An earlier single-sample result suggested otherwise
and was noise. So tool scoping is NOT justified by agent behaviour or latency.

Why we still scope
------------------
  1. Least privilege: a read-only task that never receives `apply_edit` cannot
     mutate the workspace even if the model is confused or prompt-injected.
  2. Auditability: the profile is logged at run start, so "this run had no write
     tools" is a recorded fact rather than an inference from a 900-line trace.
  3. Failure clarity: calling a tool that wasn't offered produces a clean
     "unknown tool" error instead of a partially-allowed side effect.

And why the set stays FIXED for a whole run: the prompt cache is keyed on a
prefix that includes the tool block, so changing tools per turn throws away the
cached portion and can cost more than sending everything.
"""

from __future__ import annotations

import re
from typing import Literal

# A profile may only name tools that are actually registered. `exec` was removed once
# when its tools did not exist (it raised ValueError at startup); it is restored here
# now that run_command, git_checkpoint and git_rollback are real and tested.
ProfileName = Literal["read_only", "edit", "exec"]

# Ordered from least to most capability. Each entry names a tool that must be
# registered; `select` fails loudly if a profile references an unknown tool.
PROFILES: dict[ProfileName, tuple[str, ...]] = {
    "read_only": ("list_dir", "grep", "read_range", "find_symbol", "finish"),
    "edit": (
        "list_dir", "grep", "read_range", "find_symbol",
        "propose_edit", "apply_edit", "write_file", "finish",
    ),
    "exec": (
        "list_dir", "grep", "read_range", "find_symbol",
        "propose_edit", "apply_edit", "write_file",
        "run_command", "git_checkpoint", "git_rollback", "finish",
    ),
}

# Ordered from least to most capable. Used by the planned escalation tool to
# enforce monotonic widening: a run may ask for more, never for less.
ESCALATION_ORDER: tuple[ProfileName, ...] = ("read_only", "edit", "exec")


def can_escalate(current: ProfileName, target: ProfileName) -> bool:
    """True when `target` is strictly more capable than `current`."""
    return ESCALATION_ORDER.index(target) > ESCALATION_ORDER.index(current)

DESCRIPTIONS: dict[ProfileName, str] = {
    "read_only": "Inspect and answer. No mutations, no command execution.",
    "edit": "Read and edit files. No command execution.",
    "exec": "Full capability: read, edit, run allowlisted commands, checkpoint and roll back.",
    # "exec": "Full capability: read, edit, run commands, checkpoint and roll back.",
}


def select(registry, profile: ProfileName):
    """Return a registry view containing only the profile's tools.

    Raises if the profile names a tool that was never registered -- a typo in a
    profile should be a startup failure, not a silently missing capability that
    shows up later as an agent looping.

    TODO(request-capability-escalation): this function is the *only* place a
    profile is applied, so it is also where escalation will hook in. A granted
    request will call `select()` again with a wider profile and the result will
    replace the tool list for the next model turn. Tracked in backlog.json;
    run `python -m clarapane.backlog show request-capability-escalation`.
    """
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; choose from {sorted(PROFILES)}")

    wanted = PROFILES[profile]
    missing = [name for name in wanted if name not in registry]
    if missing:
        raise ValueError(
            f"profile {profile!r} needs unregistered tools: {missing}. "
            f"Registered: {registry.names()}"
        )

    selected = registry.__class__()
    for name in wanted:
        selected.register(registry.get(name))
    return selected


def infer(task: str) -> ProfileName:
    """Cheap keyword heuristic for picking a profile when the caller omits one.

    Deliberately dumb and inspectable. It is a *default*, not a policy: the CLI
    flag always wins, and the chosen profile is logged at run start so a wrong
    guess is visible in the trace.

    Word boundaries matter: naive substring matching classifies "where is `add`
    defined?" as an edit because "add" appears in it.
    """
    text = task.lower().strip()
    words = re.findall(r"[a-z_]+", text)

    def has(vocab: tuple[str, ...]) -> bool:
        return any(w in vocab for w in words)

    # Execution intent ("run pytest", "install", "build") needs a profile that can
    # run commands. None exists yet, so such tasks degrade to `edit` -- enough to
    # prepare the change, and the missing verification is visible in the trace
    # rather than hidden behind a profile that cannot be constructed.
    if has(("run", "pytest", "execute", "install", "build", "commit", "rollback")):
        return "edit"

    # The LEADING verb decides intent. This matters because words like "add" and
    # "find" are both verbs and plausible symbol names: "where is `add` defined?"
    # is a question, while "add a docstring" is an edit.
    lead = words[0] if words else ""
    interrogative_lead = {"where", "what", "which", "who", "how", "why", "when",
                          "does", "do", "is", "are", "can", "could", "should",
                          "explain", "describe", "show", "list", "find", "locate",
                          "summarize", "summarise", "review", "trace"}
    imperative_lead = {"fix", "implement", "change", "edit", "write", "remove",
                       "refactor", "rename", "update", "delete", "add", "create",
                       "make", "move", "replace", "insert", "patch"}

    if lead in interrogative_lead:
        return "read_only"
    if lead in imperative_lead:
        return "edit"
    # Fall back to a question mark, then to the safest profile.
    if text.endswith("?"):
        return "read_only"
    return "edit" if has(tuple(imperative_lead)) else "read_only"
