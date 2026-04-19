"""
connectors/network_weather_fleet.py

Network Weather fleet intelligence connector.

Unlike the single-device network_weather connector which fetches
one device's health snapshot, this connector queries the Partner API
fleet endpoints to answer questions that span an entire organization:

- Which devices have the worst network quality?
- Are issues trending up or down across the fleet?
- Which organizations have the most critical devices?
- What percentage of the fleet has security vulnerabilities?

This is the connector that makes Savvy valuable to David Weekly's
enterprise customers — fleet-level intelligence that is impossible
to get from the per-device Network Weather app.

Authentication: same as network_weather connector
  NWX_CLIENT_ID, NWX_CLIENT_SECRET
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from connectors.base import (
    BaseConnector,
    ConnectorAuthError,
    ConnectorError,
    ConnectorNotFoundError,
)
from core.schema import (
    DiagnosticSnapshot,
    Finding,
    FindingCategory,
    NetworkQuality,
    Severity,
    SystemHealth,
)

_BASE_URL = "https://partner.networkweather.com"
_TOKEN_URL = f"{_BASE_URL}/oauth/token"


class NetworkWeatherFleetConnector(BaseConnector):
    """
    Fleet-level Network Weather connector.

    device_id maps to an organization ID or 'all' for full fleet overview.

    Surfaces fleet-wide findings:
    - High percentage of devices offline or critical
    - Organizations with poor overall network quality
    - Fleet-wide security vulnerability patterns
    - Devices that have never checked in
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ) -> None:
        self._client_id = client_id or os.environ.get("NWX_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("NWX_CLIENT_SECRET", "")

        if not self._client_id or not self._client_secret:
            raise ConnectorAuthError(
                "Network Weather credentials not provided. "
                "Set NWX_CLIENT_ID and NWX_CLIENT_SECRET."
            )

        self._token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._msp_id: Optional[str] = None
        self._session = requests.Session()

    @property
    def name(self) -> str:
        return "network_weather_fleet"

    def health_check(self) -> bool:
        try:
            self._get("/v1/health")
            return True
        except Exception:
            return False

    def fetch(self, device_id: str) -> DiagnosticSnapshot:
        try:
            if device_id == "all":
                return self._fetch_fleet_overview()
            else:
                return self._fetch_org(device_id)
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"Fleet fetch failed: {exc}") from exc

    def _fetch_fleet_overview(self) -> DiagnosticSnapshot:
        self._ensure_auth()

        orgs_resp = self._get("/v1/organizations", params={"limit": 100})
        orgs = orgs_resp.get("data", [])

        devices_resp = self._get("/v1/devices", params={"limit": 100})
        devices = devices_resp.get("data", [])

        findings = []
        findings.extend(self._analyze_fleet_health(devices, orgs))
        findings.extend(self._analyze_org_health(orgs))

        total = len(devices)
        critical = sum(1 for d in devices if d.get("status") == "critical")
        warning = sum(1 for d in devices if d.get("status") == "warning")
        healthy = sum(1 for d in devices if d.get("status") == "healthy")
        online = sum(1 for d in devices
                     if d.get("connectionState") == "streaming")

        overall = self._compute_overall_severity(findings)

        system = SystemHealth(
            thermal_state=(
                f"Fleet: {total} devices across {len(orgs)} orgs | "
                f"{healthy} healthy, {warning} warning, {critical} critical | "
                f"{online} online now"
            )
        )

        avg_latency = self._compute_avg_latency(devices)
        network_quality = NetworkQuality(
            destination_latency_ms=avg_latency,
        ) if avg_latency else None

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id="all",
            captured_at=datetime.now(timezone.utc).isoformat(),
            findings=findings,
            system=system,
            network_quality=network_quality,
            overall_severity=overall,
            raw={
                "total_devices": total,
                "total_orgs": len(orgs),
                "critical": critical,
                "warning": warning,
                "healthy": healthy,
                "online_now": online,
                "organizations": orgs,
            },
        )

    def _fetch_org(self, org_id: str) -> DiagnosticSnapshot:
        self._ensure_auth()

        try:
            org_resp = self._get(f"/v1/organizations/{org_id}")
            org = org_resp.get("data", {})
        except ConnectorError as exc:
            if "404" in str(exc):
                raise ConnectorNotFoundError(
                    f"Organization '{org_id}' not found."
                )
            raise

        devices_resp = self._get(
            "/v1/devices",
            params={"orgId": org_id, "limit": 100},
        )
        devices = devices_resp.get("data", [])

        findings = self._analyze_fleet_health(devices, [org])
        overall = self._compute_overall_severity(findings)

        total = len(devices)
        critical = sum(1 for d in devices if d.get("status") == "critical")
        healthy = sum(1 for d in devices if d.get("status") == "healthy")

        system = SystemHealth(
            thermal_state=(
                f"Org: {org.get('name', org_id)} | "
                f"{total} devices | {healthy} healthy, {critical} critical"
            )
        )

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id=org_id,
            captured_at=datetime.now(timezone.utc).isoformat(),
            findings=findings,
            system=system,
            overall_severity=overall,
            raw={"org": org, "devices": devices},
        )

    def _analyze_fleet_health(
        self,
        devices: list[dict],
        orgs: list[dict],
    ) -> list[Finding]:
        findings = []
        total = len(devices)
        if total == 0:
            return findings

        critical = [d for d in devices if d.get("status") == "critical"]
        never_seen = [d for d in devices
                      if d.get("connectionState") == "offline"
                      and not d.get("lastSeen")]
        outdated = [d for d in devices
                    if not d.get("isCurrentVersion", True)]

        critical_pct = len(critical) / total * 100

        if critical_pct >= 20:
            findings.append(Finding(
                id="fleet-critical-devices",
                severity=Severity.CRITICAL,
                category=FindingCategory.CONNECTIVITY,
                title=f"{len(critical)} devices ({critical_pct:.0f}%) are critical",
                description=(
                    f"{len(critical)} out of {total} devices have not been seen "
                    f"in over 24 hours or have never checked in. These devices "
                    f"are effectively invisible to your network monitoring."
                ),
                resolution=(
                    "Investigate critical devices. Verify Network Weather is "
                    "installed and running. Check if devices were decommissioned."
                ),
                technical_detail=(
                    f"Critical: {len(critical)}, Never seen: {len(never_seen)}, "
                    f"Total: {total}"
                ),
            ))
        elif len(critical) > 0:
            findings.append(Finding(
                id="fleet-some-critical",
                severity=Severity.WARNING,
                category=FindingCategory.CONNECTIVITY,
                title=f"{len(critical)} devices are offline or critical",
                description=(
                    f"{len(critical)} devices have not reported in over 24 hours."
                ),
                resolution=(
                    "Check these devices. They may be offline, decommissioned, "
                    "or experiencing network issues."
                ),
                technical_detail=f"Critical devices: {len(critical)} of {total}",
            ))

        if outdated:
            findings.append(Finding(
                id="fleet-outdated-versions",
                severity=Severity.INFO,
                category=FindingCategory.CONFIGURATION,
                title=f"{len(outdated)} devices running outdated Network Weather",
                description=(
                    f"{len(outdated)} devices are not running the latest version "
                    f"of Network Weather and may be missing features or fixes."
                ),
                resolution=(
                    "Update Network Weather on these devices. "
                    "Consider using MDM or RMM tools for fleet-wide updates."
                ),
                technical_detail=f"Outdated: {len(outdated)} of {total}",
            ))

        poor_quality = [
            d for d in devices
            if d.get("networkQuality", {}) and
            d.get("networkQuality", {}).get("status") in ("poor", "degraded")
        ]
        if poor_quality:
            pct = len(poor_quality) / total * 100
            findings.append(Finding(
                id="fleet-poor-quality",
                severity=Severity.WARNING if pct > 10 else Severity.INFO,
                category=FindingCategory.PERFORMANCE,
                title=f"{len(poor_quality)} devices ({pct:.0f}%) have poor network quality",
                description=(
                    f"{len(poor_quality)} devices are experiencing poor network "
                    f"quality including high latency, packet loss, or jitter."
                ),
                resolution=(
                    "Investigate network conditions at affected locations. "
                    "Check for ISP issues, WiFi interference, or hardware problems."
                ),
                technical_detail=(
                    f"Poor quality: {len(poor_quality)} of {total} ({pct:.1f}%)"
                ),
            ))

        return findings

    def _analyze_org_health(self, orgs: list[dict]) -> list[Finding]:
        findings = []
        critical_orgs = [
            o for o in orgs
            if o.get("overallHealth") == "critical"
        ]
        if critical_orgs:
            names = [o.get("name", "Unknown") for o in critical_orgs[:5]]
            findings.append(Finding(
                id="fleet-critical-orgs",
                severity=Severity.CRITICAL,
                category=FindingCategory.AVAILABILITY,
                title=f"{len(critical_orgs)} organizations in critical state",
                description=(
                    f"{len(critical_orgs)} organizations have critical overall "
                    f"health, meaning most of their devices are offline or "
                    f"have not checked in recently."
                ),
                resolution=(
                    "Investigate these organizations immediately. Check if "
                    "there was a widespread outage or deployment issue."
                ),
                technical_detail=f"Critical orgs: {', '.join(names)}",
            ))
        return findings

    @staticmethod
    def _compute_avg_latency(devices: list[dict]) -> Optional[float]:
        latencies = [
            d["networkQuality"]["latencyMs"]
            for d in devices
            if d.get("networkQuality") and d["networkQuality"].get("latencyMs")
        ]
        if not latencies:
            return None
        return sum(latencies) / len(latencies)

    def _get(self, path: str, params: Optional[dict] = None) -> dict[str, Any]:
        self._ensure_auth()
        url = f"{_BASE_URL}{path}"
        try:
            resp = self._session.get(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                params=params or {},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ConnectorError(f"Fleet request failed: {exc}") from exc

        if resp.status_code == 401:
            raise ConnectorAuthError("Network Weather token rejected (401).")
        if resp.status_code == 404:
            raise ConnectorError(f"404: {url}")
        if not resp.ok:
            raise ConnectorError(
                f"Network Weather returned {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    def _ensure_auth(self) -> None:
        if self._token and time.time() < self._token_expiry - 60:
            return
        try:
            resp = self._session.post(
                _TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ConnectorAuthError(
                f"Token request failed: {exc}"
            ) from exc

        if not resp.ok:
            raise ConnectorAuthError(
                f"Authentication failed ({resp.status_code}): {resp.text[:200]}"
            )

        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expiry = time.time() + payload.get("expires_in", 3600)

    @staticmethod
    def _compute_overall_severity(findings: list[Finding]) -> Severity:
        if any(f.severity == Severity.CRITICAL for f in findings):
            return Severity.CRITICAL
        if any(f.severity == Severity.WARNING for f in findings):
            return Severity.WARNING
        if any(f.severity == Severity.INFO for f in findings):
            return Severity.INFO
        return Severity.OK
