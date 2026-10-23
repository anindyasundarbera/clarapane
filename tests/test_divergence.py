"""Tests for the divergence ruleset.

The ruleset exists so "how many things did not go as predicted?" is a number read
from a trace, not an impression. These tests protect the two properties that make
the number trustworthy:

  1. the taxonomy is CLOSED -- an undeclared class is an error, not a new bucket;
  2. every class has an owner and a remedy, so the number drives an action.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from clarapane import divergence  # noqa: E402
from clarapane.events import EventLog  # noqa: E402
from clarapane.events_schema import SCHEMAS  # noqa: E402


def test_taxonomy_is_registered_as_an_event():
    """The schema and the ruleset must agree, or a documented class is unwritable."""
    assert "divergence" in SCHEMAS


def test_every_rule_has_an_owner_and_a_remedy():
    for name, rule in divergence.RULES.items():
        assert rule.class_ == name, f"{name}: class_ field disagrees with its key"
        assert rule.source in ("harness", "model", "policy"), f"{name}: bad source"
        assert rule.remedy in ("eliminate", "measure"), f"{name}: bad remedy"
        assert rule.why, f"{name}: does not say why it matters"
        assert rule.rule, f"{name}: does not name the rule that predicts behaviour"


def test_unknown_class_is_rejected():
    """An open taxonomy rots into a misc bucket, so an unknown class must fail."""
    with pytest.raises(divergence.UnknownDivergence):
        divergence.record(1, "something_new", expected="a", observed="b")


def test_harness_divergence_is_always_a_defect():
    """Our own mispredictions are bugs; model and policy divergence are facts."""
    for name, rule in divergence.RULES.items():
        got = divergence.record(1, name, expected="a", observed="b")
        assert got.defect == (rule.source == "harness"), (
            f"{name}: defect flag does not follow source"
        )


def test_eliminate_candidates_are_the_actionable_ones():
    """At least one class must be removable, or tightening has nothing to target."""
    candidates = divergence.eliminate_candidates()
    assert candidates, "no class is marked eliminate: rules cannot reduce divergence"
    assert any(r.source == "harness" for r in candidates)


def test_summarise_counts_by_source_and_class():
    rows = [
        {"event": "divergence", "class_": "stall", "source": "model"},
        {"event": "divergence", "class_": "stall", "source": "model"},
        {"event": "divergence", "class_": "proposal_stale", "source": "harness"},
        {"event": "tool_call", "tool": "read_range"},
    ]
    got = divergence.summarise(rows)
    assert got["total"] == 3
    assert got["by_class"] == {"proposal_stale": 1, "stall": 2}
    assert got["by_source"] == {"harness": 1, "model": 2}
    # The only number that must reach zero.
    assert got["harness_defects"] == 1


def test_summarise_of_a_clean_session_is_zero():
    assert divergence.summarise([])["total"] == 0
    assert divergence.summarise([])["harness_defects"] == 0


def _fake_agent(tmp_path, script):
    """A real Agent against a scripted provider. No network."""
    from clarapane.policy import Workspace
    from clarapane.provider import Completion
    from clarapane.registry import ToolRegistry
    from clarapane.sdk_agent import Agent
    from clarapane.tools.finish_tool import register_finish_tool
    from clarapane.tools.read_tools import register_read_tools

    class FakeProvider:
        default_model = "fake"

        def __init__(self, items):
            self.items = list(items)

        def complete(self, messages, *, model=None, tools=None, **kw):
            return self.items.pop(0)

    class Call:
        def __init__(self, name, args, call_id="c1"):
            self.id = call_id
            self.function = type("F", (), {
                "name": name, "arguments": json.dumps(args) if not isinstance(args, str) else args
            })()

    root = tmp_path / "ws"
    root.mkdir()
    (root / "f.py").write_text("x = 1\n")

    registry = ToolRegistry()
    workspace = Workspace(root=root)
    register_read_tools(registry, workspace)
    register_finish_tool(registry)

    trace = tmp_path / "trace.jsonl"
    log = EventLog(trace)
    agent = Agent(FakeProvider(script), registry, workspace, log,
                  model="fake", verbose=False)
    return agent, log, trace


def _completion(**kw):
    from clarapane.provider import Completion

    return Completion(
        content=kw.get("content"), tool_calls=kw.get("tool_calls", ()),
        reasoning=None, finish_reason=kw.get("finish_reason", "tool_calls"),
        prompt_tokens=1, completion_tokens=1, model="fake",
    )


def test_unknown_tool_is_recorded_without_crashing_the_loop(tmp_path):
    """A model calling a tool it was never offered must be a recorded divergence."""
    # _completion is already in this module's namespace.

    class Call:
        def __init__(self, name):
            self.id = "c1"
            self.function = type("F", (), {"name": name, "arguments": "{}"})()

    agent, log, trace = _fake_agent(
        tmp_path,
        [_completion(tool_calls=(Call("delete_everything"),)),
         _completion(content="I cannot do that.", finish_reason="stop")],
    )
    result = agent.run("delete everything")
    log.close()

    assert result.finished is False, "an impossible task must not report success"
    events = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
    classes = [e["class_"] for e in events if e["event"] == "divergence"]
    assert "tool_unknown" in classes
    assert divergence.summarise(events)["harness_defects"] == 0


def test_stall_is_recorded(tmp_path):
    agent, log, trace = _fake_agent(
        tmp_path, [_completion(content="Looks fine to me.", finish_reason="stop")]
    )
    agent.run("do the thing")
    log.close()

    events = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
    assert any(e["event"] == "divergence" and e["class_"] == "stall" for e in events)


def test_step_cap_is_recorded(tmp_path):
    """A run that exhausts its budget diverged from 'the task is completable'."""
    class Call:
        def __init__(self, call_id):
            self.id = call_id
            self.function = type("F", (), {
                "name": "read_range", "arguments": json.dumps({"path": "f.py"})
            })()

    script = [_completion(tool_calls=(Call(f"c{i}"),)) for i in range(3)]
    agent, log, trace = _fake_agent(tmp_path, script)
    agent.max_steps = 3
    agent.run("loop forever")
    log.close()

    events = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
    assert any(e["event"] == "divergence" and e["class_"] == "step_cap" for e in events)
    assert any(e["event"] == "run_end" and e["reason"] == "step_cap" for e in events)


def test_divergence_never_breaks_the_single_terminal_event_invariant(tmp_path):
    """A divergence is a record, not an exit. The run still needs exactly one end."""
    class Call:
        def __init__(self):
            self.id = "c1"
            self.function = type("F", (), {"name": "no_such_tool", "arguments": "{}"})()

    agent, log, trace = _fake_agent(
        tmp_path,
        [_completion(tool_calls=(Call(),)),
         _completion(content="cannot", finish_reason="stop")],
    )
    agent.run("nope")
    log.close()

    events = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
    ends = [e for e in events if e["event"] == "run_end"]
    assert len(ends) == 1, f"expected exactly one run_end, got {len(ends)}"


def test_eval_runner_refuses_to_score_a_run_that_never_reached_the_model():
    """A vacuous verdict is worse than no verdict.

    Regression: the AOP mandate gate reported "NOT MANDATED" when every agent had
    crashed on a missing provider key. Zero failures from zero data looked exactly
    like zero failures from a clean run.
    """
    runner_src = (PROJECT_ROOT / "evals" / "runner.py").read_text()
    assert "idle = [r.id for r in results if r.steps == 0]" in runner_src
    assert '"valid": not idle' in runner_src
    assert 'return 0 if (passed == total and not idle) else 1' in runner_src
