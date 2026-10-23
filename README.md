# Clarapane

A glass-box coding agent: **every action proposed, gated, and logged.**

Clarapane is a small, readable coding agent whose value is not only what it does but
whether its history explains *why*. It runs a task against one workspace, and every
tool call, model response, policy decision and approval lands in a single append-only
JSONL trace — so a claim of success can be checked instead of believed.

The display name is resolved at runtime (`clarapane.identity`) and can be changed
without touching code: `CLARAPANE_APP_NAME` or `--name`. It is cosmetic only.

---

## Why it exists

Most agents are opaque: they act, then report. That makes two failures invisible:

1. **Unproven success.** The agent says it fixed something it never changed.
2. **Unattributable history.** Nobody can reconstruct what was approved, by whom, or
   on what exact content.

Clarapane treats both as defects with executable checks, not as documentation
promises. The design rule throughout: *a claim that cannot be verified from the trace
is a defect, even when nothing is failing.*

---

## Quick start

Requires Python 3.11+.

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e .

make setup                # verify this machine from .env
cp .env.example .env      # or start here and fill in the values
```

`.env` is required and is **never committed**. Only `.env.example` is tracked, and it
contains key names with no values.

```bash
# Ask a question (read-only, cannot mutate)
PYTHONPATH=src .venv/bin/python -m clarapane.cli "Where is add defined?"

# Or use the short commands
make run TASK="Where is add defined?"
make test      # offline suite, includes the defect audit
make audit     # the defect audit alone
```

### Environment

Setting up a new machine, or migrating devices? See **[docs/SETUP.md](docs/SETUP.md)** —
it lists everything that is deliberately **not** in git: credentials, local git config,
and CI secrets.

| Key | Purpose |
|---|---|
| `PROVIDER_BASE_URL` | Any OpenAI-compatible endpoint (verified: `https://ollama.com/v1`) |
| `PROVIDER_API_KEY` | Provider key — **one** key, used for chat and model listing |
| `PROVIDER_MODEL` | Must name a **tool-capable** model; check with `make models` |
| `CLARAPANE_OPERATOR_ID` | Accountable human for approvals (defaults to OS login) |
| `CLARAPANE_APP_NAME` | Cosmetic display name (defaults to `Clarapane`) |

---

## How a run works

```
task ──> capability profile ──> agent loop ──> tools ──> EventLog (one writer)
                                     │                      │
                              propose/gate            run_start … run_end
```

**Capability profiles** decide what the model is even allowed to see. The set is
fixed for the whole run, because changing the tool block between turns invalidates
the provider's prompt cache.

| Profile | Tools | Can mutate? |
|---|---|---|
| `read_only` | `list_dir` `grep` `read_range` `find_symbol` `finish` | no |
| `edit` | + `propose_edit` `apply_edit` `write_file` | yes, gated |
| `exec` | + `run_command` `git_checkpoint` `git_rollback` | yes, gated |

`--profile` overrides inference. When omitted, the profile is guessed from the task
text and **the guess is recorded as a divergence** rather than left implicit.

**Propose before mutate.** `propose_edit` computes a diff and returns a
`proposal_id`; nothing is written. `apply_edit` consumes that id and writes. A
proposal is single-use, lives only for its run, and is bound to the file hash it was
diffed against — so a concurrent change is refused rather than clobbered.

**The gate is in the harness, not the tool.** A tool cannot decide whether it was
approved. An approval is bound to a fingerprint of the exact payload, so the trace
proves *what* was approved and *by whom*, not merely that something was. A bare
"yes" is rejected: it does not bind consent to content.

**Errors are data.** A tool failure returns `{"error": ...}` to the model so it can
recover. It does not crash the loop.

**Never report an unproven success.** A `finish` claim is valid only when the task was
read-only, or a mutating tool actually ran. When mutation was offered but unused, the
run reports `finished=False` with `reason="finish_unverified"` even if the model
claims success. A confident summary is not evidence.

---

## The trace

Every event goes through `clarapane.events.EventLog` — the only writer. That
restriction is what makes the trace trustworthy: if an action happened, it is there,
and there is exactly one place it could have been written.

Events are validated against a declared schema on every write, so an undeclared or
misspelled field is a hard error at the call site rather than a silent shape change.

```
run_start  model_call  tool_call  approval  finish  divergence
stall  run_end  provider_error  recovery_failed  injection_blocked
```

Each record carries `ts`, `run_id`, `seq` (a total order independent of clock skew),
and `event`.

### Divergence: how predictable was this run?

`divergence` records every failed attempt and every place the harness or model
**guessed**. Each has a closed-taxonomy class, an owner, and a remedy:

| Source | Meaning |
|---|---|
| `harness` | **our** misprediction — always a defect |
| `model` | the model's mistake — measured, not a bug |
| `policy` | a gate doing its job — usually a misconfigured case |

A new class cannot be invented ad hoc: an undeclared class is a `BLOCKER` in the
audit, so the count cannot rot into a misc bucket.

