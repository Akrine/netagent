"""
tests/connectors/test_network_weather_fleet.py

Unit tests for the Network Weather fleet intelligence connector.
No API credentials or network access required.
"""

from __future__ import annotations

import pytest

from connectors.network_weather_fleet import NetworkWeatherFleetConnector
from connectors.base import ConnectorAuthError
from core.schema import FindingCategory, Severity


@pytest.fixture
def connector() -> NetworkWeatherFleetConnector:
    c = NetworkWeatherFleetConnector.__new__(NetworkWeatherFleetConnector)
    c._client_id = "test"
    c._client_secret = "test"
    c._token = "test_token"
    c._token_expiry = float("inf")
    c._msp_id = "msp_test"
    import requests
    c._session = requests.Session()
    return c


def make_device(
    status: str = "healthy",
    connection_state: str = "streaming",
    is_current: bool = True,
    latency_ms: float = None,
    quality_status: str = "good",
) -> dict:
    d = {
        "clientId": "device-1",
        "status": status,
        "connectionState": connection_state,
        "isCurrentVersion": is_current,
        "lastSeen": "2026-04-18T00:00:00Z",
    }
    if latency_ms is not None:
        d["networkQuality"] = {
            "latencyMs": latency_ms,
            "status": quality_status,
        }
    return d


def make_org(name: str, health: str = "healthy") -> dict:
    return {
        "orgId": f"org_{name}",
        "name": name,
        "overallHealth": health,
        "stats": {"total": 10, "healthy": 8, "warning": 1, "critical": 1},
    }


class TestFleetHealthAnalysis:
    def test_no_findings_for_healthy_fleet(self, connector):
        devices = [make_device() for _ in range(10)]
        findings = connector._analyze_fleet_health(devices, [])
        critical = [f for f in findings if f.severity == Severity.CRITICAL]
        assert len(critical) == 0

    def test_warning_for_some_critical_devices(self, connector):
        devices = [make_device()] * 8 + [make_device(status="critical")] * 2
        findings = connector._analyze_fleet_health(devices, [])
        warning = [f for f in findings if f.severity == Severity.WARNING
                   and "offline" in f.title.lower() or "critical" in f.title.lower()]
        assert len(warning) >= 1

    def test_critical_finding_when_20_percent_critical(self, connector):
        devices = [make_device()] * 8 + [make_device(status="critical")] * 2
        findings = connector._analyze_fleet_health(devices, [])
        severities = {f.severity for f in findings}
        assert Severity.WARNING in severities or Severity.CRITICAL in severities

    def test_outdated_devices_generate_info_finding(self, connector):
        devices = [make_device(is_current=False)] * 3 + [make_device()] * 7
        findings = connector._analyze_fleet_health(devices, [])
        info = [f for f in findings if "outdated" in f.title.lower()]
        assert len(info) == 1
        assert info[0].severity == Severity.INFO

    def test_poor_quality_devices_generate_finding(self, connector):
        devices = (
            [make_device(latency_ms=50, quality_status="good")] * 8 +
            [make_device(latency_ms=400, quality_status="poor")] * 2
        )
        findings = connector._analyze_fleet_health(devices, [])
        quality = [f for f in findings if "quality" in f.title.lower()]
        assert len(quality) == 1

    def test_empty_fleet_returns_no_findings(self, connector):
        findings = connector._analyze_fleet_health([], [])
        assert findings == []


class TestOrgHealthAnalysis:
    def test_critical_org_generates_critical_finding(self, connector):
        orgs = [make_org("Acme", "critical"), make_org("Beta", "healthy")]
        findings = connector._analyze_org_health(orgs)
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL
        assert "Acme" in findings[0].technical_detail

    def test_healthy_orgs_no_findings(self, connector):
        orgs = [make_org("Acme", "healthy"), make_org("Beta", "healthy")]
        findings = connector._analyze_org_health(orgs)
        assert findings == []


class TestLatencyComputation:
    def test_computes_average_latency(self, connector):
        devices = [
            make_device(latency_ms=100),
            make_device(latency_ms=200),
        ]
        avg = connector._compute_avg_latency(devices)
        assert avg == 150.0

    def test_returns_none_when_no_latency_data(self, connector):
        devices = [make_device()]
        avg = connector._compute_avg_latency(devices)
        assert avg is None


class TestSeverityComputation:
    def test_ok_when_no_findings(self, connector):
        assert connector._compute_overall_severity([]) == Severity.OK

    def test_critical_takes_precedence(self, connector):
        from core.schema import Finding
        findings = [
            Finding(id="1", severity=Severity.WARNING,
                    category=FindingCategory.CONNECTIVITY,
                    title="w", description="", resolution=""),
            Finding(id="2", severity=Severity.CRITICAL,
                    category=FindingCategory.CONNECTIVITY,
                    title="c", description="", resolution=""),
        ]
        assert connector._compute_overall_severity(findings) == Severity.CRITICAL


class TestConnectorInit:
    def test_raises_auth_error_without_credentials(self):
        import os
        keys = ["NWX_CLIENT_ID", "NWX_CLIENT_SECRET"]
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            with pytest.raises(ConnectorAuthError):
                NetworkWeatherFleetConnector(client_id="", client_secret="")
        finally:
            for k, v in saved.items():
                if v:
                    os.environ[k] = v

    def test_connector_name(self, connector):
        assert connector.name == "network_weather_fleet"
