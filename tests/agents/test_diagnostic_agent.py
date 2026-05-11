"""
tests/agents/test_diagnostic_agent.py

Unit tests for DiagnosticAgent access control and role-aware behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agents.diagnostic import DiagnosticAgent
from core.llm import LLMResponse
from core.schema import DiagnosticSnapshot, Severity
from core.user_context import (
    make_end_user_context,
    make_operator_context,
    make_admin_context,
)


def _make_snapshot(device_id: str = "device-A") -> DiagnosticSnapshot:
    return DiagnosticSnapshot(
        source_connector="network_weather",
        device_id=device_id,
        captured_at=datetime.now(timezone.utc).isoformat(),
        overall_severity=Severity.OK,
        findings=[],
    )


@pytest.fixture
def agent() -> DiagnosticAgent:
    return DiagnosticAgent(api_key="test-key", enable_logging=False)


@pytest.fixture
def snapshot_a() -> DiagnosticSnapshot:
    return _make_snapshot("device-A")


@pytest.fixture
def snapshot_b() -> DiagnosticSnapshot:
    return _make_snapshot("device-B")


class TestAccessControl:
    def test_access_denied_when_device_not_in_allowed_list(self, agent, snapshot_b):
        ctx = make_end_user_context(user_id="user-1", device_id="device-A")
        response = agent.query(snapshot_b, "What is wrong?", user_context=ctx)
        assert "Access denied" in response.answer
        assert response.sources == []
        assert response.follow_up_suggestions == []

    def test_access_allowed_when_device_in_allowed_list(self, agent, snapshot_a):
        ctx = make_end_user_context(user_id="user-1", device_id="device-A")
        mock_response = LLMResponse(text="Everything looks fine.", model="test", backend="test")
        with patch.object(agent._backend, "complete", return_value=mock_response):
            response = agent.query(snapshot_a, "What is wrong?", user_context=ctx)
        assert "Access denied" not in response.answer
        assert response.answer == "Everything looks fine."

    def test_fleet_scope_can_access_any_device(self, agent, snapshot_b):
        ctx = make_operator_context(user_id="op-1", org_id="org-1")
        mock_response = LLMResponse(text="Fleet looks healthy.", model="test", backend="test")
        with patch.object(agent._backend, "complete", return_value=mock_response):
            response = agent.query(snapshot_b, "Fleet status?", user_context=ctx)
        assert "Access denied" not in response.answer

    def test_admin_scope_can_access_any_device(self, agent, snapshot_b):
        ctx = make_admin_context(user_id="admin-1")
        mock_response = LLMResponse(text="All clear.", model="test", backend="test")
        with patch.object(agent._backend, "complete", return_value=mock_response):
            response = agent.query(snapshot_b, "Status?", user_context=ctx)
        assert "Access denied" not in response.answer

    def test_no_user_context_passes_through(self, agent, snapshot_a):
        mock_response = LLMResponse(text="Here is the data.", model="test", backend="test")
        with patch.object(agent._backend, "complete", return_value=mock_response):
            response = agent.query(snapshot_a, "What is wrong?", user_context=None)
        assert "Access denied" not in response.answer

    def test_llm_not_called_on_access_denied(self, agent, snapshot_b):
        ctx = make_end_user_context(user_id="user-1", device_id="device-A")
        with patch.object(agent._backend, "complete") as mock_create:
            agent.query(snapshot_b, "What is wrong?", user_context=ctx)
            mock_create.assert_not_called()
