"""
tests/test_llm.py

Tests for the swappable LLM backend abstraction.

Alisson's instruction: "change parts of your natural language stack
later, and have tests for those." These tests prove the contract:
any backend that implements BaseLLMBackend is a valid drop-in.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.llm import AnthropicBackend, BaseLLMBackend, LLMResponse, OllamaBackend


class TestLLMResponse:
    def test_holds_text_model_backend(self):
        r = LLMResponse(text="hello", model="phi3:mini", backend="ollama")
        assert r.text == "hello"
        assert r.model == "phi3:mini"
        assert r.backend == "ollama"

    def test_repr_is_informative(self):
        r = LLMResponse(text="hello world", model="test", backend="test")
        assert "test" in repr(r)
        assert "11" in repr(r)


class TestBackendContract:
    """Prove BaseLLMBackend enforces the interface."""

    def test_cannot_instantiate_base_directly(self):
        with pytest.raises(TypeError):
            BaseLLMBackend()

    def test_concrete_backend_must_implement_name(self):
        class BadBackend(BaseLLMBackend):
            def complete(self, system_prompt, messages, max_tokens=1024):
                return LLMResponse("", "", "bad")
        with pytest.raises(TypeError):
            BadBackend()

    def test_concrete_backend_must_implement_complete(self):
        class BadBackend(BaseLLMBackend):
            @property
            def name(self):
                return "bad"
        with pytest.raises(TypeError):
            BadBackend()

    def test_valid_backend_satisfies_contract(self):
        class GoodBackend(BaseLLMBackend):
            @property
            def name(self):
                return "good"
            def complete(self, system_prompt, messages, max_tokens=1024):
                return LLMResponse("ok", "test-model", self.name)

        backend = GoodBackend()
        result = backend.complete("system", [{"role": "user", "content": "hi"}])
        assert isinstance(result, LLMResponse)
        assert result.text == "ok"


class TestAnthropicBackend:
    def test_name_is_anthropic(self):
        backend = AnthropicBackend(api_key="test")
        assert backend.name == "anthropic"

    def test_complete_returns_llm_response(self):
        backend = AnthropicBackend(api_key="test")
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="Network looks healthy.")]
        with patch.object(backend._client.messages, "create", return_value=mock_resp):
            result = backend.complete("system", [{"role": "user", "content": "status?"}])
        assert isinstance(result, LLMResponse)
        assert result.text == "Network looks healthy."
        assert result.backend == "anthropic"

    def test_complete_raises_on_failure(self):
        backend = AnthropicBackend(api_key="test")
        with patch.object(backend._client.messages, "create", side_effect=Exception("API down")):
            with pytest.raises(RuntimeError, match="Anthropic backend failed"):
                backend.complete("system", [{"role": "user", "content": "hi"}])


class TestOllamaBackend:
    def test_name_is_ollama(self):
        backend = OllamaBackend()
        assert backend.name == "ollama"

    def test_complete_returns_llm_response(self):
        backend = OllamaBackend(host="http://localhost:11434", model="phi3:mini")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": "All clear."}
        with patch("requests.post", return_value=mock_resp):
            result = backend.complete("system", [{"role": "user", "content": "status?"}])
        assert isinstance(result, LLMResponse)
        assert result.text == "All clear."
        assert result.backend == "ollama"
        assert result.model == "phi3:mini"

    def test_complete_raises_on_failure(self):
        backend = OllamaBackend()
        with patch("requests.post", side_effect=Exception("connection refused")):
            with pytest.raises(RuntimeError, match="Ollama backend failed"):
                backend.complete("system", [{"role": "user", "content": "hi"}])

    def test_builds_prompt_from_messages(self):
        backend = OllamaBackend()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": "ok"}
        with patch("requests.post", return_value=mock_resp) as mock_post:
            backend.complete(
                "You are a diagnostic assistant.",
                [
                    {"role": "user", "content": "What is wrong?"},
                    {"role": "assistant", "content": "Let me check."},
                    {"role": "user", "content": "Please hurry."},
                ],
            )
        call_args = mock_post.call_args
        prompt = call_args[1]["json"]["prompt"]
        assert "You are a diagnostic assistant." in prompt
        assert "What is wrong?" in prompt


class TestBackendSwappability:
    """Prove you can swap backends without touching the agent."""

    def test_anthropic_and_ollama_return_same_type(self):
        anthropic = AnthropicBackend(api_key="test")
        ollama = OllamaBackend()

        mock_anthropic = MagicMock()
        mock_anthropic.content = [MagicMock(text="Anthropic answer")]

        mock_ollama = MagicMock()
        mock_ollama.raise_for_status = MagicMock()
        mock_ollama.json.return_value = {"response": "Ollama answer"}

        with patch.object(anthropic._client.messages, "create", return_value=mock_anthropic):
            r1 = anthropic.complete("sys", [{"role": "user", "content": "hi"}])

        with patch("requests.post", return_value=mock_ollama):
            r2 = ollama.complete("sys", [{"role": "user", "content": "hi"}])

        assert type(r1) is type(r2)
        assert isinstance(r1, LLMResponse)
        assert isinstance(r2, LLMResponse)
        assert r1.backend == "anthropic"
        assert r2.backend == "ollama"
