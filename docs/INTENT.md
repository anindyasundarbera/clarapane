# Intent

**What this project is for, what it must become, and what it is deliberately not.**

Read this before proposing work. If a change does not serve the goal below, it belongs
in [`backlog.json`](../backlog.json), not in this session.

---

## The goal

**Turn Clarapane into a workable system that codes autonomously under an atomic policy,
so that writing and maintaining software costs less human effort.**

Three words carry the weight:

| Word | What it forbids |
|---|---|
| **workable** | A demo that only runs on the author's machine, or a design that only works on a 27-line fixture |
| **autonomously** | A tool that needs a human to babysit each step. Approval is for *irreversible* actions, not for every action |
| **atomic policy** | A blanket "trust the model". Each action is separately proposed, risk-classed, gated, and logged |

The measure of progress is not how much is built. It is **how much human effort a task
takes**. That is why the eval suite and the divergence report are the primary artefacts:
they are the only two things that can say whether effort went down.

---

## The problem it solves

Two failures make agents untrustworthy, and both are invisible without instruments:

1. **Unproven success.** The agent says it fixed something it never changed. The
   confident summary is indistinguishable from a real result.
2. **Unattributable history.** Nobody can reconstruct what was approved, by whom, or on
   what exact content.

Every design decision in this repository traces back to one of those two. When a
decision does not, it is probably scope creep.

---

## The design rule

> **A claim that cannot be verified from the repository is a defect, even when nothing
> is failing.**

This rule is load-bearing, and it applies to the project as much as to the agent:

- A test that cannot fail is a defect (this repository has found four).
- A metric that cannot see its data is a defect (found once, in the divergence report).
- A profile advertising a tool that does not exist is a defect (found once, `exec`).
- A README sentence claiming "no CI exists" after CI was added is a defect.

---

## Where the project is

Plan tracked in [`plan.json`](../plan.json); validated by `make audit`.

| Step | Status | What it had to produce |
|---|---|---|
| Foundation | done | A loop whose behaviour is observable and attributable |
| Evals | done | A score instead of an opinion |
| **Execution** | **in progress** | The agent must prove its own edits, not have the harness prove them for it |
| Framework comparison | pending | Evidence about what LangChain and LangGraph hide and cost, against a scored baseline |

`execution` is nearly closed: `run_command` behind an allowlist exists, and the
`fix-and-verify` case shows the agent running `pytest` on its own work. What is missing
is not capability but **evidence that it generalises past the fixture**.

---

## What "done" looks like

Not a feature list. Four conditions:

1. **A score that means something.** Evals on tasks the agent did not grow up on — a
   repository it has not seen, a task with no seeded bug, a change spanning more than
   one file. A 6/6 on a 27-line fixture is a smoke test.
2. **Zero harness divergence** across those runs. Our own mispredictions are defects;
   model mistakes are measured, not fixed.
3. **A human can stop reading every run.** The trace answers "what happened, who
   approved it, and what proves it" without opening the diff.
4. **A task that used to take an hour takes minutes**, measured, on a real repository.

Until condition 1 holds, adding capability is guessing.

---

## Explicitly not the goal

Recorded so future work has somewhere to be declined rather than debated:

- A web UI, trace viewer, or dashboard. The JSONL trace and `make divergences` are the
  interface.
- Multi-agent orchestration. One agent with a closed policy is not yet proven; several
  would multiply an unproven thing.
- Packaging or publishing to PyPI. It runs from the tree.
- Deployment. There is no service.
- A container sandbox. The exec allowlist is defence in depth, not isolation, and that
  limitation is documented rather than hidden — but building isolation is a separate
  project, justified only when the agent runs untrusted tasks.
- Any new guardrail that no failing eval or real defect demanded.

---

## History worth keeping

This began as interview preparation: build an agent from scratch to relearn agentic
AI. The scope lock in [`AGENTS.md`](../AGENTS.md) §0a exists because that plan derailed —
a three-hour session ran five hours and never reached the planned framework step,
because each turn surfaced a real defect and each defect was fixed immediately.

The lesson was not "stop fixing defects". It was **fix the defect or build the plan,
and never confuse the two**. That distinction is now enforced by `plan.json` and the
`plan-integrity` audit check.

The purpose has since changed from learning to building. The discipline has not.
