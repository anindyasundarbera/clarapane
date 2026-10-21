"""Provider layer: one adapter over any OpenAI-compatible endpoint.

Why this file exists
--------------------
Everything downstream (agent loop, tools, evals) must never import `openai`
directly. That single rule is what lets us swap Ollama Cloud for another
OpenAI-compatible provider -- or a mock -- without touching agent logic.

Verified against the live Ollama Cloud endpoint during setup:
  * base URL            https://ollama.com/v1
  * model listing       GET https://ollama.com/api/tags   (OpenAI-native, richer)
  * `message.reasoning` IS returned in model_extra for thinking models
  * tool calling works, including parallel tool calls
  * model aliases resolve: `gpt-oss:120b` == `gpt-oss:120b-cloud`
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall


@dataclass(frozen=True)
class ModelInfo:
    """A model advertised by the provider, with the capabilities we care about."""

    name: str
    capabilities: tuple[str, ...]

    @property
    def supports_tools(self) -> bool:
        return "tools" in self.capabilities

    @property
    def supports_thinking(self) -> bool:
        """True when the model emits a separate reasoning trace."""
        return "thinking" in self.capabilities


@dataclass(frozen=True)
class Completion:
    """Normalised result of one model call.

    We normalise deliberately: the agent loop should see a stable shape, not
    provider-specific pydantic objects. This is the seam a mock provider
    implements in tests.
    """

    content: str | None
    tool_calls: tuple[ChatCompletionMessageToolCall, ...]
    reasoning: str | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    model: str

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class Provider:
    """Thin, swappable wrapper around an OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
        *,
        env_path: str = ".env",
    ) -> None:
        load_dotenv(env_path, override=True)
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self.default_model = default_model or os.environ.get("DEFAULT_OLLAMA_MODEL")
        key = api_key or os.environ.get("OPENAI_API_KEY")

        if not self.base_url or not key:
            raise RuntimeError(
                "OPENAI_BASE_URL and OPENAI_API_KEY must be set (see .env)"
            )

        self._client = OpenAI(base_url=self.base_url, api_key=key)

    # ------------------------------------------------------------------ chat
    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> Completion:
        """One chat completion. No retries here -- the graph owns retry policy."""
        resp = self._client.chat.completions.create(
            model=model or self.default_model or "",
            messages=messages,
            tools=tools or None,
            parallel_tool_calls=True if tools else None,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choice = resp.choices[0]
        message: ChatCompletionMessage = choice.message
        usage = resp.usage

        # Ollama returns the thinking trace out-of-band in model_extra.
        extra = message.model_extra or {}

        return Completion(
            content=message.content,
            tool_calls=tuple(message.tool_calls or ()),
            reasoning=extra.get("reasoning"),
            finish_reason=choice.finish_reason,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            model=resp.model,
        )

    # ---------------------------------------------------------------- models
    def list_models(self) -> list[ModelInfo]:
        """List provider models with capabilities.

        Uses Ollama's native /api/tags rather than /v1/models because only the
        native route exposes `capabilities`, which tells us whether a model can
        do tool calling. Non-Ollama providers can override this.
        """
        root = self.base_url.removesuffix("/v1").rstrip("/")
        models = self._client.models.list()  # OpenAI-native fallback shape
        names = [m.id for m in models.data]

        caps = self._fetch_capabilities(root)
        return [
            ModelInfo(name=n, capabilities=tuple(caps.get(n, ())))
            for n in names
        ]

    def _fetch_capabilities(self, root: str) -> dict[str, tuple[str, ...]]:
        import json
        import urllib.request

        url = f"{root}/api/tags"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._client.api_key}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.load(resp)
        except Exception:  # noqa: BLE001 - capability probe is best-effort
            return {}
        return {
            m["name"]: tuple(m.get("capabilities") or ())
            for m in payload.get("models", [])
        }
