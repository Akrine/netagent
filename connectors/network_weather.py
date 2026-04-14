"""
connectors/network_weather.py

Network Weather Partner API connector.

Authenticates via OAuth2 client_credentials, fetches the latest
health snapshot for a given client ID, and normalizes it into
the framework's DiagnosticSnapshot schema.

API reference: https://www.networkweather.com/docs/partner/partner-api/
"""

from __future__ import annotations

import os
import time
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
    GatewayInfo,
    NetworkQuality,
    Severity,
    SystemHealth,
    WifiStatus,
)

_BASE_URL = "https://partner.networkweather.com"
_TOKEN_URL = f"{_BASE_URL}/oauth/token"

_SEVERITY_MAP: dict[str, Severity] = {
    "ok": Severity.OK,
    "info": Severity.INFO,
    "warning": Severity.WARNING,
    "critical": Severity.CRITICAL,
}

_CATEGORY_MAP: dict[str, FindingCategory] = {
    "wifi": FindingCategory.WIFI,
    "security": FindingCategory.SECURITY,
    "performance": FindingCategory.PERFORMANCE,
    "connectivity": FindingCategory.CONNECTIVITY,
    "gateway": FindingCategory.GATEWAY,
    "isp": FindingCategory.ISP,
    "vpn": FindingCategory.VPN,
    "system": FindingCategory.SYSTEM,
}


class NetworkWeatherConnector(BaseConnector):
    """
    Connector for the Network Weather Partner API.

    Credentials are read from environment variables:
      NWX_CLIENT_ID     - OAuth2 client ID
      NWX_CLIENT_SECRET - OAuth2 client secret

    Tokens are cached in memory and refreshed automatically
    before expiry to avoid unnecessary round-trips.
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: str = _BASE_URL,
    ) -> None:
        self._client_id = client_id or os.environ.get("NWX_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("NWX_CLIENT_SECRET", "")
        self._base_url = base_url.rstrip("/")

        if not self._client_id or not self._client_secret:
            raise ConnectorAuthError(
                "Network Weather credentials not provided. "
                "Set NWX_CLIENT_ID and NWX_CLIENT_SECRET environment variables."
            )

        self._token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._session = requests.Session()

    @property
    def name(self) -> str:
        return "network_weather"

    def health_check(self) -> bool:
        """Probe the partner API health endpoint (no auth required)."""
        try:
            resp = self._session.get(f"{self._base_url}/v1/health", timeout=5)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def fetch(self, device_id: str) -> DiagnosticSnapshot:
        """
        Fetch and normalize the latest health snapshot for a device.

        Parameters
        ----------
        device_id:
            Network Weather client ID (UUID format).
        """
        token = self._get_token()
        url = f"{self._base_url}/v1/devices/{device_id}/health"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            resp = self._session.get(url, headers=headers, timeout=10)
        except requests.RequestException as exc:
            raise ConnectorError(f"Request to Network Weather failed: {exc}") from exc

        if resp.status_code == 401:
            raise ConnectorAuthError("Network Weather token rejected (401).")
        if resp.status_code == 404:
            raise ConnectorNotFoundError(
                f"No health snapshot found for device '{device_id}'. "
                "The device may never have pushed data."
            )
        if not resp.ok:
            raise ConnectorError(
                f"Network Weather returned {resp.status_code}: {resp.text[:200]}"
            )

        raw = resp.json().get("data", {})
        return self._normalize(device_id, raw)

    def _get_token(self) -> str:
        """Return a valid access token, refreshing if within 60 s of expiry."""
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

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
                f"Token request to Network Weather failed: {exc}"
            ) from exc

        if not resp.ok:
            raise ConnectorAuthError(
                f"Network Weather authentication failed ({resp.status_code}): "
                f"{resp.text[:200]}"
            )

        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expiry = time.time() + payload.get("expires_in", 3600)
        return self._token

    def _normalize(self, device_id: str, raw: dict[str, Any]) -> DiagnosticSnapshot:
        """Map raw Network Weather health snapshot to DiagnosticSnapshot."""
        findings = self._extract_findings(raw)
        overall = self._compute_overall_severity(findings)

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id=device_id,
            captured_at=raw.get("capturedAt", ""),
            findings=findings,
            network_quality=self._extract_network_quality(raw),
            wifi=self._extract_wifi(raw),
            system=self._extract_system(raw),
            gateway=self._extract_gateway(raw),
            overall_severity=overall,
            raw=raw,
        )

    def _extract_findings(self, raw: dict[str, Any]) -> list[Finding]:
        findings = []
        for item in raw.get("findings", []):
            severity = _SEVERITY_MAP.get(
                item.get("severity", "").lower(), Severity.INFO
            )
            category = _CATEGORY_MAP.get(
                item.get("category", "").lower(), FindingCategory.UNKNOWN
            )
            findings.append(
                Finding(
                    id=item.get("id", ""),
                    severity=severity,
                    category=category,
                    title=item.get("technicalLabel", item.get("impactSummary", "")),
                    description=item.get("description", ""),
                    resolution=item.get("howToResolve", ""),
                    technical_detail=item.get("technicalDetails", ""),
                    is_auto_fixable=item.get("isAutoFixable", False),
                    source=item.get("source", ""),
                )
            )
        return findings

    def _extract_network_quality(
        self, raw: dict[str, Any]
    ) -> Optional[NetworkQuality]:
        nq = raw.get("networkQuality")
        if not nq:
            return None
        return NetworkQuality(
            gateway_latency_ms=nq.get("gatewayLatencyMs"),
            gateway_loss_percent=nq.get("gatewayLossPercent"),
            destination_latency_ms=nq.get("destinationLatencyMs"),
            destination_loss_percent=nq.get("destinationLossPercent"),
            destination_jitter_ms=nq.get("destinationJitterMs"),
        )

    def _extract_wifi(self, raw: dict[str, Any]) -> Optional[WifiStatus]:
        w = raw.get("wifi")
        if not w:
            return None
        return WifiStatus(
            ssid=w.get("ssid", ""),
            rssi_dbm=w.get("rssi"),
            channel=w.get("channel"),
            channel_width_mhz=w.get("channelWidth"),
            protocol=w.get("wifiProtocol", ""),
            security=w.get("security", ""),
            transmit_rate_mbps=w.get("transmitRate"),
        )

    def _extract_system(self, raw: dict[str, Any]) -> Optional[SystemHealth]:
        s = raw.get("system")
        if not s:
            return None
        return SystemHealth(
            cpu_percent=s.get("cpuUsagePercent"),
            memory_percent=s.get("memoryUsedPercent"),
            disk_percent=s.get("diskUsedPercent"),
            thermal_state=s.get("thermalState", ""),
            uptime_seconds=s.get("uptimeSeconds"),
            battery_percent=s.get("batteryChargePercent"),
        )

    def _extract_gateway(self, raw: dict[str, Any]) -> Optional[GatewayInfo]:
        g = raw.get("gateway")
        if not g:
            return None
        return GatewayInfo(
            vendor=g.get("vendor", ""),
            model=g.get("model", ""),
            management_reachable=g.get("managementReachable", False),
            supports_integration=g.get("supportsIntegration", False),
            web_admin_url=g.get("webAdminURL", ""),
        )

    @staticmethod
    def _compute_overall_severity(findings: list[Finding]) -> Severity:
        if any(f.severity == Severity.CRITICAL for f in findings):
            return Severity.CRITICAL
        if any(f.severity == Severity.WARNING for f in findings):
            return Severity.WARNING
        if any(f.severity == Severity.INFO for f in findings):
            return Severity.INFO
        return Severity.OK
