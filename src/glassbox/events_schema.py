"""Typed event schemas with write-time validation.

Why this module exists
----------------------
Before this, the trace schema was *implicit*: `emit()` took ``**fields`` and each
call site decided what to include. That worked while the only reader was a human
with jq, but fails as soon as anything programmatic consumes the trace -- an eval
assertion, a SQL ingest, a viewer. A renamed or omitted field breaks them silently.

Now the schema is declared once, checked on every write, and cannot drift without a
test failing.

IMPORTANT: this module deliberately does NOT use ``from __future__ import
annotations``. With postponed annotations, ``TypedDict.__required_keys__`` is
empty and every key looks optional:

    with `from __future__ import annotations`   -> required: frozenset()
    without it                                  -> required: frozenset({'step'})

Validation depends on that introspection, so adding the future import here would
silently disable every "required field missing" check. Verified on Python 3.14.4.
"""

import json
from dataclasses import dataclass
from typing import Any, NotRequired, Required, TypedDict, get_type_hints


class RunStart(TypedDict, total=False):
    task: Required[str]
    model: Required[str]
    tools: Required[list[str]]
    workspace: Required[str]
    profile: str
    profile_source: str  # "cli" | "inferred"


class ModelCall(TypedDict, total=False):
    step: Required[int]
    duration_ms: Required[int]
    tool_calls: Required[list[str]]
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning: str | None
    content: str | None


class ToolCall(TypedDict, total=False):
    """Two legitimate shapes: success carries args/result, failure carries error."""

    step: Required[int]
    tool: Required[str]
    risk: Required[str]
    ok: Required[bool]
    duration_ms: Required[int]
    args: dict[str, Any]
    result: Any
    error: str


class Finish(TypedDict, total=False):
    step: Required[int]
    summary: str | None
    evidence: Any
    # True when the loop coerced a prose answer into this call.
    recovered: bool
    # False when mutating capability was offered but never used: the claim is
    # unproven, and a confident summary is not evidence.
    verified: bool
    original_content: str | None


class RecoveryFailed(TypedDict, total=False):
    step: Required[int]
    error: Required[str]


class Stall(TypedDict, total=False):
    step: Required[int]
    content: str | None


class RunEnd(TypedDict, total=False):
    reason: Required[str]
    steps: Required[int]


class ProviderError(TypedDict, total=False):
    step: Required[int]
    error: Required[str]


class ProfileSelected(TypedDict, total=False):
    """LEGACY (pre-2026-10-22): profile info now lives on RunStart.

    Kept so historical traces remain describable; new runs must not emit it.
    """

    profile: Required[str]
    source: Required[str]
    tools: Required[list[str]]
    description: str


SCHEMAS: dict[str, type] = {
    "run_start": RunStart,
    "model_call": ModelCall,
    "tool_call": ToolCall,
    "finish": Finish,
    "stall": Stall,
    "run_end": RunEnd,
    "provider_error": ProviderError,
    "recovery_failed": RecoveryFailed,
    "profile_selected": ProfileSelected,
}

# Events a new run may no longer emit.
LEGACY_EVENTS = frozenset({"profile_selected"})


class EventSchemaError(ValueError):
    """A write would produce an event that violates the declared schema."""


def _required_keys(schema: type) -> set[str]:
    """Required keys, resolved robustly.

    ``__required_keys__`` is authoritative only when annotations are evaluated
    eagerly. Fall back to ``get_type_hints`` so the check cannot silently degrade
    into "everything optional".
    """
    keys = set(getattr(schema, "__required_keys__", set()))
    if keys:
        return keys
    resolved = get_type_hints(schema, include_extras=True)
    return {
        name for name, hint in resolved.items()
        if getattr(hint, "__origin__", None) is Required
    }


def _allowed_keys(schema: type) -> set[str]:
    return set(get_type_hints(schema, include_extras=True))


def validate(event: str, fields: dict[str, Any], *, allow_legacy: bool = False) -> None:
    """Raise EventSchemaError if `fields` cannot be written as `event`."""
    if event not in SCHEMAS:
        raise EventSchemaError(f"unknown event {event!r}; declared: {sorted(SCHEMAS)}")
    if event in LEGACY_EVENTS and not allow_legacy:
        raise EventSchemaError(f"event {event!r} is legacy; new runs must not emit it")

    schema = SCHEMAS[event]
    missing = _required_keys(schema) - fields.keys()
    extra = fields.keys() - _allowed_keys(schema)
    if missing:
        raise EventSchemaError(f"{event} missing required fields: {sorted(missing)}")
    if extra:
        raise EventSchemaError(
            f"{event} has undeclared fields: {sorted(extra)}. "
            "Declare them in events_schema.py rather than writing them ad hoc."
        )
    if event == "tool_call":
        if fields["ok"] and "error" in fields:
            raise EventSchemaError("tool_call cannot be ok=True and carry an error")
        if not fields["ok"] and "error" not in fields:
            raise EventSchemaError("tool_call with ok=False must carry an error")


@dataclass(frozen=True)
class Problem:
    """One schema violation found while auditing an existing trace."""

    lineno: int
    event: str
    detail: str


def audit(lines: list[str], *, allow_legacy: bool = True) -> list[Problem]:
    """Check an existing trace without rejecting historical variance.

    Old traces predate this schema (they contain `profile_selected` and fields
    added over time), so auditing reports rather than fails. New writes go through
    the strict `validate()` in EventLog.
    """
    problems: list[Problem] = []
    for lineno, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(Problem(lineno, "?", f"invalid JSON: {exc}"))
            continue

        event = record.get("event", "?")
        payload = {
            k: v for k, v in record.items()
            if k not in ("ts", "run_id", "seq", "event")
        }
        try:
            validate(event, payload, allow_legacy=allow_legacy)
        except EventSchemaError as exc:
            problems.append(Problem(lineno, event, str(exc)))

    return problems
