"""Append-only JSONL event log -- the single source of truth for a run.

Every tool call, model response, policy decision and approval flows through this
one writer. Nothing else in the codebase may open the log file. That restriction
is what makes the trace trustworthy: if an action happened, it is in here, and
there is exactly one place that could have written it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class EventLog:
    """Writes one JSON object per line, flushed eagerly so a crash loses nothing."""

    def __init__(self, path: Path, run_id: str | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = run_id or f"{stamp}-{uuid.uuid4().hex[:6]}"
        self.seq = 0
        self._fh = self.path.open("a", encoding="utf-8")

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        """Record one event. `seq` gives a total order independent of clock skew."""
        self.seq += 1
        record: dict[str, Any] = {
            "ts": _now(),
            "run_id": self.run_id,
            "seq": self.seq,
            "event": event,
        }
        record.update(fields)
        self._fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        self._fh.flush()
        return record

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> EventLog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