```bash
make divergences          # report by class and source
```

The goal is not zero divergence — a model will always guess wrong sometimes. The goal
is **zero harness divergence** and a falling model divergence.

---

## Measuring it, not asserting it

```bash
make eval          # live-model eval suite; needs network + a key
make divergences   # trace-derived predictability report
```

The eval suite runs deterministic cases against a fresh copy of `sandbox/` per case,
so order cannot change the score. Pass is all-or-nothing: averaging away a false
success would hide the exact defect the harness exists to find.

A case that never reached the model is reported **INVALID**, not scored — zero
failures from zero data must never read as a clean result.

Research results, including what was **rejected**, live in
[`docs/evidence/`](docs/evidence/).

---

## Safety boundaries

- **Path jail.** Every path resolves through `Workspace.resolve()`. The agent may
  read its own source but never write it. `.env`, `.venv` and `.git` are never
  readable; `src/clarapane`, `.git`, `.venv`, `.env` are never writable.
- **Least privilege by construction.** A profile's tool list is the only thing the
  model sees. Calling an unlisted tool fails cleanly instead of partially working.
- **Trust boundary.** Tool output is data, never instructions. Content that combines a
  directive aimed at a model with an actionable payload (a URL or shell command) stops
  the run and is recorded as `injection_blocked`. Honest limit: patterns are evadable;
  the real boundary is capability — a read-only run has no write, exec, or network.
- **Exec allowlist.** `run_command` uses an argv allowlist with `shell=False`
  explicitly, no network programs at all, a jailed `cwd`, a capped timeout, and a
  capped output size.

---

## Repository map

```
src/clarapane/
  sdk_agent.py      the loop — the only place conversation state mutates
  registry.py       tool name, schema, risk class, gate flag in one declaration
  profiles.py       which tools a run may see (fixed per run)
  policy.py         path jail and read/write boundaries
  proposals.py      propose-before-mutate, single-use, hash-bound
  gate.py           approval bound to a payload fingerprint and an operator
  events.py         the single log writer
  events_schema.py  declared event schemas, validated on write
  divergence.py     closed taxonomy of failures and guesses
  safety.py         trust boundary for workspace content
  tools/            atomic read, write, exec and git tools
evals/              deterministic eval suite
tests/              offline suite, includes the audit
scripts/            audit_defects.py, check_commits.py, check_pr.py, ...
```

---

## Rules for contributors

The binding rules are in [`AGENTS.md`](AGENTS.md). Read it before changing anything.

Summarised:

- **Fix blockers first.** Serious defects outrank feature work.
- **Never commit `.env`.**
- **Commits** follow Conventional Commits, with a self-sufficient body ending in a
  `Refs:` footer. One commit per decision or coherent capability.
- **Before committing:** `make test` and `python scripts/check_commits.py`.
- **Deferred work goes in [`backlog.json`](backlog.json)**, managed via
  `python -m clarapane.backlog`, never only into a conversation.

```bash
make test
git config core.hooksPath .githooks   # enable the local hooks (not automatic)
```

Hooks are **inert in a fresh clone** until that config is set; the test suite and the
audit are the controls that travel with the repository.

---

## Current state

Honest status, because this project treats an overclaim as a defect:

- **220 tests** pass offline; the eval suite scores **6/6** on the committed fixture
  with a pinned model (sha `7aa9e5f`, `deepseek-v4.1-flash:cloud`).
- **CI runs on every push and pull request** (`.github/workflows/ci.yml`). The offline
  job — tests, defect audit, documentation drift, commit messages, setup template —
  needs no credentials and is the gate that must always be available. A separate
  live-model job runs the eval suite and **fails loudly** when `PROVIDER_API_KEY` is
  missing rather than skipping, because a skipped eval is a green build that tested
  nothing.
- The agent **can verify its own edits**: `run_command`, `git_checkpoint` and
  `git_rollback` exist, and the `fix-and-verify` eval case shows it running `pytest`
  on its own change. Before those tools existed it could only *claim* success.
- The package is not published; it runs from the tree via `PYTHONPATH=src`.
- Evals use one 27-line fixture. That is a smoke test, not a benchmark — see the
  limits in [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md).
- An AOP "agent observability" ledger was researched and **mostly rejected**: it did
  not reduce wasted work. Only per-job attribution survived, and it is a read
  function, not a subsystem. See [`docs/evidence/aop-decision-2026-10-23.md`](docs/evidence/aop-decision-2026-10-23.md).
- **Frameworks are not compared yet.** LangChain and LangGraph are the next plan step,
  and they will be measured against this baseline rather than ported for their own sake.

Where to read next:

| Document | Answers |
|---|---|
| [`docs/INTENT.md`](docs/INTENT.md) | What this is for, and what "done" means |
| [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md) | What it can and cannot do, each claim tied to a test |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How it is built, and why each invariant exists |

## License

Not yet specified.
