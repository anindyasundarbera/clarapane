"""Event schema tests: the trace must stay machine-readable as it evolves."""

from __future__ import annotations

import json

import pytest

from clarapane import events_schema as S
from clarapane.events import EventLog


def test_required_keys_are_actually_required():
    """Guards against the postponed-annotations trap: Required must introspect."""
    assert S._required_keys(S.RunStart) == {"task", "model", "tools", "workspace"}
    assert S._required_keys(S.ToolCall) == {"step", "tool", "risk", "ok", "duration_ms"}


def test_unknown_event_rejected():
    with pytest.raises(S.EventSchemaError, match="unknown event"):
        S.validate("not_an_event", {})


def test_missing_required_field_rejected():
    with pytest.raises(S.EventSchemaError, match="missing required fields"):
        S.validate("run_start", {"task": "x"})


def test_undeclared_field_rejected():
    """An ad-hoc field is exactly the schema drift this module exists to stop."""
    with pytest.raises(S.EventSchemaError, match="undeclared fields"):
        S.validate("run_end", {"reason": "finish", "steps": 1, "surprise": True})


def test_tool_call_success_shape():
    S.validate("tool_call", {
        "step": 1, "tool": "grep", "risk": "read", "ok": True,
        "duration_ms": 2, "args": {}, "result": {},
    })


def test_tool_call_failure_must_carry_error():
    with pytest.raises(S.EventSchemaError, match="must carry an error"):
        S.validate("tool_call", {
            "step": 1, "tool": "grep", "risk": "read", "ok": False, "duration_ms": 1,
        })


def test_tool_call_success_cannot_carry_error():
    with pytest.raises(S.EventSchemaError, match="cannot be ok=True"):
        S.validate("tool_call", {
            "step": 1, "tool": "grep", "risk": "read", "ok": True,
            "duration_ms": 1, "error": "x",
        })


def test_legacy_event_cannot_be_emitted_by_new_runs():
    with pytest.raises(S.EventSchemaError, match="legacy"):
        S.validate("profile_selected", {
            "profile": "edit", "source": "cli", "tools": [],
        })


def test_eventlog_rejects_bad_payload_without_writing(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    with pytest.raises(S.EventSchemaError, match="missing required fields"):
        log.emit("run_end", reason="finish")  # `steps` is required
    log.close()
    assert (tmp_path / "e.jsonl").read_text() == ""


def test_eventlog_writes_valid_payload(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    log.emit("run_end", reason="finish", steps=2)
    log.close()
    record = json.loads((tmp_path / "e.jsonl").read_text().strip())
    assert record["event"] == "run_end"
    assert record["seq"] == 1
    assert record["run_id"] == log.run_id


def test_committed_trace_passes_audit():
    """The checked-in traces must remain readable by the declared schema."""
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "runs" / "runs.jsonl"
    if not path.exists():
        pytest.skip("no committed trace yet")

    problems = S.audit(path.read_text().splitlines())
    assert not problems, "\n".join(
        f"line {p.lineno} [{p.event}]: {p.detail}" for p in problems
    )
