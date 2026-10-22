"""Divergence ruleset: every failed attempt and every guess, in one closed taxonomy.

The problem this solves
-----------------------
An agent run produces two kinds of surprise:

  1. **Failures** -- a tool errored, a call was denied, a JSON arg was malformed.
  2. **Guesses** -- the harness *assumed* something and the assumption is invisible:
     the profile was inferred from keywords, a proposal was computed against content
     that later changed, a finish was coerced from prose.

Both were previously visible only by reading a 900-line trace, so "how predictable
is this run?" had no numeric answer. Without a number there is no way to tell
whether tightening a rule actually helped.

The rule that makes the number meaningful
-----------------------------------------
A count is only useful if the taxonomy is **closed** and each class is assigned an
owning remedy. An open-ended "misc" bucket lets the metric rot: it grows, nobody
acts, and the number stops meaning anything. So:

  * every class declares a `source` ("harness" | "model" | "policy"),
  * every class declares a `remedy` -- either "eliminate" (a rule can make it
    impossible) or "measure" (it is inherent and must be counted),
  * `harness` divergence is ALWAYS a defect: our code mispredicted.

The goal is not zero divergence -- a model will always guess wrong sometimes. The
goal is **zero harness divergence** and a *falling* model divergence, because that
is the part policy can actually remove.

Why it is emitted through EventLog
----------------------------------
Same reason as every other decision: one writer. A side-channel counter would
silently lose entries on a crash, and a metric that can lose entries will.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

class_ = str  # readability alias
Source = Literal["harness", "model", "policy"]
Remedy = Literal["eliminate", "measure"]


@dataclass(frozen=True)
class Rule:
    """One class of divergence, with an owner and a remedy."""

    class_: str
    source: Source
    remedy: Remedy
    why: str
    # The rule that predicts the expected behaviour, so a failure names its cause.
    rule: str


RULES: dict[str, Rule] = {
    # ---- harness: our predictions. Any of these is a defect in this repo. ------
    "profile_inferred": Rule(
        class_="profile_inferred",
        source="harness",
        remedy="eliminate",
        why=(
            "The capability profile was guessed from task keywords. A wrong guess "
            "either exposes tools the task must not have or hides the one it needs."
        ),
        rule="profile must be declared by the caller, never inferred from prose",
    ),
    "proposal_stale": Rule(
        class_="proposal_stale",
        source="harness",
        remedy="measure",
        why=(
            "A proposal was computed against content that changed before it applied. "
            "The compare-and-swap guard catches it, so correctness holds, but the "
            "agent's work between read and apply was wasted."
        ),
        rule="proposal before_hash must equal the file hash at apply time",
    ),
    "finish_recovered": Rule(
        class_="finish_recovered",
        source="harness",
        remedy="measure",
        why=(
            "The model answered in prose and the harness coerced a finish call. The "
            "protocol was not followed voluntarily; the claim is only trusted when a "
            "mutation actually happened."
        ),
        rule="a run ends by calling finish, not by falling silent",
    ),
    "approval_denied": Rule(
        class_="approval_denied",
        source="policy",
        remedy="eliminate",  # by configuring the gate correctly, not by loosening it
        why=(
            "A gated tool was denied. This is the policy working, but if it fires on a "
            "case that needs the write, the case is misconfigured and can only fail."
        ),
        rule="a case that expects a mutation must have its gate approve writes",
    ),
    "vacuous_case": Rule(
        class_="vacuous_case",
        source="harness",
        remedy="eliminate",
        why=(
            "A case claimed to test a behaviour it never exercised -- it passed without "
            "doing the thing, or failed without running at all."
        ),
        rule="a case must observe the behaviour it claims to test",
    ),
    "tool_unknown": Rule(
        class_="tool_unknown",
        source="model",
        remedy="measure",
        why=(
            "The model called a tool outside its profile. That is a capability request "
            "expressed as an error."
        ),
        rule="only profile tools are callable",
    ),
    "args_malformed": Rule(
        class_="args_malformed",
        source="model",
        remedy="measure",
        why="The model emitted tool arguments that were not a JSON object.",
        rule="tool arguments must parse as a JSON object",
    ),
    "tool_error": Rule(
        class_="tool_error",
        source="model",
        remedy="measure",
        why=(
            "The tool ran and rejected the request: wrong path, ambiguous old_str, "
            "missing file. Recoverable by design, but still a wrong prediction."
        ),
        rule="a tool call should address content that exists and is unambiguous",
    ),
    "stall": Rule(
        class_="stall",
        source="model",
        remedy="measure",
        why="The model stopped without calling a tool and recovery was unavailable.",
        rule="a run ends with finish, or it stalls",
    ),
    "step_cap": Rule(
        class_="step_cap",
        source="model",
        remedy="measure",
        why="The run exhausted its step budget without finishing.",
        rule="the task is completable within the step cap",
    ),
}

# Classes are closed: an unknown class is a programming error, not a new label.
KNOWN_CLASSES = frozenset(RULES)


class UnknownDivergence(ValueError):
    """A divergence was recorded outside the closed taxonomy."""


@dataclass(frozen=True)
class Divergence:
    step: int
    class_: str
    source: str
    expected: str
    observed: str
    rule: str
    defect: bool

    def as_event(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "class_": self.class_,
            "source": self.source,
            "expected": self.expected,
            "observed": self.observed,
            "rule": self.rule,
            "defect": self.defect,
        }


def record(step: int, class_: str, *, expected: str, observed: str) -> Divergence:
    """Build a divergence from the taxonomy. Raises on an undeclared class.

    Failing loudly is the point: a new class of surprise must be added to RULES with
    an owner and a remedy before it can be counted. That is what keeps the metric
    from silently absorbing a new failure mode.
    """
    rule = RULES.get(class_)
    if rule is None:
        raise UnknownDivergence(
            f"undeclared divergence class {class_!r}; declared: {sorted(KNOWN_CLASSES)}. "
            "Add it to RULES with a source and a remedy before recording it."
        )
    return Divergence(
        step=step, class_=class_, source=rule.source,
        expected=expected, observed=observed, rule=rule.rule,
        defect=(rule.source == "harness"),
    )


def summarise(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Count divergence from a trace, grouped by the axis that drives action."""
    rows = [e for e in events if e.get("event") == "divergence"]
    by_class: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for e in rows:
        by_class[e["class_"]] = by_class.get(e["class_"], 0) + 1
        by_source[e["source"]] = by_source.get(e["source"], 0) + 1
    return {
        "total": len(rows),
        "by_class": dict(sorted(by_class.items())),
        "by_source": dict(sorted(by_source.items())),
        # The only number that must reach zero. Model and policy divergence may
        # persist; harness divergence is a bug in this repository.
        "harness_defects": sum(
            1 for e in rows if e.get("source") == "harness"
        ),
    }


