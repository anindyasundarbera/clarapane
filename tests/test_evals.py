"""Eval-harness tests: the harness itself must be trustworthy.

A suite that always passes is worse than no suite, because it manufactures confidence.
These tests verify the harness's own machinery: case integrity, workspace isolation,
and that a failing run is actually reported as a failure. They do NOT call the model,
so they run in the normal offline test suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.cases import build_cases  # noqa: E402
from evals.runner import _copy_fixture  # noqa: E402


@pytest.fixture(scope="module")
def cases():
    return build_cases(PROJECT_ROOT / "sandbox")


def test_cases_have_unique_ids(cases):
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), f"duplicate case ids: {ids}"


def test_every_case_declares_why_it_exists(cases):
    for case in cases:
        assert case.why, f"{case.id} does not say why it exists"
        assert case.checks, f"{case.id} has no checks"


def test_every_case_uses_a_real_profile(cases):
    from clarapane import profiles

    for case in cases:
        assert case.profile in profiles.PROFILES, (
            f"{case.id} uses profile {case.profile!r}, which does not exist"
        )


def test_edit_cases_expect_a_mutation(cases):
    """An edit case that never changes anything cannot have tested anything.

    The runner now enforces `expects_mutation` at run time. This asserts the case
    declarations that make that enforcement meaningful.
    """
    from clarapane import profiles

    mutating = [c for c in cases if c.expects_mutation]
    assert mutating, "no case declares expects_mutation, so no edit path is covered"
    for case in mutating:
        # Any profile that actually offers a mutating tool qualifies. Naming `edit`
        # alone would break the moment a wider profile (exec) gained a mutating case.
        tools = profiles.PROFILES[case.profile]
        offers_mutation = any(
            t in tools for t in ("propose_edit", "apply_edit", "write_file")
        )
        assert offers_mutation, (
            f"{case.id} expects a mutation but profile {case.profile!r} offers no "
            f"mutating tool: {tools}"
        )
        assert case.gate_writes, (
            f"{case.id} expects a mutation but its gate denies writes, "
            "so it can only fail"
        )


def test_gate_policy_is_declared_not_inferred_from_id():
    """Regression: the runner used `case.profile == 'edit' and case.id != 'write-denied'`.

    That special case silently granted writes to any newly added edit case, and
    denied them to any case whose id happened to contain the magic string.
    """
    runner_src = (PROJECT_ROOT / "evals" / "runner.py").read_text()
    assert 'case.id == "injection-blocked"' not in runner_src
    assert 'case.id != "write-denied"' not in runner_src
    assert "case.gate_writes" in runner_src


def test_missing_mutation_fails_an_edit_case():
    """Control for the vacuous-pass guard: no write must be a violation.

    A tuple is always returned when the case claims a mutation, so a passing run
    still records "the edit happened" as positive evidence rather than nothing.
    """
    from evals.runner import _apply_mutation_guard

    violation = _apply_mutation_guard(expects_mutation=True, events=[])
    assert violation is not None
    name, ok, detail = violation
    assert name == "expects_mutation" and not ok
    assert "NEVER attempted" in detail

    wrote = [{"event": "tool_call", "tool": "apply_edit", "ok": True}]
    name, ok, detail = _apply_mutation_guard(expects_mutation=True, events=wrote)
    assert ok, detail
    assert "succeeded" in detail

    assert _apply_mutation_guard(expects_mutation=False, events=[]) is None

    # A denied write is not a mutation: the case still tested nothing. The message
    # must point at the gate, not blame the model for never trying.
    denied = [{"event": "tool_call", "tool": "apply_edit", "ok": False}]
    name, ok, detail = _apply_mutation_guard(expects_mutation=True, events=denied)
    assert not ok, detail
    assert "attempted but none succeeded" in detail


def test_fixture_copies_are_isolated():
    """Each case must get its own workspace, or order would change the score."""
    first = _copy_fixture()
    second = _copy_fixture()
    assert first != second, "cases share a workspace"

    (first / "calculator" / "ops.py").write_text("# mutated\n")
    assert "mutated" not in (second / "calculator" / "ops.py").read_text()


def test_the_original_fixture_is_never_mutated():
    """Evals must not touch the committed fixture, only copies of it."""
    fixture_ops = PROJECT_ROOT / "sandbox" / "calculator" / "ops.py"
    before = fixture_ops.read_text()
    sandbox = _copy_fixture()
    (sandbox / "calculator" / "ops.py").write_text("# changed\n")
    assert fixture_ops.read_text() == before


def test_checks_read_from_the_per_case_workspace():
    """Regression: file checks once captured the fixture path at build time.

    That made fix-defect fail even when the agent had fixed the file, because the
    check inspected a directory the run never touched.
    """
    source = (PROJECT_ROOT / "evals" / "cases.py").read_text()
    assert 'ctx["workspace"] / "calculator" / "ops.py"' in source
    # A bare `workspace / "calculator"` outside the build function is the old bug.
    build_section = source.split("def build_cases", 1)[1]
    helpers = build_section.split("return [", 1)[0]
    assert 'workspace / "calculator"' not in helpers


def test_a_denied_write_is_reported_as_a_failure():
    """Negative control: prove the checks are not vacuous.

    Build a context that represents a denied run and assert the edit case's
    file_fixed check rejects it. This runs offline -- no model call.
    """
    from clarapane.sdk_agent import AgentResult
    from evals.cases import build_cases as build

    sandbox = _copy_fixture()  # clamping still present, i.e. unfixed
    case = next(c for c in build(PROJECT_ROOT / "sandbox") if c.id == "fix-defect")
    ctx = {
        "result": AgentResult(finished=False, summary=None, evidence=None,
                              reason="finish_unverified"),
        "events": [],
        "workspace": sandbox,
        "start_tools": ["read_range", "propose_edit", "apply_edit"],
        "thrown": None,
    }
    results = [(f.__name__, *f(ctx)) for f in case.checks]
    failed = [name for name, ok, _ in results if not ok]
    assert "file_fixed" in failed, "file_fixed passed on an unfixed workspace"
    assert "finished_true" in failed


def test_reports_are_attributable():
    """A report must name the model that ran, not the policy that pinned it.

    Regression: the runner wrote `"model": args.model or "pinned"`, so every report
    said the model was the literal string "pinned". That is unattributable: the
    score could not be reproduced from the record.
    """
    runner_src = (PROJECT_ROOT / "evals" / "runner.py").read_text()
    assert '"git_sha"' in runner_src
    assert "date" in runner_src
    assert 'args.model or "pinned"' not in runner_src, (
        "report would record the word 'pinned' as the model name"
    )
    assert 'model=start.get("model")' in runner_src, (
        "the model must be read from the run's own trace"
    )
