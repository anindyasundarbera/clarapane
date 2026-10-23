# AOP Research: Decision Report

Date: 2026-10-23
Model: `deepseek-v4.1-flash:cloud` (pinned from `.env`; named per run in every report)
Harness: `research/aop/` in worktree `.worktrees/aop-research`, branch `research/aop`
Baseline commit: `7aa9e5f`

All decision rules below were written **before** their runs. Two were amended
before execution where a probe showed the original criterion was untestable; both
amendments are recorded with their evidence.

---

## Verdict in one paragraph

**The coordination claim is rejected; the attribution claim is supported.** A shared
ledger did *not* reduce wasted work — it slightly increased stale rejections (5 → 8)
because reading about other agents' intent did not help agents avoid collisions, and
the existing compare-and-swap guard already prevents the harm that mattered. But the
same ledger made **exact per-agent file attribution** possible at 100% precision and
recall, where `git status` gave every agent the same undifferentiated list of dirty
files. The protocol's value is **accountability, not coordination** — and it is worth
building only in that narrower form.

---

## What was tested

| Experiment | Question | Result |
|---|---|---|
| **Mandate gate** | Can the fixture produce the failure at all? | **PASS** — 1 real stale rejection from genuine concurrency |
| **E2** | Does a ledger reduce wasted work? | **REJECT** — stale rejections rose 5 → 8 |
| **E3** | Is declared intent trustworthy? | **PASS** — 0% contradicted across 8 runs |
| **E6** | Can a ledger attribute files to the right agent? | **PASS** — 100% precision, 100% recall |

E1 (AST/outline) and E5 (marginal cost) were **not run**: E2's rejection removed the
premise for spending on them. E4 (reasoning publication) was scoped off by decision.

---

## Two probes that changed the design before any code was written

**P1 — worktrees make the research subject disappear.** Two worktrees edited the same
relative path and produced three distinct inodes; the files cannot collide. The plan
proposed one worktree per simulated agent as the isolation for a *concurrency*
experiment. Had it been built that way it would have reported "zero conflicts" — a
null result measuring the experiment's own setup. Corrected to **one shared
workspace**, which is also the real case AOP is for.

**P2 — the compare-and-swap guard already prevents lost updates.** Two proposal
stores on the same file: B's stale apply was refused with
`ops.py changed after the proposal was created`. Silent data loss is **already
impossible**. This killed the strongest AOP claim before it was tested: AOP cannot be
sold as conflict prevention, because that guarantee already exists and works.

---

## Mandate gate: PASS

Four real agents, one shared workspace, no ledger, 4 disjoint-region tasks where one
task deliberately overlapped another.

```
add-bug    steps= 7 finished=True  verified=True  stale=0
div-bug    steps= 9 finished=True  verified=True  stale=0
mod-bug    steps= 9 finished=True  verified=True  stale=0
add-bug-2  steps= 8 finished=False verified=False stale=1
```

The fixture **can** express the failure. Research had a subject.

**The first attempt at this gate returned a false negative.** All four agents crashed
on a missing provider key (a fresh worktree has no `.env`), and the gate printed
"NOT MANDATED: 0 stale rejections" — converting an infrastructure failure into a
scientific conclusion. Fixed by distinguishing "the failure did not happen" from "the
experiment did not run"; the gate now returns `INVALID` (exit 2) when no agent
completes a model call. That guard was then back-ported to `evals/runner.py`, which
had the same flaw.

---

## E2: REJECT — the ledger did not reduce wasted work

3 rounds per arm, 4 concurrent agents per round, identical model/sha/step cap/tasks.
The only difference was whether `aop_query` was registered.

| Metric | Control | Ledger |
|---|---|---|
| **Stale rejections** (primary) | **5** (mean 1.67) | **8** (mean 2.67) |
| Duplicate-region edits | 0 | 0 |
| Mean steps per round | 41.67 | 32.33 |
| Agents finishing | 9/12 | 8/12 |

**Rule applied verbatim:** keep only if stale rejections fall **and** duplicates drop
≥50% **and** mean steps do not rise by more than 1.

| Criterion | Result |
|---|---|
| Stale rejections lower | ✗ (5 → 8, worse) |
| Duplicates dropped ≥50% | **UNTESTABLE** (0 → 0; a ratio over zero cannot fail) |
| Steps not up by >1 | ✓ (−9.34) |

**VERDICT: REJECT.**

Two honest notes:

1. **A vacuous criterion was caught in my own rule.** `0 -> 0` initially evaluated as
   "50% drop satisfied". It is not a drop; it is a metric that never occurred. The
   rule now reports UNTESTABLE and cannot contribute to a keep decision.