def eliminate_candidates() -> list[Rule]:
    """Classes a rule could remove entirely, i.e. where tightening is possible."""
    return [r for r in RULES.values() if r.remedy == "eliminate"]


# ------------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    """Report divergence over committed traces.

    Exists so the number is a command, not an opinion -- the same reason
    `scripts/audit_defects.py` is executable.
    """
    import argparse
    import json as _json
    from pathlib import Path

    ap = argparse.ArgumentParser(description="Summarise divergence over traces")
    ap.add_argument("--traces", default="runs", help="Directory of JSONL traces")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--strict", action="store_true",
                    help="Exit 1 if any harness divergence is found")
    args = ap.parse_args(argv)

    root = Path(args.traces)
    files = sorted(root.glob("*.jsonl"))
    if not files:
        print(f"no traces under {root} -- nothing to summarise")
        return 0

    all_events: list[dict[str, Any]] = []
    per_run: dict[str, list[dict[str, Any]]] = {}
    for path in files:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            rec = _json.loads(line)
            all_events.append(rec)
            per_run.setdefault(rec.get("run_id", "?"), []).append(rec)

    total = summarise(all_events)
    print(f"traces: {len(files)} file(s), {len(per_run)} run(s)")
    print(f"divergence: {total['total']}")
    for source, count in total["by_source"].items():
        print(f"  {source:8} {count}")
    print("by class:")
    for name, count in total["by_class"].items():
        rule = RULES.get(name)
        owner = rule.source if rule else "?"
        remedy = rule.remedy if rule else "?"
        print(f"  {name:20} {count:4}  source={owner:8} remedy={remedy}")

    noisy = sorted(
        ((len([e for e in evs if e.get("event") == "divergence"]), rid)
         for rid, evs in per_run.items()),
        reverse=True,
    )[:5]
    if noisy and noisy[0][0]:
        print("noisiest runs:")
        for count, rid in noisy:
            if count:
                print(f"  {rid}  {count}")

    if args.json_out:
        from pathlib import Path as _P

        _P(args.json_out).write_text(_json.dumps(total, indent=2) + "\n")

    if args.strict and total["harness_defects"]:
        print(f"FAIL: {total['harness_defects']} harness divergence(s) -- our own "
              "mispredictions are defects")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
