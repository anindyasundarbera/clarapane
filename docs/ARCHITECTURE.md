# Architecture

How Clarapane is put together, and why each part exists. Every claim here is checkable
against the code; where a check exists, it is named.

---

## The shape of a run

```
task
  │
  ▼
capability profile ──── which tools the model may even see (fixed for the run)
  │
  ▼
┌─────────────── agent loop (sdk_agent.py) ───────────────┐
│  ask ──> inspect tool calls ──> validate ──> gate ──> run │
│   ▲                                            │          │
│   └──────────── observations (as data) ◄───────┘          │
└───────────────────────────────────────────────────────────┘
  │
  ▼
EventLog (one writer) ──> runs/*.jsonl ──> make divergences / evals
```

One process, one workspace, one trace. No queue, no service, no database.

---

## The eight invariants

Each one exists because its absence produced a specific, observed failure.

### 1. Propose before mutate

`propose_edit` returns a diff and a `proposal_id`; it writes nothing. `apply_edit`
consumes that id and writes. A proposal is **single-use**, **run-scoped**, and bound to
the hash of the file it was diffed against.

*Prevents:* an edit nobody reviewed, and a concurrent change being clobbered.
*Check:* `tests/test_write_tools.py` — a proposal cannot be applied twice, and refuses
when the file changed underneath it.

### 2. Risk lives in the registry, not the prompt

`ToolSpec` declares `risk` (`read` / `write` / `exec`) and `gate`. The model can describe
any call it likes; it cannot change what that call is allowed to do.

*Prevents:* the model negotiating its own access.
*Check:* `tests/test_exec_tools.py` asserts `run_command` is `risk="exec"`, `gate=True`.

### 3. One log writer

All events go through `clarapane.events.EventLog`. Nothing else opens the trace file.

*Prevents:* a trace that records claims rather than events.
*Check:* audit `single-log-writer` scans the source for a second writer.

### 4. Errors are data

A tool failure returns `{"error": ...}` as a tool message. The model can recover; the
loop does not crash.

*Prevents:* one tool bug killing a whole run.
*Check:* `test_agent_loop.py::test_agent_recovers_from_a_tool_error`.

### 5. Capability profiles are fixed per run

Three profiles — `read_only` (5 tools), `edit` (8), `exec` (11). The set does not change
between turns.

*Prevents:* prompt-cache invalidation, and a model re-planning because tools appeared or
vanished mid-run. Measured: 88% of the tool block is cached on repeat calls, and tool
count did not predict latency (11 tools 1.7 s vs 5 tools 2.4 s).
*Check:* audit `plan-integrity` and `test_profiles.py`.

### 6. Least privilege by construction

A profile's tool list is the only thing the model sees. `select()` raises if a profile
names an unregistered tool.

*Prevents:* capability creep, and — the real instance — a profile advertising tools that
did not exist.
*Check:* `test_profiles.py::test_every_advertised_profile_can_actually_be_built`. This
test was added *because* `exec` was broken and nothing noticed.

### 7. Verify before claiming

A `finish` claim is valid only when the run could not mutate anything, **or** a mutating
tool actually ran and succeeded. Otherwise the run reports `finished=False`,
`reason="finish_unverified"`, regardless of what the model says.

*Prevents:* the defect this project exists for — a confident summary standing in for
evidence.
*Check:* four regression tests in `test_write_tools.py`, including the exact live bug
where a denied write was reported as success.

### 8. Path jail on every access

Every path resolves through `Workspace.resolve()`, which has **separate** read and write
boundaries: the agent may read its own source to reason about itself, but never write it.

*Prevents:* escape from the workspace, and self-modification.
*Check:* `test_policy.py` — `../escape`, `/etc/passwd`, `.env`, and source writes all fail.

---

## The loop, mechanically

`Agent.run()` is deliberately the only place conversation state mutates:

- **The agent is stateless; the transcript is the state.** A model call is a pure
  function of the message list. Replay the JSONL and you get the same decisions.
- **Three exits, all logged:** `finish`, `stall` (prose reply, no tool), `step_cap`
  (default 12 model turns). A fourth, `injection_blocked`, aborts at the tool boundary.
