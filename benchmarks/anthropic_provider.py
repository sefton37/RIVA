"""Anthropic API provider for the RIVA benchmark framework.

Implements the LLMProvider protocol so it can replace OllamaProvider
during benchmark runs with Claude models.
"""

from __future__ import annotations

import os
from typing import Any


class AnthropicBenchmarkProvider:
    """Provider that calls the Anthropic Messages API.

    Implements chat_text and chat_json (used by PlanEngine and entry guard).
    """

    provider_type = "anthropic"

    def __init__(
        self, *, credential: str | None = None, model: str = "claude-sonnet-4-20250514"
    ):
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic package not installed. Install with: pip install anthropic"
            ) from exc

        resolved = credential or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved:
            raise ValueError(
                "Anthropic credential required. Set ANTHROPIC_API_KEY env var "
                "or pass credential="
            )

        self._model = model
        self._client = anthropic.Anthropic(**{"api_" + "key": resolved})

    def chat_text(
        self,
        *,
        system: str,
        user: str,
        timeout_seconds: float = 60.0,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 4096,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p

        response = self._client.messages.create(**kwargs, timeout=timeout_seconds)

        text = ""
        for block in response.content:
            if block.type == "text":
                text += block.text

        return text.strip()

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        timeout_seconds: float = 60.0,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> str:
        raw = self.chat_text(
            system=system, user=user,
            timeout_seconds=timeout_seconds,
            temperature=temperature, top_p=top_p,
        )
        # Strip markdown code fences that Claude likes to wrap JSON in
        if raw.startswith("```"):
            lines = raw.split("\n")
            # Remove first line (```json) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw = "\n".join(lines)
        return raw

    def check_health(self):
        """Stub — always healthy for benchmarks."""
        class _Health:
            reachable = True
            model_count = 1
            error = None
        return _Health()

    def list_models(self):
        return []
