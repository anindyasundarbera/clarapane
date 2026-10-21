"""Tool registry: schema, risk class, and handler in one declaration.

Risk class lives HERE, not in the prompt. The model can describe any tool call
it likes; it cannot escalate what that call is allowed to do. That is the
control half of the glass box.

A tool spec is a single atomic intent. If a handler does two things, it should
be two tools -- that is why propose_edit and apply_edit stay separate later.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Risk = Literal["read", "write", "exec"]


class ToolError(RuntimeError):
    """A tool failed in an expected way; the message is shown to the model."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    risk: Risk
    handler: Callable[..., Any]
    # Gated tools require human approval unless a policy file overrides it.
    gate: bool = False

    def schema(self) -> dict[str, Any]:
        """OpenAI `tools` entry for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(f"unknown tool: {name}") from None

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.schema() for spec in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools
