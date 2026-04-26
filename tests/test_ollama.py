"""
tests/test_ollama.py

Unit tests for the OllamaConnector.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from connectors.ollama import OllamaConnector
from core.schema import (
    DiagnosticSnapshot,
    Finding,
    FindingCategory,
    Severity,
)


@pytest.fixture
def connector() -> OllamaConnector:
    return OllamaConnector(host="http://localhost:11434", model="phi3:mini")


@pytest.fixture
def sample_snapshot() -> DiagnosticSnapshot:
    return DiagnosticSnapshot(
        source_connector="network_weather",
        device_id="device-abc",
        captured_at="2026-04-21T00:00:00Z",
        findings=[
            Finding(
                id="F1",
                severity=Severity.WARNING,
                category=FindingCategory.WIFI,
                title="Connection dropouts",
                description="220 periods where your internet froze.",
                resolution="Restart your router.",
                technical_detail="RTT avg: 69.8ms",
            )
        ],
        overall_severity=Severity.WARNING,
    )


class TestPromptBuilding:
    def test_prompt_contains_question(self, connector, sample_snapshot):
        prompt = connector._build_prompt(sample_snapshot, "Why is Zoom freezing?")
        assert "Why is Zoom freezing?" in prompt

    def test_prompt_contains_connector_name(self, connector, sample_snapshot):
        prompt = connector._build_prompt(sample_snapshot, "Q")
        assert "network_weather" in prompt

    def test_prompt_contains_severity(self, connector, sample_snapshot):
        prompt = connector._build_prompt(sample_snapshot, "Q")
        assert "warning" in prompt

    def test_prompt_contains_finding_title(self, connector, sample_snapshot):
        prompt = connector._build_prompt(sample_snapshot, "Q")
        assert "Connection dropouts" in prompt

    def test_prompt_contains_technical_detail(self, connector, sample_snapshot):
        prompt = connector._build_prompt(sample_snapshot, "Q")
        assert "RTT avg: 69.8ms" in prompt

    def test_prompt_no_findings(self, connector):
        snapshot = DiagnosticSnapshot(
            source_connector="test",
            device_id="local",
            captured_at="2026-04-21T00:00:00Z",
            overall_severity=Severity.OK,
        )
        prompt = connector._build_prompt(snapshot, "Q")
        assert "No issues detected" in prompt


class TestQuery:
    def test_query_returns_response(self, connector, sample_snapshot):
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "Your Zoom is freezing due to dropouts."}
        mock_response.raise_for_status = MagicMock()

        with patch("connectors.ollama.requests.post", return_value=mock_response):
            answer = connector.query(sample_snapshot, "Why is Zoom freezing?")

        assert answer == "Your Zoom is freezing due to dropouts."

    def test_query_strips_whitespace(self, connector, sample_snapshot):
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "  Some answer  "}
        mock_response.raise_for_status = MagicMock()

        with patch("connectors.ollama.requests.post", return_value=mock_response):
            answer = connector.query(sample_snapshot, "Q")

        assert answer == "Some answer"

    def test_query_raises_on_request_failure(self, connector, sample_snapshot):
        import requests
        with patch("connectors.ollama.requests.post", side_effect=requests.RequestException("timeout")):
            with pytest.raises(RuntimeError, match="Ollama request failed"):
                connector.query(sample_snapshot, "Q")

    def test_query_posts_to_correct_endpoint(self, connector, sample_snapshot):
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "answer"}
        mock_response.raise_for_status = MagicMock()

        with patch("connectors.ollama.requests.post", return_value=mock_response) as mock_post:
            connector.query(sample_snapshot, "Q")

        call_args = mock_post.call_args
        assert "http://localhost:11434/api/generate" in call_args[0]

    def test_query_sends_correct_model(self, connector, sample_snapshot):
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "answer"}
        mock_response.raise_for_status = MagicMock()

        with patch("connectors.ollama.requests.post", return_value=mock_response) as mock_post:
            connector.query(sample_snapshot, "Q")

        body = mock_post.call_args[1]["json"]
        assert body["model"] == "phi3:mini"
        assert body["stream"] is False
