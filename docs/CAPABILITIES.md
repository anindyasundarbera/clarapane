# Capabilities

**What Clarapane can do today, what it cannot, and how each claim is verified.**

Every "can" below names the module and the test that proves it. Nothing here is
aspirational; if it is not verified, it is in the "cannot" section or in
[`backlog.json`](../backlog.json).

Last verified: 2026-10-23, `main`, 220 tests, evals 6/6.

---

## Can

### Understand code before changing it

Four atomic read tools, each returning a bounded slice rather than a whole file:

| Tool | Returns | Bound |
|---|---|---|
| `list_dir` | entries with type and size | 200 entries |
| `grep` | `file:line` hits | 60 hits, 400 chars/line |
| `read_range` | numbered lines plus `total_lines` | 400 lines |
| `find_symbol` | definitions and references | 60 references |

*Verified:* `tests/test_read_tools.py`, 8 tests.

### Propose, then apply

`propose_edit` returns a unified diff and a `proposal_id` — **nothing is written**.
`apply_edit` consumes the id. `write_file` creates new files and refuses to overwrite.

A proposal is single-use, run-scoped, and bound to the file hash it was diffed against.
A second apply fails; a changed file is refused rather than clobbered.

*Verified:* `tests/test_write_tools.py`, 33 tests.

### Run commands to prove its own work

`run_command` executes an **argv allowlist**, never a shell:

```
cat  echo  git  ls  pytest  python  python3
```

- `shell=False`, explicitly — a semicolon inside an argument is data
- no network programs at all: `curl`, `wget`, `nc`, `ssh` are absent by construction
- `git` write subcommands (`commit`, `push`, `reset`, …) refused
- `cwd` jailed to the workspace; timeout capped at 120 s; output capped at 6000 chars

*Verified:* `tests/test_exec_tools.py`, 21 tests, most of them adversarial.

### Return to a known state

`git_checkpoint` records the tree as a tag without touching the branch or history.
`git_rollback` restores tracked files. Untracked files are deliberately left alone.

*Verified:* `tests/test_git_tools.py` and the `fix-and-verify` eval.

### Ask for approval that actually means something

An approval is bound to a **12-hex fingerprint** of the exact payload and recorded with
the **operator** identity (`CLARAPANE_OPERATOR_ID`, else the OS login, never empty).

`proceed`, `y`, `yes`, `ok`, `sure`, `lgtm` and an empty answer are **rejected** — they do
not bind consent to content. `git_checkpoint` is ungated (a safety net must not need
permission); `apply_edit`, `write_file`, `run_command` and `git_rollback` are gated.

*Verified:* `tests/test_write_tools.py` and a guard in `test_repo_hygiene.py`.

### Refuse to claim unproven success

A `finish` claim is valid only when the run could not mutate, or a mutating tool actually
ran and succeeded. Otherwise: `finished=False`, `reason="finish_unverified"` — even when
the model's summary says otherwise.

*Verified:* four regression tests, including the live defect where a denied write was
reported as success.

### Resist prompt injection

Tool output is marked `trust: "untrusted"`. Content combining a directive aimed at a
model **and** an actionable payload (URL or shell command) aborts the run and is recorded
as `injection_blocked` — before the model ever sees it.

*Verified:* `tests/test_safety.py`, 11 tests; the `injection-blocked` eval case.

### Stay inside the workspace

Every path resolves through `Workspace.resolve()`, with separate read and write
boundaries. `.env`, `.venv` and `.git` are unreadable; `src/clarapane` is unwritable.

*Verified:* `tests/test_policy.py`.

### Produce a complete, validated trace

Twelve declared events, validated on write. One writer. `seq` gives a total order
independent of clock skew.

*Verified:* `tests/test_events_schema.py`, and an audit check that no second writer exists.

### Report how predictable it was

`divergence` records every failed attempt and every guess, in a 10-class closed taxonomy
with an owner and a remedy. `make divergences --strict` fails on **harness** divergence.

*Verified:* `tests/test_divergence.py`, 12 tests.

### Measure itself

Six deterministic eval cases against a fresh fixture copy per case. Isolated workspaces,
pinned model, git SHA recorded, all-or-nothing pass.

*Verified:* `tests/test_evals.py`, 11 tests, including a negative control proving the suite
can fail.

---

## Cannot

Stated plainly, because an unstated limitation is an overclaim.

| Cannot | Why | Consequence |
|---|---|---|
| Reach the network | No network program is allowlisted | An injected URL has nothing to resolve it — this is the point |
| Install packages | No `pip`/`uv` in the allowlist | Cannot fix a missing dependency itself |
| Run arbitrary binaries | Allowlist of seven programs | A task needing `make`, `docker`, `node` must be done by a human |
| Edit its own source | `src/clarapane` is in the write deny-list | Cannot improve itself; deliberate |
| Commit or push | `git` write subcommands refused | History stays a human decision |
| Escape the workspace | Path jail on every access | — |
| Be trusted in a shared environment | No container isolation | The allowlist is defence in depth, not a sandbox |
| Prove general capability | Evals use one 27-line fixture | 6/6 is a smoke test, not a benchmark |
| Compare to LangChain/LangGraph | That step has not started | No evidence either way yet |

### Two limits worth stating twice

**The exec allowlist is not a sandbox.** `python3` is allowlisted and `python3` can do
anything. The allowlist removes *accidental* and *injected* command execution; it does
not survive a determined adversary with a shell-capable interpreter. A container is the
only real answer, and it is not built.

**Injection detection is pattern-based.** It requires two independent signals, so it does
not fire on ordinary prose, but a novel phrasing will get through. The durable protection
is capability: a run under the read-only profile has nothing to exploit.

---

## Current measured state

| Measure | Value | Attributable to |
|---|---|---|
| Tests | **220**, offline, no network | — |
| Evals | **6/6** | sha `7aa9e5f`, `deepseek-v4.1-flash:cloud` |
| Divergence | 2 across 15 runs | 1 policy, 1 model, **0 harness** |
| Audit | 14 checks, 0 defects | — |

### What the eval cases prove

| Case | Proves | Steps |
|---|---|---|
| `locate-defect` | Finds and explains code using tools only | 3 |
| `fix-defect` | Locate → propose → apply → fixture test passes | 9 |
| `fix-and-verify` | **The agent runs `pytest` itself and shows it passing** | 12 |
| `write-denied` | A denial is reported as failure, not success | 6 |
| `injection-blocked` | Hostile content stops before the model sees it | 1 |
| `unknown-tool-recovery` | Admits it cannot, without false claims | 4 |

---

## How to check this document

Every claim above is meant to be falsifiable:

```bash
make test            # 220 tests, including the audit
make audit           # 14 checks, 0 defects
make divergences     # predictability, by class and source
make eval            # live model; needs credentials
python scripts/check_docs.py   # does this document still match the code?
```

If a row in this document cannot be reproduced by one of those commands, the document is
wrong and should be corrected or deleted — a claim that cannot be verified is a defect.
