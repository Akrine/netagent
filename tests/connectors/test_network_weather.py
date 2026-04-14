"""
tests/connectors/test_network_weather.py

Unit tests for the Network Weather connector normalization logic.

These tests exercise the full normalization pipeline using a real
snapshot fixture captured from the Network Weather app. No API
credentials or network access are required.
"""

from __future__ import annotations

import pytest

from connectors.network_weather import NetworkWeatherConnector
from connectors.base import ConnectorAuthError
from core.schema import (
    FindingCategory,
    Severity,
)


REAL_SNAPSHOT_FIXTURE = {
    "capturedAt": "2026-04-14T02:17:22Z",
    "isOffline": False,
    "wifi": {
        "ssid": "SpectrumSetup-90",
        "bssid": "e4:c0:e2:6a:73:97",
        "rssi": -44,
        "noise": -93,
        "channel": 157,
        "channelWidth": 80,
        "wifiProtocol": "Wi-Fi 5 (802.11ac)",
        "transmitRate": 866.0,
        "security": "WPA2 Personal",
    },
    "gateway": {
        "vendor": "Sagemcom Ca",
        "model": "",
        "managementReachable": True,
        "supportsIntegration": False,
        "integrationConfigured": False,
        "webAdminURL": "https://192.168.1.1",
        "ouiVendor": "Sagemcom Broadband SAS",
        "detectionMethod": "rDNS",
    },
    "system": {
        "cpuUsagePercent": 16.2,
        "memoryUsedPercent": 69.7,
        "diskUsedPercent": 28.3,
        "thermalState": "Nominal",
        "uptimeSeconds": 890761,
        "batteryChargePercent": 80,
        "isLowPowerMode": False,
    },
    "networkQuality": {
        "gatewayLatencyMs": 7.6,
        "gatewayLossPercent": 0.0,
        "destinationLatencyMs": 39.7,
        "destinationLossPercent": 0.0,
        "destinationJitterMs": 11.0,
    },
    "findings": [
        {
            "id": "F6C1C27B-2AD1-4A3C-BBA8-AAE22AF0A742",
            "severity": "warning",
            "category": "wifi",
            "technicalLabel": "Connection dropouts",
            "impactSummary": "220 periods where your internet froze",
            "description": (
                "Your Mac recorded 220 times when this network appeared connected "
                "but nothing was actually getting through."
            ),
            "howToResolve": (
                "Try restarting your router. If it keeps happening, it could be "
                "WiFi interference, a flaky cable, or an ISP issue."
            ),
            "technicalDetails": "RTT avg: 69.8ms, Conn success: 3092/3248",
            "isAutoFixable": False,
            "source": "Scan",
            "archived": False,
            "resolved": False,
        },
        {
            "id": "AEB41ECA-69E5-4531-AB4B-AE68DA673C7B",
            "severity": "info",
            "category": "security",
            "technicalLabel": "WPA2 only",
            "impactSummary": "WPA2 only",
            "description": (
                "Your network uses WPA2, which is adequate but consider upgrading "
                "to WPA3 for stronger protection."
            ),
            "howToResolve": "",
            "technicalDetails": "BSSIDs: e4:c0:e2:6a:73:97",
            "isAutoFixable": False,
            "source": "WiFi Scan",
            "archived": False,
            "resolved": False,
        },
        {
            "id": "FD7D2E74-C3BB-483C-BD4C-1E5E429F8DCD",
            "severity": "info",
            "category": "security",
            "technicalLabel": "No management frame protection",
            "impactSummary": "Your WiFi could be stronger against attacks",
            "description": (
                "Your AP does not support 802.11w protected management frames."
            ),
            "howToResolve": (
                "Enable PMF/802.11w in your router's advanced wireless settings."
            ),
            "technicalDetails": "BSSIDs: e4:c0:e2:6a:73:97",
            "isAutoFixable": False,
            "source": "WiFi Scan",
            "archived": False,
            "resolved": False,
        },
    ],
    "metadata": {
        "computerName": "Alius202602b",
        "modelIdentifier": "Mac16,8",
        "chipName": "Apple M4 Pro",
        "serialNumber": "G41VT696FK",
        "version": "1.2.2",
        "build": "97",
        "appName": "Network Weather",
        "generatedAt": "2026-04-14T02:17:22Z",
    },
}


@pytest.fixture
def connector() -> NetworkWeatherConnector:
    return NetworkWeatherConnector(
        client_id="test_id",
        client_secret="test_secret",
    )


