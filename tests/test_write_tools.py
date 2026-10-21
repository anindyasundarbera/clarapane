"""Mutation tests: propose-before-mutate, single-use ids, and approval gating.

The properties asserted here are the ones an interviewer will probe: can an edit be
applied without a proposal, twice, or onto a file that changed underneath it?
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clarapane.gate import PolicyGate
from clarapane.policy import PolicyError
from clarapane.proposals import ProposalError, ProposalStore
from clarapane.registry import ToolRegistry
from clarapane.tools.finish_tool import register_finish_tool
from clarapane.tools.read_tools import register_read_tools
from clarapane.tools.write_tools import register_write_tools


@pytest.fixture
def tools(workspace):
    registry = ToolRegistry()
    register_read_tools(registry, workspace)
    register_write_tools(registry, workspace, ProposalStore())
    register_finish_tool(registry)
    return registry


@pytest.fixture
def target(workspace):
    return workspace.root / "pkg" / "mod.py"


# --------------------------------------------------------------- propose_edit
def test_propose_returns_diff_and_writes_nothing(tools, target):
    before = target.read_text()
    out = tools.get("propose_edit").handler(
        path="pkg/mod.py", old_str="return x + 1", new_str="return x + 2"
    )

    assert out["proposal_id"].startswith("p_")
    assert "-    return x + 1" in out["diff"]
    assert "+    return x + 2" in out["diff"]
    assert target.read_text() == before, "propose_edit must not touch the file"


def test_propose_rejects_ambiguous_match(tools, target):
    target.write_text("a = 1\nb = 1\n")
    with pytest.raises(ProposalError, match="appears 2 times"):
        tools.get("propose_edit").handler(path="pkg/mod.py", old_str="= 1", new_str="= 2")


def test_propose_rejects_absent_text(tools):
    with pytest.raises(ProposalError, match="not found"):
        tools.get("propose_edit").handler(
            path="pkg/mod.py", old_str="this text does not exist", new_str="x"
        )


def test_propose_rejects_identical_strings(tools):
    with pytest.raises(ProposalError, match="identical"):
        tools.get("propose_edit").handler(
            path="pkg/mod.py", old_str="return x + 1", new_str="return x + 1"
        )


def test_propose_rejects_empty_old_str(tools):
    with pytest.raises(ProposalError, match="must not be empty"):
        tools.get("propose_edit").handler(path="pkg/mod.py", old_str="", new_str="x")


def test_propose_enforces_the_path_jail(tools):
    with pytest.raises(PolicyError, match="escapes workspace"):
        tools.get("propose_edit").handler(path="../outside.py", old_str="a", new_str="b")


# ----------------------------------------------------------------- apply_edit
def test_apply_writes_the_change(tools, target):
    proposal = tools.get("propose_edit").handler(
        path="pkg/mod.py", old_str="return x + 1", new_str="return x + 2"
    )
    out = tools.get("apply_edit").handler(proposal_id=proposal["proposal_id"])

    assert out["applied"] is True
    assert "return x + 2" in target.read_text()


def test_apply_requires_a_proposal_id(tools):
    with pytest.raises(ProposalError, match="unknown proposal id"):
        tools.get("apply_edit").handler(proposal_id="p_doesnotexist")


def test_a_proposal_cannot_be_applied_twice(tools, target):
    """Single use: a looping model must not be able to apply the same edit again."""
    proposal = tools.get("propose_edit").handler(
        path="pkg/mod.py", old_str="return x + 1", new_str="return x + 2"
    )
    pid = proposal["proposal_id"]
    tools.get("apply_edit").handler(proposal_id=pid)

    with pytest.raises(ProposalError, match="already applied"):
        tools.get("apply_edit").handler(proposal_id=pid)


def test_apply_refuses_when_the_file_changed_since_proposal(tools, target):
    """Compare-and-swap: never clobber concurrent work."""
    proposal = tools.get("propose_edit").handler(
        path="pkg/mod.py", old_str="return x + 1", new_str="return x + 2"
    )
    target.write_text(target.read_text() + "\n# concurrent change\n")

    with pytest.raises(ProposalError, match="changed after the proposal"):
        tools.get("apply_edit").handler(proposal_id=proposal["proposal_id"])


def test_apply_respects_the_write_jail(tools, workspace):
    """A proposal cannot be smuggled past the jail by constructing it directly."""
    store = ProposalStore()
    secret = workspace.root.parent / "outside.py"
    secret.write_text("x = 1\n")
    # Simulate a proposal whose recorded path escapes the jail.
    proposal = store.create(
        path=secret, relative_path="../outside.py", old_str="x = 1", new_str="x = 2", step=1
    )
    registry = ToolRegistry()
    register_write_tools(registry, workspace, store)

    with pytest.raises(PolicyError, match="escapes workspace"):
        registry.get("apply_edit").handler(proposal_id=proposal.id)


# ----------------------------------------------------------------- write_file
def test_write_file_creates_a_new_file(tools, workspace):
    out = tools.get("write_file").handler(path="pkg/new.py", content="y = 2\n")
    assert out["created"] is True
    assert (workspace.root / "pkg" / "new.py").read_text() == "y = 2\n"


def test_write_file_refuses_to_overwrite(tools):
    with pytest.raises(ProposalError, match="already exists"):
        tools.get("write_file").handler(path="pkg/mod.py", content="clobber")


def test_write_file_refuses_missing_parent(tools):
    with pytest.raises(ProposalError, match="parent directory"):
        tools.get("write_file").handler(path="nodir/x.py", content="x")


def test_write_file_cannot_write_agent_source(workspace):
    (workspace.root / "src" / "clarapane").mkdir(parents=True)
    registry = ToolRegistry()
    register_write_tools(registry, workspace, ProposalStore())

    with pytest.raises(PolicyError, match="write denied"):
        registry.get("write_file").handler(path="src/clarapane/agent.py", content="evil")


# ------------------------------------------------------------- risk and gating
def test_mutation_tools_declare_their_risk(tools):
    assert tools.get("propose_edit").risk == "read"   # proposing is not mutating
    assert tools.get("apply_edit").risk == "write"
    assert tools.get("write_file").risk == "write"
    assert tools.get("apply_edit").gate is True
    assert tools.get("write_file").gate is True
    assert tools.get("propose_edit").gate is False


# ---------------------------------------------------------- loop integration
def _agent(workspace, script, gate):
    from clarapane.events import EventLog
    from clarapane.sdk_agent import Agent

    registry = ToolRegistry()
    register_read_tools(registry, workspace)
    register_write_tools(registry, workspace, ProposalStore())
    register_finish_tool(registry)
    # A unique trace per test: a shared file lets one test's events be read by
    # another, which produced a genuinely confusing failure.
    import tempfile

    log = EventLog(Path(tempfile.mkdtemp()) / "trace.jsonl")
    return Agent(_FakeProvider(script), registry, workspace, log,
                 max_steps=6, verbose=False, gate=gate), log


from tests.test_agent_loop import FakeCall, FakeProvider as _FakeProvider  # noqa: E402

def _proposal_id_from(messages):
    """Extract the proposal id the loop just returned, from the tool message."""
    for msg in reversed(messages):
        if msg.get("role") != "tool":
            continue
        payload = json.loads(msg["content"])
        data = payload.get("data", payload)
        if isinstance(data, dict) and "proposal_id" in data:
            return data["proposal_id"]
    raise AssertionError("no proposal_id found in the conversation")


def _apply_call(messages):
    """A scripted turn that applies whatever proposal the previous turn created."""
    return {"tool_calls": [FakeCall("apply_edit", {"proposal_id": _proposal_id_from(messages)})]}




def test_denied_gate_returns_an_error_to_the_model(workspace):
    """A denial is data the model can react to, not a crash."""
    agent, log = _agent(
        workspace,
        [
            {"tool_calls": [FakeCall(
                "propose_edit",
                {"path": "pkg/mod.py", "old_str": "return x + 1", "new_str": "return x + 2"},
            )]},
            _apply_call,
            {"tool_calls": [FakeCall("finish", {"summary": "done", "evidence": "e"}, "call_fin")]},
        ],
        PolicyGate(allow_writes=False, reason="test"),
    )
    result = agent.run("change it")
    log.close()

    apply_step = next(s for s in result.steps if s.tool_name == "apply_edit")
    assert apply_step.tool_ok is False
    assert "denied" in (apply_step.error or "")


def test_approval_decision_is_logged_with_an_actor(workspace):
    agent, log = _agent(
        workspace,
        [
            {"tool_calls": [FakeCall(
                "propose_edit",
                {"path": "pkg/mod.py", "old_str": "return x + 1", "new_str": "return x + 2"},
            )]},
            _apply_call,
            {"tool_calls": [FakeCall("finish", {"summary": "done", "evidence": "e"}, "call_fin")]},
        ],
        PolicyGate(allow_writes=True, reason="test"),
    )
    agent.run("change it")
    log.close()

    records = [json.loads(x) for x in log.path.read_text().splitlines() if x.strip()]
    approval = next(r for r in records if r["event"] == "approval")
    assert approval["actor"] == "policy"
    assert approval["approved"] is True
    assert "test" in approval["reason"]


def test_gated_tool_without_a_gate_fails_closed(workspace):
    """Forgetting to configure a gate must deny, never silently allow."""
    agent, log = _agent(workspace, [
        {"tool_calls": [FakeCall("propose_edit", {
            "path": "pkg/mod.py", "old_str": "return x + 1", "new_str": "return x + 2"})]},
        _apply_call,
        {"tool_calls": [FakeCall("finish", {"summary": "s", "evidence": "e"}, "c3")]},
    ], gate=None)
    result = agent.run("change it")
    log.close()

    apply_step = next(s for s in result.steps if s.tool_name == "apply_edit")
    assert apply_step.tool_ok is False
    assert "no gate" in (apply_step.error or "")


def test_approved_write_makes_the_finish_verified(workspace):
    """A real mutation upgrades a recovered finish from unverified to verified."""
    (workspace.root / "pkg" / "mod.py").write_text("def alpha(x):\n    return x + 1\n")
    agent, log = _agent(workspace, [
        {"tool_calls": [FakeCall("propose_edit", {
            "path": "pkg/mod.py", "old_str": "return x + 1", "new_str": "return x + 2"})]},
        _apply_call,
        {"content": "changed it", "finish_reason": "stop"},
        {"tool_calls": [FakeCall("finish", {"summary": "changed", "evidence": "diff"}, "c4")]},
    ], PolicyGate(allow_writes=True, reason="test"))
    result = agent.run("change it")
    log.close()

    assert result.finished is True
    assert result.reason == "finish_recovered"


# ------------------------------------------------- verification on ALL exits
def test_denied_write_never_reports_success(workspace):
    """Regression: the normal finish path once reported success after a denied write.

    The agent prepared a correct patch, was denied by policy, said "NOT APPLIED" in
    its summary, and the loop still returned finished=True. Verification was applied
    only to recovered finishes, so the ordinary exit was unguarded.
    """
    agent, log = _agent(
        workspace,
        [
            {"tool_calls": [FakeCall("propose_edit", {
                "path": "pkg/mod.py", "old_str": "return x + 1", "new_str": "return x + 2"})]},
            _apply_call,
            {"tool_calls": [FakeCall("finish", {
                "summary": "NOT APPLIED -- denied by policy", "evidence": "error above"})]},
        ],
        PolicyGate(allow_writes=False, reason="test"),
    )
    result = agent.run("change it")
    log.close()

    assert result.finished is False, "a denied write must not be reported as success"
    assert result.reason == "finish_unverified"


def test_read_only_run_finishing_is_verified(workspace):
    """The mirror case: with no mutating tool offered, a finish is legitimate."""
    from clarapane.events import EventLog
    from clarapane.sdk_agent import Agent

    registry = ToolRegistry()
    register_read_tools(registry, workspace)
    register_finish_tool(registry)

    import tempfile

    log = EventLog(Path(tempfile.mkdtemp()) / "t.jsonl")
    agent = Agent(
        _FakeProvider([{"tool_calls": [FakeCall(
            "finish", {"summary": "it is at line 1", "evidence": "pkg/mod.py:1"})]}]),
        registry, workspace, log, max_steps=2, verbose=False,
        gate=PolicyGate(allow_writes=False),
    )
    result = agent.run("where is alpha?")
    log.close()

    assert result.finished is True
    assert result.reason == "finish"


def test_finish_event_records_verified_on_the_normal_path(workspace):
    """Both exit paths must emit the same finish shape, so a reader can compare."""
    agent, log = _agent(
        workspace,
        [
            {"tool_calls": [FakeCall("propose_edit", {
                "path": "pkg/mod.py", "old_str": "return x + 1", "new_str": "return x + 2"})]},
            _apply_call,
            {"tool_calls": [FakeCall("finish", {"summary": "s", "evidence": "e"})]},
        ],
        PolicyGate(allow_writes=True, reason="test"),
    )
    agent.run("change it")
    log.close()

    records = [json.loads(x) for x in log.path.read_text().splitlines() if x.strip()]
    finish = next(r for r in records if r["event"] == "finish")
    assert finish["recovered"] is False
    assert finish["verified"] is True


def test_approved_edit_actually_writes_the_file(workspace):
    """End to end: approval must result in a real file change on disk."""
    target = workspace.root / "pkg" / "mod.py"
    before = target.read_text()

    agent, log = _agent(
        workspace,
        [
            {"tool_calls": [FakeCall("propose_edit", {
                "path": "pkg/mod.py", "old_str": "return x + 1", "new_str": "return x + 42"})]},
            _apply_call,
            {"tool_calls": [FakeCall("finish", {"summary": "done", "evidence": "diff"})]},
        ],
        PolicyGate(allow_writes=True, reason="test"),
    )
    result = agent.run("change it")
    log.close()

    assert result.finished is True
    assert target.read_text() != before
    assert "return x + 42" in target.read_text()
