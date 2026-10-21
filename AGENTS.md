# AGENTS.md — rules for anyone (human or AI) working in this repo

**Project name:** Clarapane (`clarapane`) — a glass-box coding agent.
The display name is resolved at runtime by `clarapane.identity` and can be
changed without touching code, via the `CLARAPANE_APP_NAME` environment
variable or the `--name` CLI flag.

Resolution order: explicit argument, then `CLARAPANE_APP_NAME`, then
`Clarapane`. The name is read at call time, never at import time. It is
cosmetic only — it does not alter schema, policy, or behaviour.
Short commands: `make test`, `make audit`, `make run TASK="..."`, `make clean`.

These rules are binding. They exist because this project's value is not only
*what* it does but whether its history explains *why*. Read this before
committing, before touching secrets, and before skipping a tool gate.

---

## 0a. Purpose and scope lock — read before proposing work

**This project exists to prepare one person for an agentic-AI interview by building
an agent hands-on.** It is a training artefact, not a product. Every activity must
serve that purpose.

The plan lives in **`plan.json`**, is validated by `scripts/audit_defects.py`
(`plan-integrity`), and its current step is printed on every CLI run. In order:

1. Raw OpenAI SDK: basic agent loop, tool calling, conversation state
2. Ollama Cloud models, switchable per run
3. LangChain: the same agent, to see what the framework hides
4. LangGraph: the same agent as a graph, for state and interrupts
5. Evaluation and interview rehearsal

**Deviation is a defect.** It is logged with the same severity vocabulary as section 0.

### The rule

Before starting any work, ask: **does this serve the current step of the plan?**

- If yes, do it.
- If it is a genuine BLOCKER, fix it, and say plainly that it interrupted the plan.
- Otherwise **record it in `backlog.json` and continue the plan.** Do not build it now.

`plan.json` advances only when a step's work is finished: mark it `done`, name what it
covered, set the next step to `in_progress`, and update `current_step`. The audit fails
if two steps are active at once or a later step starts before an earlier one finishes.

Every unplanned addition needs an explicit decision from the operator. "It would make
the system better" is not sufficient: a better system that never reaches the interview
preparation is a failed artefact.

### Why this rule is written in the imperative

It was earned. A three-hour interview-preparation session ran more than five hours and
never reached LangChain, because each turn surfaced a real defect and each defect was
fixed immediately. Individually justified, collectively a derailment. The failure was
not any single fix; it was never asking whether the fix belonged in this session.

### Recognising a deviation in progress

Any of these is a signal to stop and check scope:

- More new infrastructure than new understanding
- The plan's next step has slipped by more than one work item
- The operator has not confirmed the current task in the last few steps
- A tool, guardrail, or policy is being added that the interview plan never called for

When that happens: stop, state the deviation, and ask whether to continue or return to
the plan. Do not silently continue.

## 0. Defect policy — read this first

**Serious and blocking defects outrank every other task.** Feature work, phase
progress, and cosmetic polish all stop until they are fixed. This is not advisory.

| Severity | Definition | Required action |
|---|---|---|
| **BLOCKER** | The agent can do the wrong thing and report success, or the trace cannot be trusted. | Stop all other work. Fix before proceeding. |
| **MAJOR** | A capability is unreliable, or a claim cannot be verified from the trace. | Fix before building on top of it. |
| **MINOR** | Cosmetic or ergonomic. | May defer, with a backlog entry. |

The audit is executable, so "are there blocking bugs?" is a command, not an opinion:

```bash
PYTHONPATH=src .venv/bin/python scripts/audit_defects.py            # full report
PYTHONPATH=src .venv/bin/python scripts/audit_defects.py --strict   # exit 1 on any BLOCKER
```

It is also exercised by the test suite, so a BLOCKER fails tests. Current checks:

- `.env` must not be tracked
- every run must have exactly one terminal event (`run_start` == `run_end`)
- no run may report finish with `verified: false`
- the committed trace must satisfy the declared schema
- only `EventLog` may write the trace
- high-priority backlog items must have real acceptance criteria
- static backstops for the agent invariants in section 6

**Rules:**

1. If you find a BLOCKER, fixing it *is* the task. Do not defer it to a backlog
   item. Do not "note it and move on". Deferring a blocker is itself a defect.
2. A bug that is reproducible but whose cause is unknown is a **BLOCKER, not a
   curiosity**. The stall bug reproduced 3 times in 5 runs, was logged as "medium
   priority, investigate later", and then blocked the next phase.
