"""Agent-loop tests against a scripted fake provider.

The point of these tests: the loop's control flow (tool dispatch, error
recovery, step cap, finish detection) is verified without a network call.
"""

from __future__ import annotations

import json

import pytest

from glassbox.events import EventLog
from glassbox.registry import ToolRegistry, ToolSpec
from glassbox.sdk_agent import Agent
from glassbox.tools.finish_tool import register_finish_tool
from glassbox.tools.read_tools import register_read_tools


class FakeProvider:
    """Replays a fixed list of Completion objects, then fails loudly."""

    default_model = "fake-model"

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def complete(self, messages, *, model=None, tools=None, **kw):
        from glassbox.provider import Completion

        self.calls += 1
        if not self.script:
            raise AssertionError("provider called more times than scripted")
        item = self.script.pop(0)
        if callable(item):
            item = item(messages)
        return Completion(
            content=item.get("content"),
            tool_calls=tuple(item.get("tool_calls", ())),
            reasoning=item.get("reasoning"),
            finish_reason=item.get("finish_reason", "tool_calls"),
            prompt_tokens=10,
            completion_tokens=5,
            model="fake-model",
        )


class FakeCall:
    """Mimics the OpenAI tool_call shape the loop consumes."""

    def __init__(self, name, args, call_id="call_1"):
        self.id = call_id
        self.function = type("Fn", (), {"name": name, "arguments": json.dumps(args)})()


@pytest.fixture
def agent_factory(workspace, tmp_path):
    def build(script, max_steps=4):
        registry = ToolRegistry()
        register_read_tools(registry, workspace)
        register_finish_tool(registry)
        log = EventLog(tmp_path / "events.jsonl")
        agent = Agent(FakeProvider(script), registry, workspace, log,
                      max_steps=max_steps, verbose=False)
        return agent, log

    return build


def test_agent_reads_then_finishes(agent_factory):
    agent, log = agent_factory([
        {"tool_calls": [FakeCall("read_range", {"path": "pkg/mod.py", "start": 1, "end": 2})]},
        {"tool_calls": [FakeCall("finish", {"summary": "found alpha", "evidence": "pkg/mod.py:1"})]},
    ])
    result = agent.run("where is alpha?")

    assert result.finished is True
    assert result.steps[0].tool_ok is True
    assert result.summary == "found alpha"
    assert result.evidence == "pkg/mod.py:1"
    log.close()


def test_agent_recovers_from_a_tool_error(agent_factory):
    """A bad path must be reported back to the model, not abort the run."""
    agent, log = agent_factory([
        {"tool_calls": [FakeCall("read_range", {"path": "missing.py"})]},
        {"tool_calls": [FakeCall("finish", {"summary": "recovered", "evidence": "n/a"})]},
    ])
    result = agent.run("read a file")

    assert result.finished is True
    assert result.steps[0].tool_ok is False
    assert "not a file" in (result.steps[0].error or "")
    # The error was fed back as a tool message so the model could correct itself.
    tool_msgs = [m for m in result.messages if m["role"] == "tool"]
    assert any("error" in m["content"] for m in tool_msgs)
    log.close()


def test_agent_reports_stall_when_model_answers_without_tools(agent_factory):
    agent, log = agent_factory([{"content": "I think the answer is 42.", "finish_reason": "stop"}])
    result = agent.run("do something")

    assert result.finished is False
    assert "without calling a tool" in result.reason
    log.close()


def test_step_cap_stops_a_looping_agent(agent_factory):
    loop = {"tool_calls": [FakeCall("list_dir", {"path": "."})]}
    agent, log = agent_factory([loop] * 10, max_steps=3)
    result = agent.run("spin forever")

    assert result.finished is False
    assert "step cap" in result.reason
    log.close()


def test_unknown_tool_is_an_error_not_a_crash(agent_factory):
    agent, log = agent_factory([
        {"tool_calls": [FakeCall("delete_everything", {})]},
        {"tool_calls": [FakeCall("finish", {"summary": "gave up", "evidence": "n/a"})]},
    ])
    result = agent.run("try something unsupported")

    assert result.steps[0].tool_ok is False
    assert "unknown tool" in (result.steps[0].error or "")
    log.close()


def test_invalid_json_arguments_are_handled(agent_factory):
    class BadCall:
        id = "call_bad"
        function = type("Fn", (), {"name": "read_range", "arguments": "{not json"})()

    agent, log = agent_factory([
        {"tool_calls": [BadCall()]},
        {"tool_calls": [FakeCall("finish", {"summary": "ok", "evidence": "n/a"})]},
    ])
    result = agent.run("bad json")

    assert result.steps[0].tool_ok is False
    assert "not valid JSON" in (result.steps[0].error or "")
    log.close()


