"""The `finish` tool: how the agent declares completion.

Requiring `evidence` is a cheap but effective eval hook. An agent that claims
success must point at the diff, file:line, or command output that proves it --
which makes an unsupported claim visible in the trace instead of invisible.
"""

from __future__ import annotations

from typing import Any

from ..registry import ToolRegistry, ToolSpec


def finish(summary: str, evidence: Any = None) -> dict[str, Any]:
    # The agent loop intercepts this tool before dispatch; this handler only
    # exists so the schema is registered and the call is well-formed.
    return {"acknowledged": True, "summary": summary, "evidence": evidence}


def register_finish_tool(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        name="finish",
        description=(
            "Declare the task complete. Provide a one-line summary and concrete "
            "evidence (file:line, diff summary, or command output) proving it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "What was accomplished."},
                "evidence": {
                    "type": "string",
                    "description": "Proof: file:line references, diff, or command output.",
                },
            },
            "required": ["summary", "evidence"],
        },
        risk="read",
        handler=finish,
    ))