3. Prefer measuring before theorising. Three claims in this project were refuted
   by measurement (per-turn tool scoping, tool count affecting latency, scoped
   tools changing agent behaviour). A plausible mechanism is not evidence.
4. When you fix a behaviour, add the check that would have caught it.

## 1. Secrets

- **Never commit `.env`.** It is gitignored; keep it that way.
- Only `.env.example` is committed, and it contains **key names only**, never values.
- If a secret is ever committed, deleting the file in a later commit is **not**
  sufficient — it stays in history. Treat it as a rotation incident: rotate the
  key, then rewrite history.

Verify before committing:

```bash
git status --short            # .env must never appear
git diff --cached --name-only | grep -E '(^|/)\.env$' && echo "STOP: secret staged"
```

## 1a. Atomic completion — a change-set has peer surfaces

Twice in this project a change was **adopted on one surface and abandoned on
another**. Both times the cause was selective `git add <files>`: the author lists
the files they were thinking about and omits a peer artifact.

| # | Changed | Forgotten | Consequence |
|---|---|---|---|
| 1 | `.env.example`, `identity.py`, `cli.py` | `.env` | The setting was documented, wired, and **inert** |
| 2 | four source files | `runs/runs.jsonl` | The commit's own `Evidence:` cited a trace it did not contain |

Rule: **a change-set is not complete until every peer surface has been considered
and either updated or explicitly excluded.** Common peers:

- a new setting → `.env.example` **and** `.env` **and** the code that reads it
- agent behaviour → `src/` **and** the trace under `runs/` **and** the backlog entry
- a new invariant → the code **and** a test **and** the audit check
- a rename → package, imports, docs, `AGENTS.md`, `.github/`, installed config

This is enforced, not advised:

- `.githooks/pre-commit` refuses a commit while a tracked `runs/` artifact is
  modified but unstaged.
- `make commit MSG="..."` stages everything, so the default path cannot forget.
- `scripts/audit_defects.py` reports `evidence-abandoned` and
  `env-matches-template`, and runs in the test suite.
- `tests/test_repo_hygiene.py` compares `.env` against `.env.example` by key name.

Override deliberately and rarely: `ALLOW_UNSTAGED=1 git commit ...`

## 1b. Approval must bind to the exact work

A bare **"proceed" is not an approval**. It authorizes *future* work rather than the
work in front of the operator, and it leaves no record of what was consented to.
This was a real defect: `proceed` was accepted as consent for an edit nobody had
reviewed.

Approval requires both:

1. **An accountable operator.** `CLARAPANE_OPERATOR_ID`, else the OS login. Never
   empty. A policy approval records `actor="policy"` and `operator="policy:<reason>"`
   and must never claim a human approved.
2. **A binding fingerprint.** A 12-hex hash of the exact tool + payload. The operator
   types it to approve, the loop re-derives it before executing, and a mismatch
   aborts. The fingerprint is recorded in the `approval` event.

Enforced by `tests/test_repo_hygiene.py`, which asserts that no gate accepts any of
`y / yes / proceed / ok / sure / lgtm / approve / ""`, and that every decision carries
an operator and a fingerprint. Enforced again in the loop, so a gate that approves a
different payload cannot cause a write.

Honest limit: this proves *what* was approved and *by whom*. It does not prove the
operator read or understood it.

## 1c. Never commit or push directly to main

`main` is protected by a GitHub ruleset (id 25580492, `enforcement: active`,
`current_user_can_bypass: never`). Every change reaches it through a pull request.

This was a real violation: 28 commits landed directly on `main`, and one was pushed.
The ruleset now makes that impossible server-side, regardless of local configuration.

Three layers, deliberately redundant:

| Layer | Where | What it does |
|---|---|---|
| Preventive | `.githooks/pre-commit` | Refuses a commit while HEAD is a protected branch |
| Preventive | `.githooks/pre-push` | Refuses a push whose target is a protected branch |
| Authoritative | GitHub ruleset on `main` | Rejects the push server-side; no local bypass exists |
| Detective | `audit_defects.py` → `no-direct-main-commits` | Reports commits on main with no PR reference |

A local hook is a courtesy, not a control: `--no-verify` and a different clone both
bypass it. The ruleset is the control. The audit is how you find out when a control
was bypassed.

**Workflow:**

