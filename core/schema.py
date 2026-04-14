"""
core/schema.py

Normalized data models that all connectors must produce.
The agent reasoning layer operates exclusively on these models,
never on connector-specific raw data structures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class FindingCategory(str, Enum):
    WIFI = "wifi"
    SECURITY = "security"
    PERFORMANCE = "performance"
    CONNECTIVITY = "connectivity"
    GATEWAY = "gateway"
    ISP = "isp"
    VPN = "vpn"
    SYSTEM = "system"
    UNKNOWN = "unknown"


@dataclass
class Finding:
    """A single diagnosed issue surfaced by any connector."""
    id: str
    severity: Severity
    category: FindingCategory
    title: str
    description: str
    resolution: str
    technical_detail: str = ""
    is_auto_fixable: bool = False
    source: str = ""


@dataclass
class NetworkQuality:
    """Point-in-time network performance metrics."""
    gateway_latency_ms: Optional[float] = None
    gateway_loss_percent: Optional[float] = None
    destination_latency_ms: Optional[float] = None
    destination_loss_percent: Optional[float] = None
    destination_jitter_ms: Optional[float] = None


@dataclass
class WifiStatus:
    """Current WiFi connection details."""
    ssid: str = ""
    rssi_dbm: Optional[int] = None
    channel: Optional[int] = None
    channel_width_mhz: Optional[int] = None
    protocol: str = ""
    security: str = ""
    transmit_rate_mbps: Optional[float] = None


@dataclass
class SystemHealth:
    """Host machine resource utilization."""
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    disk_percent: Optional[float] = None
    thermal_state: str = ""
    uptime_seconds: Optional[int] = None
    battery_percent: Optional[int] = None


@dataclass
class GatewayInfo:
    """Details about the local network gateway."""
    vendor: str = ""
    model: str = ""
    management_reachable: bool = False
    supports_integration: bool = False
    web_admin_url: str = ""


@dataclass
class DiagnosticSnapshot:
    """
    The normalized output of any connector.

    This is the single data contract between connectors and the agent.
    All fields are optional to accommodate connectors with varying
    levels of data richness.
    """
    source_connector: str
    device_id: str
    captured_at: str
    findings: list[Finding] = field(default_factory=list)
    network_quality: Optional[NetworkQuality] = None
    wifi: Optional[WifiStatus] = None
    system: Optional[SystemHealth] = None
    gateway: Optional[GatewayInfo] = None
    overall_severity: Severity = Severity.OK
    raw: dict = field(default_factory=dict)

    def findings_by_severity(self, severity: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity == severity]

    def has_issues(self) -> bool:
        return any(
            f.severity in (Severity.WARNING, Severity.CRITICAL)
            for f in self.findings
        )
