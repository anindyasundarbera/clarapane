# Handoff — 2026-10-23

Written for a new session with no memory of the one that produced it. Read this before
proposing any work. It is deliberately blunt about what is unproven.

---

## 1. What this project is

**Clarapane** — a glass-box coding agent. Every action is proposed, risk-classed, gated,
and written to an append-only JSONL trace.

The operator's intent, stated on 2026-10-23 and **not yet reflected in the docs** (see
§8, item 1):

> A glass-box coding assistant **platform**. A human can see what is happening right now,
> approve flow, logic, deviation, and actions. Everything visible is controllable from a
> **control pane**.

The distinction that matters:

| | Means | Does **not** mean |
|---|---|---|
| **Autonomous** | The agent drives itself — decides tools, reads, iterates. No prompt per action. | Unsupervised |
| **Controllable** | A human sees each atomic step live and decides at *irreversible* boundaries. | Approval for every action |

Autonomy in execution, control at the boundary. This is why `propose_edit` and `apply_edit`
are separate tools.

---

## 2. Repository state at handoff

| Item | Value |
|---|---|
| Branch | `docs/capabilities-architecture` |
| HEAD | `1b26552`, working tree **clean** |
| `origin/main` | `a950ed3` (PR #5 merged) — branch is 1 commit ahead |
| Open PR | **#6** — `docs: document intent, capabilities and architecture` |
| CI | **green** on both `main` and this branch |
| Tests | **225 passed**, offline, ~3 s |
| Docs checks | **12**, no drift |
| Audit checks | **14**, 0 defects |
| Evals | **6/6**, sha `7aa9e5f`, `deepseek-v4.1-flash:cloud` |
| Divergence | 2 across 15 runs — 1 policy, 1 model, **0 harness** |

**First action for the new session:** `gh pr merge 6 --squash --delete-branch`, then
`git switch main && git pull`. Everything below assumes main.

---

## 3. Where to read next

| Document | Answers |
|---|---|
| [`docs/INTENT.md`](../docs/INTENT.md) | What it is for, what "done" means, explicit non-goals |
| [`docs/CAPABILITIES.md`](../docs/CAPABILITIES.md) | Can and cannot, each claim tied to a module and a test |
| [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) | Eight invariants, loop mechanics, rejected alternatives |
| [`AGENTS.md`](../AGENTS.md) | Binding rules: secrets, commits, scope lock, defect policy |
| [`plan.json`](../plan.json) | Ordered plan, validated by `make audit` |
| [`backlog.json`](../backlog.json) | Deferred work, each with a trigger and acceptance criteria |
| [`docs/SETUP.md`](../docs/SETUP.md) | Machine migration: everything deliberately **not** in git |

---

## 4. How to work in this repo

```bash
make test        # 225 offline tests, includes the audit
make audit       # 14 defect checks
make docs        # documentation drift check
make divergences # predictability report by class and source
make eval        # live model, ~4 min, needs credentials
make run TASK="where is add defined?"
make backlog     # actionable deferred work
```

**Hard rules** (enforced, not advisory):

- Never commit to `main` directly — GitHub ruleset + local hooks + audit check.
- Never commit `.env`. Only `.env.example`, key names only.
- Every commit: `<type>(<scope>): <lowercase subject>` + `Why / What / Decision / Evidence / Refs:`.
- Deferred work goes in `backlog.json`, with a trigger **and** acceptance criteria.
- Mark the hook point in code with `TODO(<backlog-id>)`; a test verifies the id exists.
- **Scope lock** (`AGENTS.md` §0a): before any work, ask whether it serves the current
  plan step. If not, it goes to the backlog. Deviation is a defect.

Commit via `make commit MSG="..."` — it stages everything, so evidence is never abandoned.

---

## 5. The plan

| Step | Status | Note |
|---|---|---|
| Foundation | done | loop, tools, policy, trace |
| Evals | done | 6/6, deterministic, deterministic assertions only |
| **Execution** | **in progress** | `run_command` exists; generalisation is unproven |
| Framework comparison | pending | LangChain / LangGraph, measured against the baseline |

`execution` is blocked on one question: **does the agent work past a 27-line fixture?**

---

## 6. What is proven, and what is only claimed

Every claim below is checkable with a command in §4.

**Proven by test or eval**

- Four atomic read tools, bounded output.
- Propose-then-apply, single-use ids, hash-bound, refuses clobbering.
- Approval bound to a 12-hex payload fingerprint and an operator identity; `proceed`,
  `y`, `ok`, `sure` are rejected.
- Verify-before-claiming on **every** exit path — a denied write reports
  `finish_unverified`, not success.
- Exec allowlist: 7 programs, `shell=False`, no network programs at all.
- Checkpoint / rollback.
- Path jail with separate read and write boundaries.
- Injection blocked when a directive **and** an actionable payload co-occur.
- 12 declared events, validated on write; one writer.
- Divergence: 10-class closed taxonomy; `--strict` fails on harness divergence only.

**Claimed but not proven**

- That the agent is useful on a real repository. All evals use one 27-line fixture.
- That it reduces human effort. Nothing measures that yet.
- That the exec allowlist resists a determined adversary. `python3` is allowlisted and
  can do anything; this is defence in depth, not a sandbox.
- That injection detection survives a novel phrasing. It is pattern-based and evadable;
  the durable protection is capability, not detection.
- That approval implies comprehension. It proves *what* was approved and *by whom*.

---

## 7. Known defects and traps

- **Headless approval does not exist.** `InteractiveGate` needs a TTY; `PolicyGate`
  blanket-allows or blanket-denies. There is no transport for "show a human this diff and
  wait." A server, CI job, or control pane **cannot approve anything today**. This is the
  binding constraint on the platform vision.
- **`find_dotenv()` raises `AssertionError`** from a `python -` heredoc. Pass an explicit
  path.
- **Reasoning traces arrive out-of-band** in `message.model_extra["reasoning"]`, not in
  content.
- **A check that cannot fail is a defect.** Five separate checks in this project looked
  green while being vacuous, each exposed only by a negative control. Write the negative
  control **before** trusting a new check.
- **Tool count does not predict latency**, and ~88 % of the tool block is prompt-cached.
  Do not "optimise" by trimming tools per turn.
- **Do not treat a stale claim as harmless.** README asserted "no CI exists" long after
  CI was added; that class of drift now has a check (`check_ci_claims_match_reality`).

---

## 8. Open decisions for the next session — brainstorm needed

This is what the operator wants to work through. Nothing here is decided.

### 8.1 The control pane is out of scope *in writing*, and should not be

`plan.json` and `docs/INTENT.md` both list **"Web UI, trace viewer, or dashboard"** as
explicitly not the goal. That was written before the operator clarified that the pane **is
the product surface**. The exclusion is wrong and must be corrected — but the correct
replacement is a judgement call, not a mechanical edit.

Questions to settle:

- Is the pane a *plan step* of its own, or a horizon that shapes the design of every step?
- What is the minimum pane that makes the vision real? Candidates: read-only live trace;
  plus approve/deny with diff and fingerprint; plus run control; plus history and replay.
- Does the pane change the architecture? Today the trace is append-only JSONL flushed per
  line, and the gate is a pluggable `decide()`. Both appear to be the right seams — but
  that has not been tested under a real UI.

### 8.2 Approval transport — the actual blocker

Before any pane can work, a headless run needs a way to publish a pending approval and
block until a decision returns. This is arguably a **Phase-4 blocker**, not a feature: the
docs claim the system is "workable" and "autonomous", and it currently cannot run
unattended with per-action control.

Questions:

- Is this a new `Gate` implementation (keeps the seam), or does the loop need to become
  resumable?
- Blocking on a decision holds the process. Is that acceptable, or does the run need to
  suspend and resume — which is a LangGraph-shaped problem?
- Does this pull the framework-comparison step forward? `interrupt` and a checkpointer are
  precisely the LangGraph features this needs, which would make the comparison measured
  rather than academic.

### 8.3 Generalisation past the fixture — the plan's current step

Six of six evals run against a 27-line file with a seeded bug. That is a smoke test. What
counts as evidence of generalisation?

Candidates worth considering: a repository the agent has never seen; a task with no seeded
defect; a change spanning more than one file; a task where the agent must decide *not* to
change anything.

### 8.4 Sequencing — the honest tension

- Building the pane before the agent generalises shows an unproven thing beautifully.
- Fixing approval transport first makes the platform *possible* and unblocks autonomy.
- Generalising first makes the pane worth looking at.

These conflict. The operator's stated priority is the platform; the project's own rule is
"build only what measurement justifies". Resolve this explicitly rather than drifting.

### 8.5 Smaller open items

| Backlog id | Priority | Trigger |
|---|---|---|
| `request-capability-escalation` | high | an eval fails with `unknown tool` |
| `reviewer-agent` | high | after evals generalise |
| `structural-outline-tool` | medium | file > 400 lines, or a step-cap on exploration |
| `stuck-loop-investigation` | medium | before multi-step evals are trusted |
| `sqlite-trace-ingest` | medium | when jq cannot express an eval assertion |
| `aop-attribution-ledger` | medium | when concurrent runs exist |

Note `dynamic-tool-selection` is **rejected with measurements** — do not re-propose it
without new data.

---

## 9. How decisions are recorded

`git log` is the project's memory. The body of every commit answers *why*:

```bash
git log --grep="Decision:"        # the reasoning behind each choice
git log --grep="Refs: backlog:"   # which deferred work each commit touched
```

A decision that is not in git, the backlog, or the docs did not happen. If the next
session reaches a conclusion about the control pane, it belongs in `docs/INTENT.md` and
probably in a new plan step — not only in a conversation.

---

## 10. What the next session should do first

1. Merge PR #6, pull `main`, confirm `make test` is green.
2. Read `docs/INTENT.md` and `AGENTS.md` §0a.
3. **Brainstorm §8 with the operator.** Do not start implementing. The pane is a
   product-shaping decision; building it before the shape is agreed repeats the derailment
   that §0a exists to prevent.
4. Record the outcome in `plan.json` and `docs/INTENT.md` **before** writing code.