def test_every_step_is_logged_with_a_run_id(agent_factory):
    agent, log = agent_factory([
        {"tool_calls": [FakeCall("list_dir", {"path": "."})]},
        {"tool_calls": [FakeCall("finish", {"summary": "done", "evidence": "n/a"})]},
    ])
    agent.run("list files")
    log.close()

    records = [json.loads(line) for line in log.path.read_text().splitlines() if line.strip()]
    assert records[0]["event"] == "run_start"
    assert records[-1]["event"] == "run_end"
    assert {r["run_id"] for r in records} == {log.run_id}
    assert [r["seq"] for r in records] == sorted(r["seq"] for r in records)


# ---------------------------------------------------------------- stall recovery


class SequenceProvider(FakeProvider):
    """FakeProvider that also honours forced tool_choice, like the real provider."""

    def complete(self, messages, *, model=None, tools=None, tool_choice=None, **kw):
        return super().complete(messages, model=model, tools=tools, **kw)


def test_prose_answer_is_recovered_into_finish(agent_factory):
    """The common real failure: correct work, wrong protocol. Recover, don't discard."""
    agent, log = agent_factory([
        {"content": "add is at calculator/ops.py:9", "finish_reason": "stop"},
        {"tool_calls": [FakeCall("finish", {"summary": "add at ops.py:9",
                                           "evidence": "ops.py:9"})]},
    ])
    result = agent.run("where is add?")

    assert result.finished is True
    assert result.reason == "finish_recovered"
    assert result.summary == "add at ops.py:9"
    log.close()


def test_recovery_is_marked_in_the_trace(agent_factory):
    """A reader must be able to tell a voluntary finish from a coerced one."""
    agent, log = agent_factory([
        {"content": "prose", "finish_reason": "stop"},
        {"tool_calls": [FakeCall("finish", {"summary": "s", "evidence": "e"})]},
    ])
    agent.run("q")
    log.close()

    records = [json.loads(x) for x in log.path.read_text().splitlines() if x.strip()]
    finish = next(r for r in records if r["event"] == "finish")
    assert finish["recovered"] is True
    assert finish["verified"] is True
    assert finish["original_content"] == "prose"


def test_recovery_refuses_success_when_mutation_was_offered_but_unused(
    workspace, tmp_path
):
    """The safety property: a confident summary is not evidence.

    A read-only task that legitimately finishes must succeed. An EDIT task where
    the agent never called the mutating tool must NOT be reported as finished,
    even though the model was coerced into claiming it.
    """
    from glassbox.registry import ToolRegistry, ToolSpec

    registry = ToolRegistry()
    register_read_tools(registry, workspace)
    register_finish_tool(registry)
    registry.register(ToolSpec(
        name="apply_edit", description="apply an edit",
        parameters={"type": "object", "properties": {}, "required": []},
        risk="write", gate=True, handler=lambda **kw: {"ok": True},
    ))

    provider = SequentialProvider([
        {"content": "I fixed it, trust me", "finish_reason": "stop"},
        {"tool_calls": [FakeCall("finish", {"summary": "fixed", "evidence": "trust me"})]},
    ])
    log = EventLog(tmp_path / "e.jsonl")
    agent = Agent(provider, registry, workspace, log, max_steps=3, verbose=False)

    result = agent.run("fix the bug")
    log.close()

    assert result.finished is False, "accepted an unproven success claim"
    assert result.reason == "finish_unverified"
    records = [json.loads(x) for x in log.path.read_text().splitlines() if x.strip()]
    finish = next(r for r in records if r["event"] == "finish")
    assert finish["verified"] is False


def test_recovery_accepts_success_when_mutation_was_actually_used(workspace, tmp_path):
    """If the agent did call a mutating tool, a recovered finish is legitimate."""
    from glassbox.registry import ToolRegistry, ToolSpec

    registry = ToolRegistry()
    register_read_tools(registry, workspace)
    register_finish_tool(registry)
    registry.register(ToolSpec(
        name="apply_edit", description="apply an edit",
        parameters={"type": "object", "properties": {}, "required": []},
        risk="write", gate=True, handler=lambda **kw: {"ok": True},
    ))

    provider = SequentialProvider([
        {"tool_calls": [FakeCall("apply_edit", {}, "call_edit")]},
        {"content": "done", "finish_reason": "stop"},
        {"tool_calls": [FakeCall("finish", {"summary": "fixed", "evidence": "diff"},
                                 "call_fin")]},
    ])
    log = EventLog(tmp_path / "e.jsonl")
    agent = Agent(provider, registry, workspace, log, max_steps=4, verbose=False)

    result = agent.run("fix the bug")
    log.close()

    assert result.finished is True
    assert result.reason == "finish_recovered"


def test_recovery_failure_falls_back_to_stall(agent_factory):
    """If the provider will not coerce, report a stall rather than pretend."""
    agent, log = agent_factory([
        {"content": "prose", "finish_reason": "stop"},
        {"content": "still prose", "finish_reason": "stop"},
    ])
    result = agent.run("q")

    assert result.finished is False
    assert "without calling a tool" in result.reason
    log.close()


class SequentialProvider(FakeProvider):
    def complete(self, messages, *, model=None, tools=None, tool_choice=None, **kw):
        return super().complete(messages, model=model, tools=tools, **kw)
