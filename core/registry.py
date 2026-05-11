"""
core/registry.py

Connector registry with auto-discovery.

Instead of manually wiring connectors into the API, demo script,
and UI, connectors register themselves here. Adding a new connector
means creating one file and adding one registration call.

The registry handles:
- Connector registration with metadata
- Lazy instantiation (connectors are only created when needed)
- Availability checking (credentials present or not)
- Auto-discovery for the API and UI
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Type

from connectors.base import BaseConnector, ConnectorAuthError
from core.taxonomy import Category, Tag


@dataclass
class ConnectorSpec:
    """
    Metadata and factory for a single connector.

    name:             Unique identifier used in API calls and UI
    display_name:     Human-readable name shown in the UI
    description:      One-line description of what this connector monitors
    factory:          Callable that returns a BaseConnector instance
    requires_creds:   Whether this connector needs credentials to instantiate
    default_device_id: Default device_id to use when none is specified
    category:         Primary category — where this connector lives in the tree
    tags:             Context tags — where this connector surfaces in discovery
    """
    name: str
    display_name: str
    description: str
    factory: Callable[[], BaseConnector]
    requires_creds: bool = True
    default_device_id: str = "local"
    category: Category = Category.NETWORK
    tags: list[Tag] = field(default_factory=list)

    def matches_tag(self, tag: Tag) -> bool:
        return tag in self.tags

    def matches_any_tag(self, tags: list[Tag]) -> bool:
        return any(t in self.tags for t in tags)

    def to_dict(self) -> dict:
        from core.taxonomy import TAXONOMY
        domain = TAXONOMY.get_domain_for_category(self.category)
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "requires_creds": self.requires_creds,
            "category": self.category.value,
            "domain": domain.value,
            "tags": [t.value for t in self.tags],
        }


class ConnectorRegistry:
    """
    Central registry for all Savvy connectors.

    Connectors register themselves via register(). The registry
    provides lazy instantiation and availability checking so the
    API can report which connectors are available without failing
    on missing credentials.
    """

    def __init__(self) -> None:
        self._specs: dict[str, ConnectorSpec] = {}

    def register(self, spec: ConnectorSpec) -> None:
        """Register a connector spec."""
        self._specs[spec.name] = spec

    def get_spec(self, name: str) -> Optional[ConnectorSpec]:
        """Return the spec for a connector by name."""
        return self._specs.get(name)

    def all_specs(self) -> list[ConnectorSpec]:
        """Return all registered connector specs."""
        return list(self._specs.values())

    def available_names(self) -> list[str]:
        """
        Return names of connectors that can be instantiated.
        A connector is available if its factory does not raise
        ConnectorAuthError (i.e. credentials are present).
        """
        available = []
        for name, spec in self._specs.items():
            try:
                spec.factory()
                available.append(name)
            except ConnectorAuthError:
                pass
            except Exception:
                available.append(name)
        return available

    def get(self, name: str) -> BaseConnector:
        """
        Instantiate and return a connector by name.
        Raises KeyError if not registered.
        Raises ConnectorAuthError if credentials are missing.
        """
        spec = self._specs.get(name)
        if not spec:
            raise KeyError(
                f"Connector '{name}' not registered. "
                f"Available: {list(self._specs.keys())}"
            )
        return spec.factory()

    def fetch(self, name: str, device_id: Optional[str] = None) -> object:
        """
        Fetch a snapshot from a connector by name.
        Uses the connector's default_device_id if none provided.
        """
        spec = self._specs.get(name)
        if not spec:
            raise KeyError(f"Connector '{name}' not registered.")
        connector = spec.factory()
        did = device_id or spec.default_device_id
        return connector.fetch(did)

    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)


def build_default_registry() -> ConnectorRegistry:
    """
    Build and return the default Savvy connector registry.

    Adding a new connector to Savvy means adding one ConnectorSpec
    here. Nothing else needs to change.
    """
    from connectors.google_meet import GoogleMeetConnector
    from connectors.mock_fleet import MockFleetConnector
    from connectors.network_weather_fleet import NetworkWeatherFleetConnector
    from connectors.mock_snapshot import MockSnapshotConnector
    from connectors.monday_com import MondayConnector
    from connectors.salesforce import SalesforceConnector
    from connectors.system_health import SystemHealthConnector
    from connectors.zoom import ZoomConnector

    registry = ConnectorRegistry()

    from core.taxonomy import Category, Tag

    registry.register(ConnectorSpec(
        name="system_health",
        display_name="System Health",
        description="Live machine metrics — CPU, memory, disk, battery, network",
        factory=SystemHealthConnector,
        requires_creds=False,
        default_device_id="local",
        category=Category.COMPUTE,
        tags=[Tag.CPU, Tag.MEMORY, Tag.DISK, Tag.SYSTEM, Tag.PERFORMANCE, Tag.AVAILABILITY],
    ))

    registry.register(ConnectorSpec(
        name="mock_network_weather",
        display_name="Network Weather",
        description="Network diagnostics — WiFi, gateway, ISP, security findings",
        factory=lambda: MockSnapshotConnector("fixtures/my_network.json"),
        requires_creds=False,
        default_device_id="local-device",
        category=Category.NETWORK,
        tags=[Tag.NETWORK, Tag.WIFI, Tag.ISP, Tag.LATENCY, Tag.PACKET_LOSS, Tag.SECURITY],
    ))

    registry.register(ConnectorSpec(
        name="monday_com",
        display_name="Monday.com",
        description="Project health — stuck items, at-risk tasks, overdue deadlines",
        factory=MondayConnector,
        requires_creds=True,
        default_device_id="all",
        category=Category.PROJECT_MANAGEMENT,
        tags=[Tag.PROJECT, Tag.TASKS, Tag.AVAILABILITY],
    ))

    registry.register(ConnectorSpec(
        name="salesforce",
        display_name="Salesforce",
        description="CRM health — stalled deals, overdue cases, pipeline risk",
        factory=SalesforceConnector,
        requires_creds=True,
        default_device_id="all",
        category=Category.CRM,
        tags=[Tag.CRM, Tag.PIPELINE, Tag.AVAILABILITY],
    ))

    registry.register(ConnectorSpec(
        name="zoom",
        display_name="Zoom",
        description="Meeting quality — participant QoS, latency, audio/video health",
        factory=ZoomConnector,
        requires_creds=True,
        default_device_id="all",
        category=Category.CONFERENCING,
        tags=[Tag.CONFERENCING, Tag.VIDEO, Tag.AUDIO, Tag.CALL_QUALITY, Tag.NETWORK, Tag.LATENCY],
    ))

    registry.register(ConnectorSpec(
        name="google_meet",
        display_name="Google Meet",
        description="Conference health — participation rates, meeting patterns",
        factory=GoogleMeetConnector,
        requires_creds=True,
        default_device_id="all",
        category=Category.CONFERENCING,
        tags=[Tag.CONFERENCING, Tag.VIDEO, Tag.AUDIO, Tag.CALL_QUALITY],
    ))

    registry.register(ConnectorSpec(
        name="network_weather_fleet",
        display_name="Network Weather Fleet",
        description="Fleet intelligence — device health across all orgs and locations",
        factory=NetworkWeatherFleetConnector,
        requires_creds=True,
        default_device_id="all",
        category=Category.FLEET,
        tags=[Tag.FLEET, Tag.FLEET_NETWORK, Tag.MULTI_DEVICE, Tag.LOCATION, Tag.NETWORK],
    ))

    registry.register(ConnectorSpec(
        name="mock_fleet",
        display_name="Fleet Demo (Acme Corp)",
        description="Demo fleet — 50 devices across NYC, Chicago, London",
        factory=MockFleetConnector,
        requires_creds=False,
        default_device_id="all",
        category=Category.FLEET,
        tags=[Tag.FLEET, Tag.FLEET_NETWORK, Tag.MULTI_DEVICE, Tag.LOCATION, Tag.DEMO],
    ))

    from connectors.yuruna_diagnostics import YurunaDiagnosticsConnector
    registry.register(ConnectorSpec(
        name="yuruna_diagnostics",
        display_name="Yuruna Diagnostics",
        description="Yuruna test failure diagnostics — surfaces findings from Get-SystemDiagnostics output",
        factory=YurunaDiagnosticsConnector,
        requires_creds=False,
        default_device_id="local",
        category=Category.COMPUTE,
        tags=[Tag.SYSTEM, Tag.AVAILABILITY, Tag.PERFORMANCE],
    ))

    return registry


# Global registry instance
registry = build_default_registry()
