"""
tests/test_history.py

Unit tests for snapshot history and change detection.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from core.history import SnapshotHistory, SnapshotDiff
from core.schema import (
    DiagnosticSnapshot,
    Finding,
    FindingCategory,
    NetworkQuality,
    Severity,
    SystemHealth,
)


def make_snapshot(
    connector: str = "test",
    device_id: str = "local",
    severity: Severity = Severity.OK,
    findings: list = None,
    captured_at: str = "2026-04-18T00:00:00Z",
) -> DiagnosticSnapshot:
    return DiagnosticSnapshot(
        source_connector=connector,
        device_id=device_id,
        captured_at=captured_at,
        findings=findings or [],
        overall_severity=severity,
    )


def make_finding(title: str, severity: Severity = Severity.WARNING) -> Finding:
    return Finding(
        id=f"id-{title}",
        severity=severity,
        category=FindingCategory.CONNECTIVITY,
        title=title,
        description="",
        resolution="",
    )


@pytest.fixture
def history(tmp_path) -> SnapshotHistory:
    return SnapshotHistory(history_dir=tmp_path / "snapshots")


class TestStorage:
    def test_store_creates_file(self, history, tmp_path):
        snapshot = make_snapshot()
        history.store(snapshot)
        assert history.count("test", "local") == 1

    def test_store_multiple_appends(self, history):
        for i in range(3):
            history.store(make_snapshot(captured_at=f"2026-04-1{i}T00:00:00Z"))
        assert history.count("test", "local") == 3

    def test_get_history_returns_records(self, history):
        history.store(make_snapshot(captured_at="2026-04-18T00:00:00Z"))
        history.store(make_snapshot(captured_at="2026-04-18T01:00:00Z"))
        records = history.get_history("test", "local")
        assert len(records) == 2

    def test_get_history_respects_limit(self, history):
        for i in range(5):
            history.store(make_snapshot())
        records = history.get_history("test", "local", limit=3)
        assert len(records) == 3

    def test_get_previous_returns_second_most_recent(self, history):
        history.store(make_snapshot(captured_at="2026-04-18T00:00:00Z"))
        history.store(make_snapshot(captured_at="2026-04-18T01:00:00Z"))
        prev = history.get_previous("test", "local")
        assert prev is not None
        assert prev["captured_at"] == "2026-04-18T00:00:00Z"

    def test_get_previous_returns_none_on_single_entry(self, history):
        history.store(make_snapshot())
        assert history.get_previous("test", "local") is None

    def test_no_history_returns_empty(self, history):
        assert history.get_history("nonexistent", "local") == []
        assert history.count("nonexistent", "local") == 0


class TestDiff:
    def test_diff_returns_none_without_history(self, history):
        snapshot = make_snapshot(severity=Severity.WARNING)
        history.store(snapshot)
        result = history.diff(snapshot)
        assert result is None

    def test_diff_detects_new_findings(self, history):
        history.store(make_snapshot(severity=Severity.OK))
        current = make_snapshot(
            severity=Severity.WARNING,
            findings=[make_finding("Connection dropouts")],
            captured_at="2026-04-18T01:00:00Z",
        )
        history.store(current)
        diff = history.diff(current)
        assert diff is not None
        assert len(diff.new_findings) == 1
        assert diff.new_findings[0].title == "Connection dropouts"

    def test_diff_detects_resolved_findings(self, history):
        prev = make_snapshot(
            severity=Severity.WARNING,
            findings=[make_finding("Connection dropouts")],
        )
        history.store(prev)
        current = make_snapshot(
            severity=Severity.OK,
            findings=[],
            captured_at="2026-04-18T01:00:00Z",
        )
        history.store(current)
        diff = history.diff(current)
        assert diff is not None
        assert len(diff.resolved_findings) == 1

    def test_diff_detects_severity_worsened(self, history):
        history.store(make_snapshot(severity=Severity.OK))
        current = make_snapshot(
            severity=Severity.CRITICAL,
            captured_at="2026-04-18T01:00:00Z",
        )
        history.store(current)
        diff = history.diff(current)
        assert diff.severity_worsened is True
        assert diff.severity_improved is False

    def test_diff_detects_severity_improved(self, history):
        history.store(make_snapshot(severity=Severity.CRITICAL))
        current = make_snapshot(
            severity=Severity.OK,
            captured_at="2026-04-18T01:00:00Z",
        )
        history.store(current)
        diff = history.diff(current)
        assert diff.severity_improved is True
        assert diff.severity_worsened is False

    def test_diff_no_changes(self, history):
        history.store(make_snapshot(severity=Severity.OK))
        current = make_snapshot(
            severity=Severity.OK,
            captured_at="2026-04-18T01:00:00Z",
        )
        history.store(current)
        diff = history.diff(current)
        assert diff is not None
        assert diff.has_changes() is False

    def test_diff_summary_worsened(self, history):
        history.store(make_snapshot(severity=Severity.OK))
        current = make_snapshot(
            severity=Severity.WARNING,
            findings=[make_finding("New issue")],
            captured_at="2026-04-18T01:00:00Z",
        )
        history.store(current)
        diff = history.diff(current)
        summary = diff.summary()
        assert "worsened" in summary or "new finding" in summary.lower()
