"""
tests/test_registry.py

Unit tests for the connector registry.
"""

from __future__ import annotations

import pytest

from core.registry import ConnectorRegistry, ConnectorSpec, build_default_registry
from connectors.base import ConnectorAuthError
from connectors.system_health import SystemHealthConnector
from connectors.mock_snapshot import MockSnapshotConnector


@pytest.fixture
def registry() -> ConnectorRegistry:
    r = ConnectorRegistry()
    r.register(ConnectorSpec(
        name="system_health",
        display_name="System Health",
        description="Live machine metrics",
        factory=SystemHealthConnector,
        requires_creds=False,
        default_device_id="local",
    ))
    r.register(ConnectorSpec(
        name="mock_network_weather",
        display_name="Network Weather",
        description="Network diagnostics",
        factory=lambda: MockSnapshotConnector("fixtures/my_network.json"),
        requires_creds=False,
        default_device_id="local-device",
    ))
    return r


class TestRegistration:
    def test_register_and_retrieve_spec(self, registry):
        spec = registry.get_spec("system_health")
        assert spec is not None
        assert spec.display_name == "System Health"
        assert spec.requires_creds is False

    def test_unknown_connector_returns_none(self, registry):
        assert registry.get_spec("nonexistent") is None

    def test_len_reflects_registered_count(self, registry):
        assert len(registry) == 2

    def test_contains_registered_connector(self, registry):
        assert "system_health" in registry
        assert "nonexistent" not in registry

    def test_all_specs_returns_all(self, registry):
        specs = registry.all_specs()
        assert len(specs) == 2
        names = {s.name for s in specs}
        assert "system_health" in names
        assert "mock_network_weather" in names


class TestInstantiation:
    def test_get_returns_connector_instance(self, registry):
        connector = registry.get("system_health")
        assert connector is not None
        assert connector.name == "system_health"

    def test_get_raises_key_error_for_unknown(self, registry):
        with pytest.raises(KeyError):
            registry.get("nonexistent")

    def test_fetch_returns_snapshot(self, registry):
        from core.schema import DiagnosticSnapshot
        snapshot = registry.fetch("system_health")
        assert isinstance(snapshot, DiagnosticSnapshot)
        assert snapshot.source_connector == "system_health"

    def test_fetch_uses_default_device_id(self, registry):
        from core.schema import DiagnosticSnapshot
        snapshot = registry.fetch("mock_network_weather")
        assert isinstance(snapshot, DiagnosticSnapshot)

    def test_fetch_with_custom_device_id(self, registry):
        from core.schema import DiagnosticSnapshot
        snapshot = registry.fetch("system_health", device_id="local")
        assert isinstance(snapshot, DiagnosticSnapshot)


class TestAvailability:
    def test_available_names_includes_no_cred_connectors(self, registry):
        available = registry.available_names()
        assert "system_health" in available
        assert "mock_network_weather" in available

    def test_unavailable_connector_excluded(self):
        r = ConnectorRegistry()
        r.register(ConnectorSpec(
            name="needs_creds",
            display_name="Needs Creds",
            description="Requires credentials",
            factory=lambda: (_ for _ in ()).throw(
                ConnectorAuthError("No credentials")
            ),
            requires_creds=True,
        ))
        available = r.available_names()
        assert "needs_creds" not in available


class TestDefaultRegistry:
    def test_default_registry_has_six_connectors(self):
        r = build_default_registry()
        assert len(r) == 6

    def test_default_registry_contains_all_connectors(self):
        r = build_default_registry()
        expected = {
            "system_health", "mock_network_weather", "monday_com",
            "salesforce", "zoom", "google_meet"
        }
        actual = {s.name for s in r.all_specs()}
        assert actual == expected

    def test_no_cred_connectors_available_by_default(self):
        r = build_default_registry()
        available = r.available_names()
        assert "system_health" in available
        assert "mock_network_weather" in available
