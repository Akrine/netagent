"""
tests/test_ollama_dual.py

Unit tests for the DualModelOllamaConnector.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from connectors.ollama_dual import DualModelOllamaConnector, DualModelResponse
from core.schema import (
    DiagnosticSnapshot,
    Finding,
    FindingCategory,
    Severity,
)


@pytest.fixture
def connector() -> DualModelOllamaConnector:
    return DualModelOllamaConnector(
        host="http://localhost:11434",
        model_a="phi3:mini",
        model_b="phi3:mini",
        agreement_threshold=0.5,
    )


@pytest.fixture
def sample_snapshot() -> DiagnosticSnapshot:
    return DiagnosticSnapshot(
        source_connector="network_weather",
        device_id="device-abc",
        captured_at="2026-04-26T00:00:00Z",
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


def make_mock_response(text: str) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = {"response": text}
    mock.raise_for_status = MagicMock()
    return mock


class TestSimilarityScore:
    def test_identical_responses_score_one(self, connector):
        text = "The connection dropouts are causing your Zoom calls to freeze repeatedly."
        score = connector._similarity_score(text, text)
        assert score == 1.0

    def test_completely_different_responses_score_low(self, connector):
        a = "dropout connection wifi router interference signal"
        b = "salesforce opportunity pipeline revenue forecast"
        score = connector._similarity_score(a, b)
        assert score < 0.2

    def test_similar_responses_score_high(self, connector):
        a = "Connection dropouts are causing packet loss and freezing during video calls."
        b = "The frequent connection dropouts explain the packet loss and video freezing."
        score = connector._similarity_score(a, b)
        assert score > 0.3

    def test_empty_responses_score_zero(self, connector):
        score = connector._similarity_score("", "")
        assert score == 0.0


class TestConsensus:
    def test_agreed_responses_return_high_confidence(self, connector):
        a = "Connection dropouts cause packet loss and freezing during video calls because packets are dropped repeatedly."
        b = "Connection dropouts cause packet loss and freezing during video calls because the network is unstable."
        result = connector._build_consensus(a, b)
        assert result.agreed is True
        assert result.confidence == "high"

    def test_disagreed_responses_return_low_confidence(self, connector):
        a = "Connection dropouts are causing packet loss and freezing during Zoom calls."
        b = "Your Salesforce pipeline needs immediate attention with overdue opportunities."
        result = connector._build_consensus(a, b)
        assert result.agreed is False
        assert result.confidence == "low"

    def test_disagreed_response_contains_both_assessments(self, connector):
        a = "dropout connection freezing"
        b = "salesforce pipeline revenue"
        result = connector._build_consensus(a, b)
        assert "Assessment 1" in result.answer
        assert "Assessment 2" in result.answer

    def test_agreed_picks_longer_response(self, connector):
        short = "Connection dropouts cause freezing."
        long = "Connection dropouts cause freezing during video calls because packets are lost repeatedly."
        result = connector._build_consensus(short, long)
        if result.agreed:
            assert result.answer == long

    def test_result_contains_both_raw_responses(self, connector):
        a = "Response from model A about dropouts."
        b = "Response from model B about dropouts connection freezing."
        result = connector._build_consensus(a, b)
        assert result.model_a == a
        assert result.model_b == b

    def test_note_contains_agreement_score(self, connector):
        a = "Connection dropouts cause freezing."
        b = "Salesforce pipeline needs attention."
        result = connector._build_consensus(a, b)
        assert "%" in result.note


class TestQuery:
    def test_query_returns_dual_model_response(self, connector, sample_snapshot):
        with patch("connectors.ollama_dual.requests.post",
                   return_value=make_mock_response("Connection dropouts cause freezing.")):
            result = connector.query(sample_snapshot, "Why is Zoom freezing?")
        assert isinstance(result, DualModelResponse)
        assert result.answer
        assert result.confidence in ("high", "low")

    def test_query_calls_both_models(self, connector, sample_snapshot):
        with patch("connectors.ollama_dual.requests.post",
                   return_value=make_mock_response("answer")) as mock_post:
            connector.query(sample_snapshot, "Q")
        assert mock_post.call_count == 2

    def test_query_raises_on_model_failure(self, connector, sample_snapshot):
        import requests
        with patch("connectors.ollama_dual.requests.post",
                   side_effect=requests.RequestException("timeout")):
            with pytest.raises(RuntimeError, match="Ollama request to model"):
                connector.query(sample_snapshot, "Q")
