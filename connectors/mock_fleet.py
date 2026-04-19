"""
connectors/mock_fleet.py

Mock fleet connector for demo purposes.

Serves a realistic 50-device fleet scenario across 3 office locations
(New York, Chicago, London) without requiring live Network Weather
partner API credentials.

The scenario tells a story:
- New York: 6 devices offline, 6 degraded — ISP or router issue
- Chicago: 1 offline, 4 degraded — outdated firmware on 4 devices
- London: all 12 healthy — clean network, up to date

This is the fixture that enables a live fleet intelligence demo
for David Weekly without requiring real MSP credentials.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from connectors.base import BaseConnector, ConnectorNotFoundError
from connectors.network_weather_fleet import NetworkWeatherFleetConnector
from core.schema import DiagnosticSnapshot


class MockFleetConnector(BaseConnector):
    """
    Serves a pre-recorded fleet snapshot for offline demo.

    Uses the NetworkWeatherFleetConnector normalization logic so
    the output is identical to what the live fleet connector produces.
    """

    def __init__(
        self,
        fixture_path: str = "fixtures/mock_fleet.json",
    ) -> None:
        self._fixture_path = Path(fixture_path)
        with open(self._fixture_path) as f:
            self._data = json.load(f)
        self._normalizer = NetworkWeatherFleetConnector.__new__(
            NetworkWeatherFleetConnector
        )
        self._normalizer._client_id = "mock"
        self._normalizer._client_secret = "mock"
        self._normalizer._token = "mock"
        self._normalizer._token_expiry = float("inf")
        import requests
        self._normalizer._session = requests.Session()

    @property
    def name(self) -> str:
        return "mock_fleet"

    def fetch(self, device_id: str) -> DiagnosticSnapshot:
        devices = self._data.get("devices", [])
        orgs = self._data.get("organizations", [])

        if device_id == "all":
            findings = self._normalizer._analyze_fleet_health(devices, orgs)
            findings += self._normalizer._analyze_org_health(orgs)
            overall = self._normalizer._compute_overall_severity(findings)
            avg_latency = self._normalizer._compute_avg_latency(devices)

            from core.schema import NetworkQuality, SystemHealth
            total = len(devices)
            critical = sum(1 for d in devices if d.get("status") == "critical")
            warning = sum(1 for d in devices if d.get("status") == "warning")
            healthy = sum(1 for d in devices if d.get("status") == "healthy")
            online = sum(
                1 for d in devices if d.get("connectionState") == "streaming"
            )

            return DiagnosticSnapshot(
                source_connector=self.name,
                device_id="all",
                captured_at=datetime.now(timezone.utc).isoformat(),
                findings=findings,
                system=SystemHealth(
                    thermal_state=(
                        f"Fleet: {total} devices across {len(orgs)} orgs | "
                        f"{healthy} healthy, {warning} warning, {critical} critical | "
                        f"{online} online now"
                    )
                ),
                network_quality=NetworkQuality(
                    destination_latency_ms=avg_latency
                ) if avg_latency else None,
                overall_severity=overall,
                raw={"organizations": orgs, "devices": devices},
            )

        org = next((o for o in orgs if o["orgId"] == device_id), None)
        if not org:
            raise ConnectorNotFoundError(
                f"Organization '{device_id}' not found in mock fleet."
            )

        org_devices = [d for d in devices if d.get("orgId") == device_id]
        findings = self._normalizer._analyze_fleet_health(org_devices, [org])
        overall = self._normalizer._compute_overall_severity(findings)

        from core.schema import SystemHealth
        total = len(org_devices)
        critical = sum(1 for d in org_devices if d.get("status") == "critical")
        healthy = sum(1 for d in org_devices if d.get("status") == "healthy")

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id=device_id,
            captured_at=datetime.now(timezone.utc).isoformat(),
            findings=findings,
            system=SystemHealth(
                thermal_state=(
                    f"Org: {org.get('name')} | "
                    f"{total} devices | {healthy} healthy, {critical} critical"
                )
            ),
            overall_severity=overall,
            raw={"org": org, "devices": org_devices},
        )

    def health_check(self) -> bool:
        return self._fixture_path.exists()