class TestNormalization:
    def test_snapshot_source_and_device(self, connector):
        snapshot = connector._normalize("device-abc", REAL_SNAPSHOT_FIXTURE)
        assert snapshot.source_connector == "network_weather"
        assert snapshot.device_id == "device-abc"
        assert snapshot.captured_at == "2026-04-14T02:17:22Z"

    def test_overall_severity_reflects_warning(self, connector):
        snapshot = connector._normalize("device-abc", REAL_SNAPSHOT_FIXTURE)
        assert snapshot.overall_severity == Severity.WARNING

    def test_finding_count(self, connector):
        snapshot = connector._normalize("device-abc", REAL_SNAPSHOT_FIXTURE)
        assert len(snapshot.findings) == 3

    def test_warning_finding_properties(self, connector):
        snapshot = connector._normalize("device-abc", REAL_SNAPSHOT_FIXTURE)
        warning = next(
            f for f in snapshot.findings if f.severity == Severity.WARNING
        )
        assert warning.category == FindingCategory.WIFI
        assert warning.title == "Connection dropouts"
        assert warning.is_auto_fixable is False
        assert "router" in warning.resolution.lower()

    def test_security_findings_categorized(self, connector):
        snapshot = connector._normalize("device-abc", REAL_SNAPSHOT_FIXTURE)
        security = [
            f for f in snapshot.findings
            if f.category == FindingCategory.SECURITY
        ]
        assert len(security) == 2

    def test_wifi_normalization(self, connector):
        snapshot = connector._normalize("device-abc", REAL_SNAPSHOT_FIXTURE)
        assert snapshot.wifi is not None
        assert snapshot.wifi.ssid == "SpectrumSetup-90"
        assert snapshot.wifi.rssi_dbm == -44
        assert snapshot.wifi.channel == 157
        assert snapshot.wifi.channel_width_mhz == 80

    def test_network_quality_normalization(self, connector):
        snapshot = connector._normalize("device-abc", REAL_SNAPSHOT_FIXTURE)
        assert snapshot.network_quality is not None
        assert snapshot.network_quality.gateway_latency_ms == 7.6
        assert snapshot.network_quality.destination_latency_ms == 39.7
        assert snapshot.network_quality.destination_jitter_ms == 11.0

    def test_system_health_normalization(self, connector):
        snapshot = connector._normalize("device-abc", REAL_SNAPSHOT_FIXTURE)
        assert snapshot.system is not None
        assert snapshot.system.cpu_percent == 16.2
        assert snapshot.system.memory_percent == 69.7
        assert snapshot.system.battery_percent == 80

    def test_gateway_normalization(self, connector):
        snapshot = connector._normalize("device-abc", REAL_SNAPSHOT_FIXTURE)
        assert snapshot.gateway is not None
        assert snapshot.gateway.vendor == "Sagemcom Ca"
        assert snapshot.gateway.management_reachable is True

    def test_has_issues_true_when_warning_present(self, connector):
        snapshot = connector._normalize("device-abc", REAL_SNAPSHOT_FIXTURE)
        assert snapshot.has_issues() is True

    def test_findings_by_severity(self, connector):
        snapshot = connector._normalize("device-abc", REAL_SNAPSHOT_FIXTURE)
        warnings = snapshot.findings_by_severity(Severity.WARNING)
        infos = snapshot.findings_by_severity(Severity.INFO)
        assert len(warnings) == 1
        assert len(infos) == 2

    def test_empty_findings_snapshot(self, connector):
        raw = {**REAL_SNAPSHOT_FIXTURE, "findings": []}
        snapshot = connector._normalize("device-abc", raw)
        assert snapshot.overall_severity == Severity.OK
        assert snapshot.has_issues() is False

    def test_missing_optional_fields(self, connector):
        raw = {
            "capturedAt": "2026-04-14T00:00:00Z",
            "findings": [],
        }
        snapshot = connector._normalize("device-abc", raw)
        assert snapshot.wifi is None
        assert snapshot.system is None
        assert snapshot.gateway is None
        assert snapshot.network_quality is None


class TestConnectorInit:
    def test_raises_auth_error_without_credentials(self):
        import os
        old_id = os.environ.pop("NWX_CLIENT_ID", None)
        old_secret = os.environ.pop("NWX_CLIENT_SECRET", None)
        try:
            with pytest.raises(ConnectorAuthError):
                NetworkWeatherConnector(client_id="", client_secret="")
        finally:
            if old_id:
                os.environ["NWX_CLIENT_ID"] = old_id
            if old_secret:
                os.environ["NWX_CLIENT_SECRET"] = old_secret
