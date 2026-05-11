"""
core/llm.py

Swappable LLM backend interface for the Savvy diagnostic agent.

The agent never talks to an LLM directly — it goes through a backend.
Swapping from Anthropic to Ollama to a fine-tuned local model requires
changing one line at construction time, not rewriting the agent.

This is the abstraction Alisson described: "change parts of your
natural language stack later, and have tests for those."
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional


class LLMResponse:
    """Normalized response from any LLM backend."""

    def __init__(self, text: str, model: str, backend: str) -> None:
        self.text = text
        self.model = model
        self.backend = backend

    def __repr__(self) -> str:
        return f"LLMResponse(backend={self.backend!r}, model={self.model!r}, len={len(self.text)})"


class BaseLLMBackend(ABC):
    """
    Abstract base for all LLM backends.

    Implementations must handle their own auth, HTTP, and error
    handling. The agent only sees complete() and the backend name.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend identifier e.g. 'anthropic', 'ollama'."""

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """
        Send a completion request and return a normalized response.

        Args:
            system_prompt: The system context string.
            messages: List of {role, content} dicts (user/assistant turns).
            max_tokens: Maximum tokens to generate.

        Returns:
            LLMResponse with the generated text.

        Raises:
            RuntimeError: If the backend call fails.
        """


class AnthropicBackend(BaseLLMBackend):
    """
    Anthropic Claude backend.

    Uses the Anthropic Python SDK. Requires ANTHROPIC_API_KEY
    in environment or passed explicitly.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        import anthropic
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        )
        self._model = model

    @property
    def name(self) -> str:
        return "anthropic"

    def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
    ) -> LLMResponse:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=messages,
            )
            return LLMResponse(
                text=response.content[0].text,
                model=self._model,
                backend=self.name,
            )
        except Exception as exc:
            raise RuntimeError(f"Anthropic backend failed: {exc}") from exc


class OllamaBackend(BaseLLMBackend):
    """
    Ollama local inference backend.

    Talks to a locally running Ollama instance via HTTP.
    No API key required — just a running Ollama server.
    """

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "phi3:mini",
    ) -> None:
        self._host = host.rstrip("/")
        self._model = model

    @property
    def name(self) -> str:
        return "ollama"

    def complete(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1024,
    ) -> LLMResponse:
        import requests

        # Build a single prompt from system + messages
        prompt_parts = [system_prompt, ""]
        for msg in messages:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            prompt_parts.append(f"{role}: {content}")
        prompt_parts.append("Assistant:")
        prompt = "\n".join(prompt_parts)

        try:
            resp = requests.post(
                f"{self._host}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": max_tokens},
                },
                timeout=120,
            )
            resp.raise_for_status()
            text = resp.json().get("response", "").strip()
            return LLMResponse(
                text=text,
                model=self._model,
                backend=self.name,
            )
        except Exception as exc:
            raise RuntimeError(f"Ollama backend failed: {exc}") from exc
