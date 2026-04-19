"""
tests/test_deduplication.py

Unit tests for finding deduplication and correlation.
"""

from __future__ import annotations

import pytest

from core.deduplication import FindingDeduplicator, deduplicate_snapshots
from core.schema import (
    DiagnosticSnapshot,
    Finding,
    FindingCategory,
    Severity,
)


def make_finding(
    id: str,
    title: str,
    severity: Severity = Severity.WARNING,
    category: FindingCategory = FindingCategory.CONNECTIVITY,
    technical_detail: str = "",
) -> Finding:
    return Finding(
        id=id,
        severity=severity,
        category=category,
        title=title,
        description="",
        resolution="",
        technical_detail=technical_detail,
    )


def make_snapshot(
    connector: str,
    findings: list[Finding],
) -> DiagnosticSnapshot:
    return DiagnosticSnapshot(
        source_connector=connector,
        device_id="test",
        captured_at="2026-04-18T00:00:00Z",
        findings=findings,
        overall_severity=Severity.WARNING if findings else Severity.OK,
    )


class TestDeduplication:
    def test_empty_snapshots_returns_empty(self):
        result = deduplicate_snapshots({})
        assert result == []

    def test_no_findings_returns_empty(self):
        snapshots = {
            "connector_a": make_snapshot("connector_a", []),
            "connector_b": make_snapshot("connector_b", []),
        }
        result = deduplicate_snapshots(snapshots)
        assert result == []

    def test_unique_findings_not_deduplicated(self):
        snapshots = {
            "network": make_snapshot("network", [
                make_finding("1", "Connection dropouts", Severity.WARNING,
                             FindingCategory.CONNECTIVITY),
            ]),
            "system": make_snapshot("system", [
                make_finding("2", "High CPU usage", Severity.WARNING,
                             FindingCategory.SYSTEM),
            ]),
        }
        result = deduplicate_snapshots(snapshots)
        assert len(result) == 2

    def test_duplicate_findings_correlated(self):
        snapshots = {
            "network": make_snapshot("network", [
                make_finding("1", "Connection dropouts detected",
                             Severity.WARNING, FindingCategory.CONNECTIVITY,
                             "RTT: 69ms"),
            ]),
            "system": make_snapshot("system", [
                make_finding("2", "Connection dropouts observed",
                             Severity.WARNING, FindingCategory.CONNECTIVITY,
                             "Loss: 5%"),
            ]),
        }
        result = deduplicate_snapshots(snapshots)
        assert len(result) == 1
        assert result[0].occurrence_count == 2
        assert result[0].is_cross_connector is True

    def test_different_severity_not_correlated(self):
        snapshots = {
            "a": make_snapshot("a", [
                make_finding("1", "Network latency high",
                             Severity.WARNING, FindingCategory.CONNECTIVITY),
            ]),
            "b": make_snapshot("b", [
                make_finding("2", "Network latency high",
                             Severity.CRITICAL, FindingCategory.CONNECTIVITY),
            ]),
        }
        result = deduplicate_snapshots(snapshots)
        assert len(result) == 2

    def test_different_category_not_correlated(self):
        snapshots = {
            "a": make_snapshot("a", [
                make_finding("1", "Security vulnerability",
                             Severity.WARNING, FindingCategory.SECURITY),
            ]),
            "b": make_snapshot("b", [
                make_finding("2", "Security vulnerability",
                             Severity.WARNING, FindingCategory.CONNECTIVITY),
            ]),
        }
        result = deduplicate_snapshots(snapshots)
        assert len(result) == 2

    def test_most_informative_kept_as_primary(self):
        snapshots = {
            "a": make_snapshot("a", [
                make_finding("1", "Connection dropouts",
                             Severity.WARNING, FindingCategory.CONNECTIVITY,
                             technical_detail="short"),
            ]),
            "b": make_snapshot("b", [
                make_finding("2", "Connection dropouts detected",
                             Severity.WARNING, FindingCategory.CONNECTIVITY,
                             technical_detail="much longer technical detail here"),
            ]),
        }
        result = deduplicate_snapshots(snapshots)
        assert len(result) == 1
        assert result[0].primary.technical_detail == "much longer technical detail here"

    def test_sorted_by_severity_then_occurrence(self):
        snapshots = {
            "a": make_snapshot("a", [
                make_finding("1", "Minor info", Severity.INFO,
                             FindingCategory.SYSTEM),
                make_finding("2", "Critical failure", Severity.CRITICAL,
                             FindingCategory.CONNECTIVITY),
                make_finding("3", "Warning issue", Severity.WARNING,
                             FindingCategory.PERFORMANCE),
            ]),
        }
        result = deduplicate_snapshots(snapshots)
        assert result[0].primary.severity == Severity.CRITICAL
        assert result[1].primary.severity == Severity.WARNING
        assert result[2].primary.severity == Severity.INFO

    def test_cross_connector_flag(self):
        snapshots = {
            "connector_a": make_snapshot("connector_a", [
                make_finding("1", "Latency spike",
                             Severity.WARNING, FindingCategory.CONNECTIVITY),
            ]),
            "connector_b": make_snapshot("connector_b", [
                make_finding("2", "Latency spike detected",
                             Severity.WARNING, FindingCategory.CONNECTIVITY),
            ]),
        }
        result = deduplicate_snapshots(snapshots)
        assert len(result) == 1
        assert result[0].is_cross_connector is True
        assert len(set(result[0].connectors)) == 2

    def test_same_connector_duplicate_not_cross_connector(self):
        snapshots = {
            "connector_a": make_snapshot("connector_a", [
                make_finding("1", "Latency spike",
                             Severity.WARNING, FindingCategory.CONNECTIVITY),
                make_finding("2", "Latency spike detected",
                             Severity.WARNING, FindingCategory.CONNECTIVITY),
            ]),
        }
        result = deduplicate_snapshots(snapshots)
        assert len(result) == 1
        assert result[0].is_cross_connector is False
