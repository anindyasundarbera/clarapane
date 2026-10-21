"""Backlog tracker tests: the guardrails that stop a wishlist becoming rot."""

from __future__ import annotations

import json

import pytest

from clarapane import backlog


@pytest.fixture
def sample():
    return {
        "version": 1,
        "updated": "2026-01-01",
        "items": [
            {
                "id": "open-thing", "title": "An open item", "status": "planned",
                "priority": "high", "area": "testing", "why": "because",
                "trigger": "when needed", "acceptance": ["works"],
            },
            {
                "id": "low-thing", "title": "A low item", "status": "planned",
                "priority": "low", "area": "testing", "why": "nice to have",
                "trigger": "someday", "acceptance": ["works"],
            },
            {
                "id": "closed-thing", "title": "Done", "status": "done",
                "priority": "high", "area": "testing", "why": "was needed",
                "resolved_on": "2026-01-02",
            },
            {
                "id": "no-thing", "title": "Rejected", "status": "rejected",
                "priority": "low", "area": "testing",
                "why": "measured as a net loss",
            },
        ],
    }


def test_real_backlog_is_valid():
    """The shipped backlog must satisfy its own rules."""
    data = backlog.load()
    assert data["items"]


def test_due_excludes_low_priority_and_closed(sample):
    ids = [i["id"] for i in backlog.due(sample)]
    assert ids == ["open-thing"]


def test_reminder_is_none_when_nothing_open(sample):
    sample["items"] = [i for i in sample["items"] if i["status"] != "planned"]
    assert backlog.reminder(sample) is None


def test_reminder_names_the_high_priority_item(sample):
    text = backlog.reminder(sample)
    assert text is not None
    assert "An open item" in text
    assert "1 open item" in text


def test_open_item_without_trigger_is_rejected(sample):
    del sample["items"][0]["trigger"]
    with pytest.raises(backlog.BacklogError, match="trigger"):
        backlog.validate(sample)


def test_open_item_without_acceptance_is_rejected(sample):
    sample["items"][0]["acceptance"] = []
    with pytest.raises(backlog.BacklogError, match="acceptance"):
        backlog.validate(sample)


def test_duplicate_ids_rejected(sample):
    sample["items"].append(dict(sample["items"][0]))
    with pytest.raises(backlog.BacklogError, match="duplicate"):
        backlog.validate(sample)


def test_invalid_status_rejected(sample):
    sample["items"][0]["status"] = "someday"
    with pytest.raises(backlog.BacklogError, match="invalid status"):
        backlog.validate(sample)


def test_done_item_needs_resolution_date(sample):
    del sample["items"][2]["resolved_on"]
    with pytest.raises(backlog.BacklogError, match="resolved_on"):
        backlog.validate(sample)


def test_start_and_done_roundtrip(tmp_path, sample, monkeypatch):
    """The CLI transition commands persist and re-validate."""
    path = tmp_path / "backlog.json"
    path.write_text(json.dumps(sample))
    # main() rebinds the module-level path; monkeypatch restores it afterwards so
    # this test cannot leak state into the other tests.
    monkeypatch.setattr(backlog, "BACKLOG_PATH", path)

    assert backlog.main(["start", "open-thing", "--note", "beginning"]) == 0
    data = backlog.load(path)
    assert backlog.find(data, "open-thing")["status"] == "in_progress"
    assert data["items"][0]["notes"] == [f"{data['updated']}: beginning"]

    assert backlog.main(["done", "open-thing"]) == 0
    data = backlog.load(path)
    assert backlog.find(data, "open-thing")["status"] == "done"
    assert "resolved_on" in backlog.find(data, "open-thing")

    assert backlog.main(["block", "low-thing", "--note", "waiting on eval"]) == 0
    assert backlog.find(backlog.load(path), "low-thing")["status"] == "blocked"


def test_todo_markers_point_at_real_backlog_ids():
    """Every TODO(<id>) in src/ must reference an item that exists."""
    from pathlib import Path

    data = backlog.load()
    known = {i["id"] for i in data["items"]}
    src = Path(__file__).resolve().parent.parent / "src"

    found: list[str] = []
    for file in src.rglob("*.py"):
        for line in file.read_text().splitlines():
            if "TODO(" not in line:
                continue
            # TODO(request-capability-escalation): ...
            marker = line.split("TODO(", 1)[1].split(")", 1)[0].strip()
            found.append(marker)
            assert marker in known, f"{file}: TODO references unknown backlog id {marker!r}"

    assert found, "expected at least one TODO(<backlog-id>) marker in src/"
