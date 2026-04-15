"""
tests/connectors/test_system_health.py

Unit tests for the system health connector.

Tests verify that findings are generated correctly at each threshold,
severity computation is accurate, and the connector reads live data
without errors.
"""

from __future__ import annotations

import pytest

from connectors.system_health import SystemHealthConnector
from core.schema import FindingCategory, Severity


@pytest.fixture
def connector() -> SystemHealthConnector:
    return SystemHealthConnector()


class TestFindingGeneration:
    def test_no_findings_when_healthy(self, connector):
        cpu = {"percent": 10.0, "count_logical": 8, "count_physical": 4}
        memory = {"percent": 40.0, "used_gb": 8.0, "total_gb": 16.0, "swap_used_gb": 0.0}
        disk = {"percent": 30.0, "used_gb": 100.0, "total_gb": 500.0, "free_gb": 400.0}
        battery = {"percent": 80, "plugged_in": True}
        findings = connector._generate_findings(cpu, memory, disk, battery, None)
        assert findings == []

    def test_cpu_warning_finding(self, connector):
        cpu = {"percent": 85.0, "count_logical": 8, "count_physical": 4}
        findings = connector._cpu_findings(cpu)
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert findings[0].category == FindingCategory.SYSTEM
        assert "85.0%" in findings[0].description

    def test_cpu_critical_finding(self, connector):
        cpu = {"percent": 97.0, "count_logical": 8, "count_physical": 4}
        findings = connector._cpu_findings(cpu)
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_memory_warning_finding(self, connector):
        memory = {"percent": 82.0, "used_gb": 13.0, "total_gb": 16.0, "swap_used_gb": 0.5}
        findings = connector._memory_findings(memory)
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert "82.0%" in findings[0].description

    def test_memory_critical_finding(self, connector):
        memory = {"percent": 96.0, "used_gb": 15.5, "total_gb": 16.0, "swap_used_gb": 2.0}
        findings = connector._memory_findings(memory)
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_disk_warning_finding(self, connector):
        disk = {"percent": 87.0, "used_gb": 435.0, "total_gb": 500.0, "free_gb": 65.0}
        findings = connector._disk_findings(disk)
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_disk_critical_finding(self, connector):
        disk = {"percent": 96.0, "used_gb": 480.0, "total_gb": 500.0, "free_gb": 20.0}
        findings = connector._disk_findings(disk)
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_battery_warning_when_unplugged_low(self, connector):
        battery = {"percent": 15, "plugged_in": False}
        findings = connector._battery_findings(battery)
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING

    def test_battery_critical_when_unplugged_very_low(self, connector):
        battery = {"percent": 5, "plugged_in": False}
        findings = connector._battery_findings(battery)
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL

    def test_no_battery_finding_when_plugged_in(self, connector):
        battery = {"percent": 5, "plugged_in": True}
        findings = connector._battery_findings(battery)
        assert findings == []

    def test_no_battery_finding_when_no_battery(self, connector):
        findings = connector._battery_findings({})
        assert findings == []


class TestSeverityComputation:
    def test_ok_when_no_findings(self, connector):
        assert connector._compute_overall_severity([]) == Severity.OK

    def test_critical_takes_precedence_over_warning(self, connector):
        from core.schema import Finding
        findings = [
            Finding(id="1", severity=Severity.WARNING, category=FindingCategory.SYSTEM,
                    title="w", description="", resolution=""),
            Finding(id="2", severity=Severity.CRITICAL, category=FindingCategory.SYSTEM,
                    title="c", description="", resolution=""),
        ]
        assert connector._compute_overall_severity(findings) == Severity.CRITICAL

    def test_warning_when_no_critical(self, connector):
        from core.schema import Finding
        findings = [
            Finding(id="1", severity=Severity.WARNING, category=FindingCategory.SYSTEM,
                    title="w", description="", resolution=""),
        ]
        assert connector._compute_overall_severity(findings) == Severity.WARNING


class TestLiveFetch:
    def test_fetch_returns_valid_snapshot(self, connector):
        snapshot = connector.fetch("local")
        assert snapshot.source_connector == "system_health"
        assert snapshot.device_id == "local"
        assert snapshot.captured_at != ""
        assert snapshot.system is not None
        assert snapshot.system.cpu_percent is not None
        assert snapshot.system.memory_percent is not None
        assert snapshot.system.disk_percent is not None
        assert 0 <= snapshot.system.cpu_percent <= 100
        assert 0 <= snapshot.system.memory_percent <= 100
        assert 0 <= snapshot.system.disk_percent <= 100

    def test_health_check_passes(self, connector):
        assert connector.health_check() is True
