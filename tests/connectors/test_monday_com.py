"""
tests/connectors/test_monday_com.py

Unit tests for the Monday.com connector normalization logic.

No API credentials or network access required.
All tests operate on synthetic board/item fixtures.
"""

from __future__ import annotations

import pytest

from connectors.monday_com import MondayConnector
from connectors.base import ConnectorAuthError
from core.schema import FindingCategory, Severity


@pytest.fixture
def connector() -> MondayConnector:
    return MondayConnector(api_token="test_token")


def make_item(name: str, status: str = "", due_date: str = "") -> dict:
    col_values = []
    if status:
        col_values.append({"id": "status", "type": "color", "text": status, "value": ""})
    if due_date:
        col_values.append({"id": "date", "type": "date", "text": due_date, "value": ""})
    return {"id": "1", "name": name, "state": "active", "column_values": col_values}


def make_board(board_id: str, name: str, items: list) -> dict:
    return {
        "id": board_id,
        "name": name,
        "state": "active",
        "items_count": len(items),
        "groups": [],
        "items_page": {"items": items},
    }


class TestFindingGeneration:
    def test_no_findings_for_healthy_board(self, connector):
        board = make_board("1", "Q2 Roadmap", [
            make_item("Feature A", status="done"),
            make_item("Feature B", status="completed"),
        ])
        findings = connector._analyze_board(board)
        assert findings == []

    def test_stuck_items_generate_critical_finding(self, connector):
        board = make_board("1", "Sprint Board", [
            make_item("Bug Fix", status="stuck"),
            make_item("Deploy", status="blocked"),
        ])
        findings = connector._analyze_board(board)
        critical = [f for f in findings if f.severity == Severity.CRITICAL]
        assert len(critical) == 1
        assert "2 item(s)" in critical[0].description
        assert critical[0].category == FindingCategory.PERFORMANCE

    def test_at_risk_items_generate_warning_finding(self, connector):
        board = make_board("1", "Sprint Board", [
            make_item("Integration", status="at risk"),
            make_item("Testing", status="behind"),
        ])
        findings = connector._analyze_board(board)
        warnings = [f for f in findings if f.severity == Severity.WARNING]
        assert len(warnings) == 1
        assert "2 item(s)" in warnings[0].description

    def test_overdue_items_generate_warning_finding(self, connector):
        board = make_board("1", "Sprint Board", [
            make_item("Old Task", due_date="2024-01-01"),
        ])
        findings = connector._analyze_board(board)
        overdue = [f for f in findings if "verdue" in f.title]
        assert len(overdue) == 1
        assert overdue[0].severity == Severity.WARNING

    def test_future_due_date_not_overdue(self, connector):
        board = make_board("1", "Sprint Board", [
            make_item("Future Task", due_date="2099-12-31"),
        ])
        findings = connector._analyze_board(board)
        overdue = [f for f in findings if "verdue" in f.title]
        assert len(overdue) == 0

    def test_mixed_board_generates_multiple_findings(self, connector):
        board = make_board("1", "Mixed Board", [
            make_item("Stuck Task", status="stuck"),
            make_item("At Risk Task", status="at risk"),
            make_item("Done Task", status="done"),
        ])
        findings = connector._analyze_board(board)
        assert len(findings) == 2
        severities = {f.severity for f in findings}
        assert Severity.CRITICAL in severities
        assert Severity.WARNING in severities


class TestItemStatus:
    def test_extracts_status_from_color_column(self, connector):
        item = make_item("Task", status="stuck")
        assert connector._get_item_status(item) == "stuck"

    def test_returns_empty_string_when_no_status(self, connector):
        item = make_item("Task")
        assert connector._get_item_status(item) == ""

    def test_status_is_lowercased(self, connector):
        item = make_item("Task", status="In Progress")
        assert connector._get_item_status(item) == "in progress"


class TestSeverityComputation:
    def test_ok_when_no_findings(self, connector):
        assert connector._compute_overall_severity([]) == Severity.OK

    def test_critical_takes_precedence(self, connector):
        from core.schema import Finding
        findings = [
            Finding(id="1", severity=Severity.WARNING, category=FindingCategory.PERFORMANCE,
                    title="w", description="", resolution=""),
            Finding(id="2", severity=Severity.CRITICAL, category=FindingCategory.PERFORMANCE,
                    title="c", description="", resolution=""),
        ]
        assert connector._compute_overall_severity(findings) == Severity.CRITICAL


class TestConnectorInit:
    def test_raises_auth_error_without_token(self):
        import os
        old = os.environ.pop("MONDAY_API_TOKEN", None)
        try:
            with pytest.raises(ConnectorAuthError):
                MondayConnector(api_token="")
        finally:
            if old:
                os.environ["MONDAY_API_TOKEN"] = old