- **Four failure paths through `_fail`:** malformed JSON arguments, unknown tool, policy
  denial, and bad arguments (`TypeError` from signature binding). Each becomes a tool
  message, never an exception.
- **`finish` is intercepted before dispatch**, and emitted by the loop rather than by
  dispatch, because `verified` depends on the whole step list.

### Why the gate is not inside the tool

If a tool knew how to ask for approval, a model could reason about the tool's
description and argue its way past it. The gate sits between registry lookup and handler
execution. The tool has no say in the outcome.

An approval is bound to a 12-hex **fingerprint** of the exact payload, recorded with the
**operator** identity. `proceed`, `y`, `ok` and `sure` are rejected: they do not bind
consent to content. The loop re-derives the fingerprint before executing, so a gate that
approves the wrong payload cannot cause a write.

---

## The trace

Append-only JSONL. Twelve declared events, validated against `TypedDict` schemas on every
write — an undeclared field is a hard error at the call site, not a silent shape change.

```
run_start  model_call  tool_call  approval  finish  divergence
stall  run_end  provider_error  recovery_failed  injection_blocked
```

Every record carries `ts`, `run_id`, `seq` (a total order independent of clock skew), and
`event`. JSONL was chosen over a database because an append plus a flush cannot fail the
way a database write can; a database, if added, must be a **derived** index rebuildable
from the log.

### Divergence: the predictability metric

`divergence` records every failed attempt and every place the harness or model **guessed**.
Ten classes in a closed taxonomy, each with a `source` and a `remedy`:

| Source | Meaning | Action |
|---|---|---|
| `harness` | **our** misprediction | always a defect |
| `model` | the model's mistake | measure, do not "fix" |
| `policy` | a gate doing its job | usually a misconfigured case |

The goal is not zero divergence. It is **zero harness divergence** and a falling model
divergence — the part policy can actually remove.

An undeclared class is a `BLOCKER` in the audit, so the count cannot rot into a misc
bucket. `make divergences --strict` fails on harness divergence only.

---

## Measurement

`evals/` runs deterministic cases against a **fresh copy** of the fixture per case, so
order cannot change the score. Pass is all-or-nothing per case: averaging away a false
success would hide the exact defect the harness exists to find.

A case that never reached the model is reported **INVALID**, not scored. Zero failures
from zero data must never read as a clean result.

The report records the **git SHA** and the **model**, so a score is attributable. A score
that moves because the model changed measures nothing.

`scripts/check_docs.py` verifies that documentation claims match the code — profiles,
events, `make` targets, backlog ids, version. Documentation that can drift is treated as
a defect, not as prose.

---

## Deliberate non-choices

Each was considered, measured, and rejected. The measurement is the reason, not taste.

| Rejected | Why |
|---|---|
| Per-turn dynamic tool selection | Invalidates the prompt-cache prefix. Tool count did not predict latency, and a 5×-per-condition trial found no behavioural difference (5/5 vs 5/5) |
| Blocklist of shell metacharacters | With `shell=False` those characters are inert data. The check gave no safety and rejected `python3 -c "import time; time.sleep(5)"` |
| A shared coordination ledger for agents | Did not reduce wasted work; stale rejections rose 5 → 8. Only per-agent attribution survived, as a read function. See `docs/evidence/aop-decision-2026-10-23.md` |
| An LLM judge for evals | A judge answers "does this look right"; an assertion answers "did the file change and does the test pass". The second cannot be talked out of a verdict |
| Committing directly to `main` | Protected by a GitHub ruleset with `current_user_can_bypass: never`, plus local hooks and an audit check |

---

## Honest limits

Stated here because an overclaim is the defect this project exists to prevent.

- **The exec allowlist is not a sandbox.** `python`, `python3` and `git` are allowlisted,
  and each can do arbitrary work. A container is the only real isolation.
- **Injection detection is pattern-based and evadable.** The real boundary is capability:
  a `read_only` run has no write, no exec, and no network.
- **Approval proves what was approved and by whom, not that it was understood.**
- **Evals run against one 27-line fixture.** That is a smoke test, not a benchmark.
- **No LangChain/LangGraph comparison exists yet.**
- **The package is not published.** It runs from the tree via `PYTHONPATH=src`.
