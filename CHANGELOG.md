# Changelog

All notable changes to this project. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Version source of truth:** the `version` field in [`pyproject.toml`](pyproject.toml).
`clarapane.identity.version()` reads it and never duplicates it, so the CLI banner
cannot drift from the package metadata. A test enforces that.

**Tag policy:** tag on a version bump, not on every merge. A tag marks a state someone
might depend on; tagging every merge makes "docs at version X" meaningless because X
changes constantly. Until 0.1.0 is tagged, no release exists and nothing should be
consumed as one.

## [Unreleased]

### Added
- `docs/INTENT.md` — what the project is for, what "done" looks like, and what is
  explicitly out of scope. The reference point for judging whether a change belongs.
- `docs/CAPABILITIES.md` — what the agent can and cannot do, every claim tied to a
  named module and test.
- `docs/ARCHITECTURE.md` — the eight invariants, the loop mechanics, the trace, and the
  deliberate non-choices with the measurements that rejected them.
- `tests/test_git_tools.py` — checkpoint and rollback were untested; the capability
  document cited a test file that did not exist, and the new docs checker caught it.
- Four documentation checks: capability tools exist, capability citations resolve,
  CI claims match reality, and INTENT states its goal and non-goals.

### Fixed
- README claimed "No CI exists yet" after CI was added — a stale claim about the safety
  net, which is the kind that makes people stop trusting the net.
- README said the agent cannot verify its own edits; `run_command` and the
  `fix-and-verify` case now show it doing exactly that.
- A link in README pointed at `aopa-decision-...md`; the file is `aop-decision-...md`.

### Added
- `README.md` — the entry point this repository did not have. Every checkable claim in
  it is verified against the code by a script rather than trusted.
- `clarapane.identity.version()` — single-source version resolution that works from a
  `PYTHONPATH=src` checkout, where `importlib.metadata` raises.
- `clarapane.divergence` — a closed taxonomy of every failed attempt and every guess,
  each with an owner (`harness` / `model` / `policy`) and a remedy
  (`eliminate` / `measure`). `make divergences` reports it; `--strict` fails on
  harness divergence only.
- `divergence` event in the trace schema, validated on write like every other event.
- Audit check `divergence-taxonomy`: an undeclared class, or a source that disagrees
  with the ruleset, is a `BLOCKER`.
- Eval case `fix-and-verify`: the agent must run `pytest` itself and show it passing.
- Exec tools: `run_command` (argv allowlist, `shell=False`, no network programs,
  jailed cwd, capped timeout and output) and `git_checkpoint` / `git_rollback`.
- `exec` capability profile, naming only tools that exist and are tested.
- Per-case `gate_writes` and `plant_injection` declarations, replacing inference from
  `case.id`.
- `make eval`, `make divergences`.

### Changed
- `evals/runner.py` records the **model that actually ran**, read from each run's
  `run_start`, instead of the literal string `"pinned"`.
- Eval traces are retained per case and linked from the report, so a failure cites its
  own evidence.
- A case that never reached the model is reported **INVALID** (and fails the run)
  instead of being scored.

### Fixed
- **Unattributable eval reports.** Every report, including the committed baseline,
  recorded the model as the word `"pinned"`.
- **Gate policy inferred from `case.id`.** Silently granted writes to any new edit
  case. This made the freshly added `fix-and-verify` case unpassable by construction:
  its `exec` profile evaluated to `allow_writes=False`, so it could never apply its
  own fix.
- **`expects_mutation` declared but never enforced.** An edit case that never wrote
  still passed, because every check held with the file untouched.
- **Banner version hardcoded.** `identity.banner()` defaulted to `version="0.1.0"`, so
  a version bump would leave the CLI reporting a stale number.

### Research
- **AOP (agent observability) — mostly rejected.** A shared intent ledger did **not**
  reduce wasted work: stale proposal rejections measured 5 (control) vs 8 (ledger).
  The strongest version of the claim was dropped before testing, because a direct
  probe showed the existing proposal compare-and-swap already refuses a stale apply —
  lost updates were never possible.
- Only **per-job attribution** survived: in a shared tree `git status` gives every
  agent the same dirty files, while the trace's recorded `path`/`after_hash`
  attributed them at 100% precision and recall. Re-scoped in the backlog to a read
  function, not a subsystem.
- Three **vacuous-result defects** found and fixed: a gate that read total
  infrastructure failure as "not mandated"; a criterion scoring `0 → 0` as a 50% drop;
  and a classifier counting honest failure as dishonesty.

## [0.1.0] — unreleased

Foundation: agent loop, atomic read tools, propose/apply split with single-use
proposal ids, approval bound to a payload fingerprint and an operator identity, path
jail with separate read and write boundaries, JSONL trace with a declared schema,
prompt-injection trust boundary, backlog CLI, defect audit, commit-format checks, and
a deterministic eval suite.
