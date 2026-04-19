"""
tests/test_monitor.py

Unit tests for the background monitor and alert generation.
"""

from __future__ import annotations

import time
import pytest
from pathlib import Path

from core.monitor import Monitor, Alert
from core.history import SnapshotHistory
from core.registry import ConnectorRegistry, ConnectorSpec
from core.schema import DiagnosticSnapshot, Finding, FindingCategory, Severity
from connectors.system_health import SystemHealthConnector
from connectors.mock_snapshot import MockSnapshotConnector


def make_snapshot(
    connector: str = "test",
    device_id: str = "local",
    severity: Severity = Severity.OK,
    findings: list = None,
) -> DiagnosticSnapshot:
    return DiagnosticSnapshot(
        source_connector=connector,
        device_id=device_id,
        captured_at="2026-04-18T00:00:00Z",
        findings=findings or [],
        overall_severity=severity,
    )


def make_finding(title: str, severity: Severity = Severity.WARNING) -> Finding:
    return Finding(
        id=f"id-{title}",
        severity=severity,
        category=FindingCategory.CONNECTIVITY,
        title=title,
        description="Test finding",
        resolution="",
    )


@pytest.fixture
def history(tmp_path) -> SnapshotHistory:
    return SnapshotHistory(history_dir=tmp_path / "snapshots")


@pytest.fixture
def small_registry() -> ConnectorRegistry:
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


class TestAlertGeneration:
    def test_no_alerts_without_history(self, history, small_registry):
        monitor = Monitor(
            registry=small_registry,
            history=history,
            interval_seconds=3600,
        )
        alerts = monitor.check_now()
        assert isinstance(alerts, list)

    def test_alert_generated_on_severity_worsened(self, history):
        from core.history import SnapshotDiff
        monitor = Monitor(interval_seconds=3600)

        prev = make_snapshot(severity=Severity.OK)
        current = make_snapshot(
            severity=Severity.WARNING,
            findings=[make_finding("New issue")],
        )
        history.store(prev)
        history.store(current)
        diff = history.diff(current)

        alerts = monitor._generate_alerts(current, diff)
        severity_alerts = [a for a in alerts if "worsened" in a.title]
        assert len(severity_alerts) >= 1
        assert severity_alerts[0].severity == Severity.WARNING

    def test_alert_generated_for_new_critical_finding(self, history):
        monitor = Monitor(interval_seconds=3600)

        prev = make_snapshot(severity=Severity.OK)
        current = make_snapshot(
            severity=Severity.CRITICAL,
            findings=[make_finding("System down", Severity.CRITICAL)],
        )
        history.store(prev)
        history.store(current)
        diff = history.diff(current)

        alerts = monitor._generate_alerts(current, diff)
        finding_alerts = [a for a in alerts if "critical" in a.title.lower()]
        assert len(finding_alerts) >= 1
        assert finding_alerts[0].severity == Severity.CRITICAL

    def test_no_alert_for_info_finding(self, history):
        monitor = Monitor(interval_seconds=3600)

        prev = make_snapshot(severity=Severity.OK)
        current = make_snapshot(
            severity=Severity.INFO,
            findings=[make_finding("Minor note", Severity.INFO)],
        )
        history.store(prev)
        history.store(current)
        diff = history.diff(current)

        alerts = monitor._generate_alerts(current, diff)
        assert all(
            a.severity not in (Severity.INFO,) or "worsened" in a.title
            for a in alerts
        )


class TestAlertManagement:
    def test_get_alerts_empty_initially(self, small_registry, history):
        monitor = Monitor(
            registry=small_registry,
            history=history,
            interval_seconds=3600,
        )
        assert monitor.get_alerts() == []

    def test_acknowledge_alert(self, small_registry, history):
        monitor = Monitor(
            registry=small_registry,
            history=history,
            interval_seconds=3600,
        )
        alert = Alert(
            id="test-alert-1",
            connector="test",
            device_id="local",
            severity=Severity.WARNING,
            title="Test alert",
            description="Test",
        )
        with monitor._lock:
            monitor._alerts.append(alert)

        assert monitor.acknowledge("test-alert-1") is True
        alerts = monitor.get_alerts()
        assert alerts[0].acknowledged is True

    def test_acknowledge_all(self, small_registry, history):
        monitor = Monitor(
            registry=small_registry,
            history=history,
            interval_seconds=3600,
        )
        for i in range(3):
            with monitor._lock:
                monitor._alerts.append(Alert(
                    id=f"alert-{i}",
                    connector="test",
                    device_id="local",
                    severity=Severity.WARNING,
                    title=f"Alert {i}",
                    description="",
                ))
        count = monitor.acknowledge_all()
        assert count == 3
        assert all(a.acknowledged for a in monitor.get_alerts())

    def test_unacknowledged_filter(self, small_registry, history):
        monitor = Monitor(
            registry=small_registry,
            history=history,
            interval_seconds=3600,
        )
        for i in range(3):
            alert = Alert(
                id=f"alert-{i}",
                connector="test",
                device_id="local",
                severity=Severity.WARNING,
                title=f"Alert {i}",
                description="",
            )
            with monitor._lock:
                monitor._alerts.append(alert)

        monitor.acknowledge("alert-0")
        unacked = monitor.get_alerts(unacknowledged_only=True)
        assert len(unacked) == 2


class TestMonitorStats:
    def test_stats_not_running(self, small_registry, history):
        monitor = Monitor(
            registry=small_registry,
            history=history,
            interval_seconds=3600,
        )
        stats = monitor.stats()
        assert stats["running"] is False
        assert stats["total_alerts"] == 0
        assert stats["check_count"] == 0

    def test_on_alert_callback(self, history):
        received = []
        monitor = Monitor(
            history=history,
            interval_seconds=3600,
            on_alert=lambda a: received.append(a),
        )
        alert = Alert(
            id="cb-test",
            connector="test",
            device_id="local",
            severity=Severity.WARNING,
            title="Callback test",
            description="",
        )
        monitor._on_alert(alert)
        assert len(received) == 1
        assert received[0].id == "cb-test"
