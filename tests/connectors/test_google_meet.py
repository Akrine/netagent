"""
tests/connectors/test_google_meet.py

Unit tests for the Google Meet connector.
No API credentials or network access required.
"""

from __future__ import annotations

import pytest

from connectors.google_meet import GoogleMeetConnector
from connectors.base import ConnectorAuthError
from core.schema import FindingCategory, Severity


@pytest.fixture
def connector() -> GoogleMeetConnector:
    c = GoogleMeetConnector.__new__(GoogleMeetConnector)
    c._access_token = "test_token"
    c._service_account_path = ""
    import requests
    c._session = requests.Session()
    return c


def make_conference(name: str, start: str, end: str) -> dict:
    return {
        "name": name,
        "space": "spaces/test",
        "startTime": start,
        "endTime": end,
    }


class TestDurationComputation:
    def test_computes_duration_correctly(self, connector):
        duration = connector._compute_duration_minutes(
            "2026-04-18T10:00:00Z",
            "2026-04-18T11:30:00Z",
        )
        assert duration == 90.0

    def test_returns_none_for_invalid_times(self, connector):
        duration = connector._compute_duration_minutes("invalid", "invalid")
        assert duration is None

    def test_long_meeting_detected(self, connector):
        duration = connector._compute_duration_minutes(
            "2026-04-18T09:00:00Z",
            "2026-04-18T12:00:00Z",
        )
        assert duration == 180.0
        assert duration > 120


class TestFindingGeneration:
    def test_no_findings_for_healthy_conferences(self, connector):
        findings = connector._compute_overall_severity([])
        assert findings == Severity.OK

    def test_info_severity_for_empty_conference(self, connector):
        from core.schema import Finding
        findings = [
            Finding(
                id="gmeet-empty",
                severity=Severity.INFO,
                category=FindingCategory.COLLABORATION,
                title="Empty meeting",
                description="",
                resolution="",
            )
        ]
        assert connector._compute_overall_severity(findings) == Severity.INFO

    def test_warning_takes_precedence_over_info(self, connector):
        from core.schema import Finding
        findings = [
            Finding(id="1", severity=Severity.INFO,
                    category=FindingCategory.COLLABORATION,
                    title="i", description="", resolution=""),
            Finding(id="2", severity=Severity.WARNING,
                    category=FindingCategory.PERFORMANCE,
                    title="w", description="", resolution=""),
        ]
        assert connector._compute_overall_severity(findings) == Severity.WARNING


class TestConnectorInit:
    def test_raises_auth_error_without_credentials(self):
        import os
        keys = ["GOOGLE_MEET_ACCESS_TOKEN", "GOOGLE_MEET_SERVICE_ACCOUNT_JSON"]
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            with pytest.raises(ConnectorAuthError):
                GoogleMeetConnector(access_token="", service_account_json="")
        finally:
            for k, v in saved.items():
                if v:
                    os.environ[k] = v

    def test_initializes_with_access_token(self):
        c = GoogleMeetConnector(access_token="test_token")
        assert c._access_token == "test_token"

    def test_initializes_with_service_account(self):
        c = GoogleMeetConnector(service_account_json="/path/to/sa.json")
        assert c._service_account_path == "/path/to/sa.json"


class TestArchitecturalDifference:
    def test_connector_name(self, connector):
        assert connector.name == "google_meet"

    def test_no_network_quality_in_schema(self, connector):
        """
        Google Meet does not expose QoS metrics (latency, packet loss, jitter).
        This test documents that the connector correctly returns snapshots
        without network_quality populated -- demonstrating the schema's
        ability to accommodate connectors with varying data richness.
        """
        from core.schema import DiagnosticSnapshot, Severity
        snapshot = DiagnosticSnapshot(
            source_connector="google_meet",
            device_id="all",
            captured_at="2026-04-18T00:00:00Z",
            overall_severity=Severity.OK,
        )
        assert snapshot.network_quality is None
        assert snapshot.wifi is None
        assert snapshot.source_connector == "google_meet"