```bash
git switch -c feat/<short-description>
# ... work, make commit MSG="..." ...
git push -u origin HEAD
gh pr create --fill
gh pr merge --squash --delete-branch
```

A squash merge records the PR number in the commit, which is what the audit looks
for as evidence of review.

Deliberate override for an emergency, visible to the audit afterwards:
`ALLOW_PROTECTED_COMMIT=1` / `ALLOW_PROTECTED_PUSH=1`.

## 2. Commit messages

Format: **Conventional Commits**, enforced by `.githooks/commit-msg`.

```
<type>(<scope>): <lowercase subject, max 72 chars, no trailing period>
```

Allowed types: `feat` `fix` `docs` `test` `refactor` `perf` `chore` `build` `ci`

The body must be self-sufficient — a reader should understand the change
**without opening the diff**. Required sections:

```
Why:
  The problem or risk that motivated this.
What:
  - Bullet list of concrete changes.
Decision:
  Alternatives considered and why rejected. Omit only if there were none.
Evidence:
  Tests, measurements, or files that prove it works.
Refs: backlog:<id> | phase:<n> | #issue
```

Why this matters: `git log` is the project's memory.

```bash
git log --grep="Decision:"          # why things are the way they are
git log --grep="Refs: backlog:"     # which deferred work each commit touched
```

## 3. Commit granularity

One commit per **decision or coherent capability**, not per file and not per
work session. A commit must be individually understandable and leave the tree
in a working state.

## 4. Validation before committing

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests -q
python scripts/check_commits.py
```

Do not commit with failing tests. If a test must be skipped, say so in the
commit body under `Evidence:`.

## 5. Deferred work and the backlog

Anything consciously **not** built goes into `backlog.json`, never only into a
conversation. Use the CLI:

```bash
PYTHONPATH=src .venv/bin/python -m clarapane.backlog due
PYTHONPATH=src .venv/bin/python -m clarapane.backlog add --title "..." --why "..." --area "..."
```

Every open item needs a `trigger` and `acceptance` criteria or `validate()` rejects it.
Mark the hook point in code with `TODO(<backlog-id>)`; a test enforces that every
such marker names a real backlog item.

Rejected ideas stay in the backlog with `status: "rejected"` and their evidence.
Do not re-propose a rejected item without new measurements.

## 6. Agent design invariants

These are not style preferences; tests depend on them.

1. **Propose before mutate.** Writes require a prior proposal; `apply_edit` is a
   separate, gated tool from `propose_edit`.
2. **Risk lives in the registry, not the prompt.** `ToolSpec.risk` and `.gate`
   decide permission. Never let the model negotiate its own access.
3. **One log writer.** All events go through `clarapane.events.EventLog`. Nothing
   else opens the trace file.
4. **Errors are data.** Tool failures return `{"error": ...}` as a tool message
   so the model can recover; they must not crash the loop.
5. **Capability profiles are fixed per run.** Never change the tool list between
   turns within a run — it invalidates the provider prompt cache.
6. **Least privilege is enforced by construction.** A profile's tool list is the
   only thing the model sees; `select()` raises on unknown tools.
7. **Never report an unproven success.** A `finish` claim is valid only when
   either the task was read-only, or a mutating tool was actually called. When
   mutation was offered but unused, report `finished=False` with
   `reason="finish_unverified"` even if the model claims success. A confident
   summary is not evidence.
8. **Path jail on every access.** All paths resolve through `Workspace.resolve()`.
   The agent may read its own source but never write it.

## 7. Environment

- Python: `uv venv .venv` then `uv pip install --python .venv/bin/python -e .`
- Run the agent: `PYTHONPATH=src .venv/bin/python -m clarapane.cli "<task>"`
- Tests: `PYTHONPATH=src .venv/bin/python -m pytest tests -q`
- Model pinning: `scripts/list_models.py --set <model>`

## 8. Things that will bite you

- `find_dotenv()` raises `AssertionError` when run from a `python -` heredoc.
  Pass an explicit path: `load_dotenv(".env", override=True)`.
- Ollama returns the reasoning trace out-of-band in `message.model_extra["reasoning"]`,
  not as content. It is logged; use it.
- Model aliases resolve both ways (`gpt-4o` style `:cloud` suffixes are optional).
- Tool count does **not** predict latency, and schema tokens are ~88% cached.
  Do not "optimise" by trimming tools per turn.