2. **Fewer steps did not mean less wasted work.** The ledger arm used fewer steps but
   suffered *more* stale rejections. Efficiency and waste moved in opposite
   directions, which is exactly why step count alone is a misleading metric.

---

## E3: PASS — declared intent is honest

8 agent runs, sequential (concurrency would confound a stale rejection with a lie).

| Outcome | Count |
|---|---|
| match | 6 (75%) |
| partial | 0 |
| **unfulfilled** (changed nothing, reported failure) | 2 (25%) |
| **contradicted** | **0 (0%)** |

**Rule:** declared namespace ships unless contradicted > 20%. **0% → TRUSTWORTHY.**

**The first version of this experiment reported 25% contradicted and would have
condemned the namespace.** Inspecting the two cases showed both agents declared
`calc/ops.py`, changed nothing, and honestly reported `finished=False`. That is
*failure*, already visible in the trace — not a misleading claim. The classifier was
conflating **"did not do it"** with **"lied about it"**. A metric that mislabels
honest failure as dishonesty cannot support a decision, so a fourth outcome
(`unfulfilled`) was added. Both the wrong and corrected classifier are in
`e3_intent.py` history.

---

## E6: PASS — exact per-agent attribution

This is the claim the user raised, and it is the **strongest positive result**.

Deterministic (no model): a real git repo where two agents modified disjoint files.

```
git status reports 4 dirty files:
  ['.aop-ledger/', 'calc/ops.py', 'calc/util.py', 'docs/notes.md']

baseline (git status only):
  alpha  would commit 4 files  <- including beta's 2 files AND the ledger itself
  beta   would commit 4 files

treatment (ledger observed namespace):
  alpha  attributed=['calc/ops.py']                      precision=100% recall=100%
  beta   attributed=['calc/util.py','docs/notes.md']     precision=100% recall=100%
```

**Rule:** useful only if precision == 1.0 and recall == 1.0 for every agent. **PASS.**

The baseline is not merely imprecise — it is actively dangerous: every agent sees
every dirty file, so a commit scoped to "my changes" sweeps foreign work into an
attributed commit. This happened for real during this session: work was left
uncommitted because there was no reliable way to separate two agents' modifications
in one tree.

**Honest limit, recorded with the result:** the ledger knows what an agent **did**,
because the harness recorded it. It does not know what an agent **should commit** —
commit grouping, and "is this file part of my task", remain judgement calls.

---

## What survives, and in what form

| Claim | Status |
|---|---|
| AOP prevents conflicting or lost edits | **FALSE** — already guaranteed by CAS |
| AOP reduces wasted work | **REJECTED** — measured worse |
| AOP makes declared intent available and honest | **SUPPORTED** — 0% contradicted |
| AOP attributes changes to the agent that made them | **SUPPORTED** — 100%/100% |

The protocol should be built as an **attribution and accountability ledger**, not a
coordination layer. The `observed` namespace does all the useful work. This is
narrower than the original Agent Observability Protocol idea — and it is the only
version the evidence supports.

## Recommendation

1. **Do not build** `aop_query` as a coordination tool for the agent's default
   profile. E2 rejected it.
2. **Do build** the `observed` ledger as harness infrastructure: it answers "which
   files belong to this job?" exactly, which `git status` cannot.
3. **Keep the `declared` namespace** — it passed E3 — but it is a description, never
   an authority.
4. **Do not publish reasoning traces.** Never tested, because E2 removed the premise
   and the cross-agent injection surface is a real cost with no demonstrated benefit.
5. **Re-run E2 on a harder fixture before revisiting coordination.** The current
   fixture produced only 5–8 stale rejections per arm; that is a small effect size
   and a larger, more contended fixture could change the answer.

## Threats to validity

- **N=3 rounds per E2 arm**, 4 agents each — enough for a large effect, not a subtle
  one. The stale-rejection means (1.67 vs 2.67) are within noise of each other; the
  correct reading is "no improvement detected", not "proven worse".
- **One model, one provider.** Results may not transfer.
- **The model may simply ignore `aop_query`.** Tool usage was not asserted per turn,
  so E2 cannot distinguish "adopted and useless" from "never used". This is a real
  gap and is why E2's rejection is reported as *no benefit detected*.
- **Synthetic concurrency.** Four simulated agents on one machine are more orderly
  than real ones; the measured benefit is an upper bound.
- **E6 is deterministic by design**, so it tests whether the ledger *carries* the
  information, not whether a model *uses* it well. That second question (call it E7)
  is untested and should be run before relying on agent-driven commit scoping.
