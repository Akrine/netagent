"""
tests/connectors/test_zoom.py

Unit tests for the Zoom connector normalization logic.
No API credentials or network access required.
"""

from __future__ import annotations

import pytest

from connectors.zoom import ZoomConnector
from connectors.base import ConnectorAuthError
from core.schema import FindingCategory, Severity


@pytest.fixture
def connector() -> ZoomConnector:
    c = ZoomConnector.__new__(ZoomConnector)
    c._account_id = "test_account"
    c._client_id = "test_client"
    c._client_secret = "test_secret"
    c._token = "test_token"
    c._token_expiry = float("inf")
    import requests
    c._session = requests.Session()
    return c


def make_participant(name: str, audio: str = "good", video: str = "good") -> dict:
    return {
        "user_name": name,
        "audio_quality": audio,
        "video_quality": video,
        "network_type": "Wifi",
    }


def make_meeting(topic: str, quality: str = "good", participants: int = 5) -> dict:
    return {
        "id": "123",
        "topic": topic,
        "quality_score": quality,
        "participants": participants,
        "duration": 60,
    }


class TestMeetingQualityAnalysis:
    def test_no_findings_for_good_quality(self, connector):
        participants = [
            make_participant("Alice", "good", "good"),
            make_participant("Bob", "good", "good"),
        ]
        findings = connector._analyze_meeting_quality("123", {}, participants)
        assert findings == []

    def test_warning_for_poor_audio_participants(self, connector):
        participants = [
            make_participant("Alice", "bad", "good"),
            make_participant("Bob", "poor", "good"),
        ]
        findings = connector._analyze_meeting_quality("123", {}, participants)
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert findings[0].category == FindingCategory.COLLABORATION
        assert "2 participants" in findings[0].title

    def test_info_finding_when_no_participants(self, connector):
        findings = connector._analyze_meeting_quality("123", {}, [])
        assert len(findings) == 1
        assert findings[0].severity == Severity.INFO


class TestMeetingHasQualityIssues:
    def test_good_meeting_has_no_issues(self, connector):
        meeting = make_meeting("Standup", quality="good")
        assert connector._meeting_has_quality_issues(meeting) is False

    def test_bad_meeting_has_issues(self, connector):
        meeting = make_meeting("Standup", quality="bad")
        assert connector._meeting_has_quality_issues(meeting) is True

    def test_fair_meeting_has_issues(self, connector):
        meeting = make_meeting("Standup", quality="fair")
        assert connector._meeting_has_quality_issues(meeting) is True


class TestNetworkQualityExtraction:
    def test_returns_none_for_empty_participants(self, connector):
        result = connector._extract_meeting_network_quality([])
        assert result is None

    def test_good_participants_produce_low_latency(self, connector):
        participants = [
            make_participant("Alice", "good"),
            make_participant("Bob", "good"),
        ]
        nq = connector._extract_meeting_network_quality(participants)
        assert nq is not None
        assert nq.destination_latency_ms == 50.0
        assert nq.destination_loss_percent == 0.0

    def test_bad_participants_produce_high_latency(self, connector):
        participants = [make_participant("Alice", "bad")]
        nq = connector._extract_meeting_network_quality(participants)
        assert nq is not None
        assert nq.destination_latency_ms == 300.0


class TestSeverityComputation:
    def test_ok_when_no_findings(self, connector):
        assert connector._compute_overall_severity([]) == Severity.OK

    def test_warning_takes_precedence_over_info(self, connector):
        from core.schema import Finding
        findings = [
            Finding(id="1", severity=Severity.INFO,
                    category=FindingCategory.COLLABORATION,
                    title="i", description="", resolution=""),
            Finding(id="2", severity=Severity.WARNING,
                    category=FindingCategory.COLLABORATION,
                    title="w", description="", resolution=""),
        ]
        assert connector._compute_overall_severity(findings) == Severity.WARNING


class TestConnectorInit:
    def test_raises_auth_error_without_credentials(self):
        import os
        keys = ["ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET"]
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            with pytest.raises(ConnectorAuthError):
                ZoomConnector(account_id="", client_id="", client_secret="")
        finally:
            for k, v in saved.items():
                if v:
                    os.environ[k] = v
