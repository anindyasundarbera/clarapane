# Backlog

Deferred work lives in [`backlog.json`](../backlog.json) and is managed with a CLI:

```bash
PYTHONPATH=src .venv/bin/python -m glassbox.backlog          # everything
PYTHONPATH=src .venv/bin/python -m glassbox.backlog due      # actionable now
PYTHONPATH=src .venv/bin/python -m glassbox.backlog show request-capability-escalation
PYTHONPATH=src .venv/bin/python -m glassbox.backlog start request-capability-escalation
PYTHONPATH=src .venv/bin/python -m glassbox.backlog done  request-capability-escalation
```

## Why

A decision **not** to build something is as load-bearing as the code, and it disappears the moment a conversation ends. This tracker keeps five states:

| Status | Meaning |
|---|---|
| `planned` | Real work, not started. Needs a trigger and acceptance criteria. |
| `in_progress` | Being worked on now. |
| `blocked` | Cannot proceed; the blocking reason is in `notes`. |
| `done` | Finished; carries `resolved_on`. |
| `rejected` | Considered and declined **with evidence**. Kept so it is not re-proposed. |

Every open item must carry a **trigger** (when to act) and **acceptance** (how you know it is finished). A backlog entry without those is a wish, and `validate()` rejects it.

## Reminders

Two mechanisms keep open items visible:

1. `glassbox.cli` prints a one-line nudge on every run when actionable items exist.
2. Source files carry a `TODO(<backlog-id>)` comment at the exact hook point, so the note sits where the change belongs rather than only in a list.

```bash
grep -rn "TODO(" src/
```

## Current items

| ID | Status | Area | Priority |
|---|---|---|---|
| `request-capability-escalation` | planned | profiles | high |
| `stuck-loop-investigation` | planned | agent-loop | medium |
| `dynamic-tool-selection` | rejected | profiles | low |

### `request-capability-escalation` (planned, high)

Profiles are fixed per run. If inference picks `read_only` for a task that needs an edit, the agent gets `unknown tool: propose_edit` and loops; today the only fix is a human re-running with `--profile edit`. The planned `request_capability(profile, reason)` tool makes asking for power an auditable, gated action instead of an invisible widening.

Hook points are marked in [profiles.py](../src/glassbox/profiles.py) (`select()`) and [sdk_agent.py](../src/glassbox/sdk_agent.py) (`_execute`, on the `unknown tool` path). `profiles.can_escalate()` already exists to enforce monotonic widening (`read_only → edit → exec`, never down).

### `dynamic-tool-selection` (rejected)

Kept deliberately: it documents a *measured* rejection. Per-turn tool rotation discards the prompt-cache prefix (~88% of the tool block was cached), tool count did not predict latency (11 tools 1.7s vs 5 tools 2.4s), and a 5×-per-condition trial found no behavioural difference (5/5 vs 5/5). Revisit only past ~40 tools or if prefix caching goes away.
