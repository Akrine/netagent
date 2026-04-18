"""
connectors/monday_com.py

Monday.com connector using the GraphQL API.

Fetches boards, items, and their statuses and normalizes them
into a DiagnosticSnapshot so the agent can reason over project
and workflow health in natural language.

Authentication:
    Set MONDAY_API_TOKEN in your .env file.
    Get your token from: monday.com > Profile > Admin > API

API reference: https://developer.monday.com/api-reference/docs/basics
"""

from __future__ import annotations

import os
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
    Severity,
    SystemHealth,
)

_API_URL = "https://api.monday.com/v2"
_API_VERSION = "2024-10"

_STATUS_SEVERITY: dict[str, Severity] = {
    "stuck": Severity.CRITICAL,
    "blocked": Severity.CRITICAL,
    "at risk": Severity.WARNING,
    "behind": Severity.WARNING,
    "in progress": Severity.INFO,
    "working on it": Severity.INFO,
    "done": Severity.OK,
    "completed": Severity.OK,
}


class MondayConnector(BaseConnector):
    """
    Connector for Monday.com project and workflow data.

    The device_id parameter maps to a Monday.com board ID.
    Pass a board ID to fetch that board's items and status.
    Pass 'all' to fetch an overview across all accessible boards.

    Authentication via personal API token stored in MONDAY_API_TOKEN.
    """

    def __init__(self, api_token: Optional[str] = None) -> None:
        self._token = api_token or os.environ.get("MONDAY_API_TOKEN", "")
        if not self._token:
            raise ConnectorAuthError(
                "Monday.com API token not provided. "
                "Set MONDAY_API_TOKEN in your environment."
            )
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": self._token,
            "Content-Type": "application/json",
            "API-Version": _API_VERSION,
        })

    @property
    def name(self) -> str:
        return "monday_com"

    def health_check(self) -> bool:
        try:
            result = self._query("query { me { id name } }")
            return "data" in result
        except Exception:
            return False

    def fetch(self, device_id: str) -> DiagnosticSnapshot:
        try:
            if device_id == "all":
                return self._fetch_all_boards()
            else:
                return self._fetch_board(device_id)
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"Monday.com fetch failed: {exc}") from exc

    def _fetch_all_boards(self) -> DiagnosticSnapshot:
        query = """
        query {
            boards(limit: 20, order_by: last_activity) {
                id
                name
                state
                items_count
                groups {
                    id
                    title
                    color
                }
                items_page(limit: 50) {
                    items {
                        id
                        name
                        state
                        column_values {
                            id
                            type
                            text
                            value
                        }
                    }
                }
            }
        }
        """
        data = self._query(query)
        boards = data.get("data", {}).get("boards", [])
        if not boards:
            raise ConnectorNotFoundError("No boards found in this Monday.com account.")

        findings = []
        total_items = 0
        stuck_items = 0
        at_risk_items = 0

        for board in boards:
            board_findings = self._analyze_board(board)
            findings.extend(board_findings)
            items = board.get("items_page", {}).get("items", [])
            total_items += len(items)
            for item in items:
                status = self._get_item_status(item)
                if status in ("stuck", "blocked"):
                    stuck_items += 1
                elif status in ("at risk", "behind"):
                    at_risk_items += 1

        system = SystemHealth(
            cpu_percent=None,
            memory_percent=None,
            disk_percent=None,
            thermal_state=f"{len(boards)} boards, {total_items} items, {stuck_items} stuck, {at_risk_items} at risk",
        )

        overall = self._compute_overall_severity(findings)

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id="all",
            captured_at=datetime.now(timezone.utc).isoformat(),
            findings=findings,
            system=system,
            overall_severity=overall,
            raw={"boards": boards},
        )

    def _fetch_board(self, board_id: str) -> DiagnosticSnapshot:
        query = """
        query($board_id: [ID!]) {
            boards(ids: $board_id) {
                id
                name
                state
                items_count
                groups {
                    id
                    title
                    color
                }
                items_page(limit: 100) {
                    items {
                        id
                        name
                        state
                        column_values {
                            id
                            type
                            text
                            value
                        }
                    }
                }
            }
        }
        """
        data = self._query(query, variables={"board_id": [board_id]})
        boards = data.get("data", {}).get("boards", [])
        if not boards:
            raise ConnectorNotFoundError(
                f"Board '{board_id}' not found or not accessible."
            )

        board = boards[0]
        findings = self._analyze_board(board)
        overall = self._compute_overall_severity(findings)
        items = board.get("items_page", {}).get("items", [])
        stuck = sum(
            1 for i in items
            if self._get_item_status(i) in ("stuck", "blocked")
        )
        at_risk = sum(
            1 for i in items
            if self._get_item_status(i) in ("at risk", "behind")
        )

        system = SystemHealth(
            thermal_state=(
                f"Board: {board.get('name')} | "
                f"{len(items)} items | {stuck} stuck | {at_risk} at risk"
            ),
        )

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id=board_id,
            captured_at=datetime.now(timezone.utc).isoformat(),
            findings=findings,
            system=system,
            overall_severity=overall,
            raw={"board": board},
        )

    def _analyze_board(self, board: dict[str, Any]) -> list[Finding]:
        findings = []
        board_name = board.get("name", "Unknown Board")
        items = board.get("items_page", {}).get("items", [])

        stuck_items = []
        at_risk_items = []
        overdue_items = []

        for item in items:
            status = self._get_item_status(item)
            if status in ("stuck", "blocked"):
                stuck_items.append(item.get("name", "Unknown"))
            elif status in ("at risk", "behind"):
                at_risk_items.append(item.get("name", "Unknown"))
            if self._is_overdue(item):
                overdue_items.append(item.get("name", "Unknown"))

        if stuck_items:
            findings.append(Finding(
                id=f"monday-stuck-{board.get('id', '')}",
                severity=Severity.CRITICAL,
                category=FindingCategory.PERFORMANCE,
                title=f"Stuck items on {board_name}",
                description=(
                    f"{len(stuck_items)} item(s) are stuck or blocked on "
                    f"the '{board_name}' board and need immediate attention."
                ),
                resolution=(
                    "Review each stuck item, identify blockers, and either "
                    "reassign or escalate to unblock progress."
                ),
                technical_detail=f"Stuck items: {', '.join(stuck_items[:5])}",
            ))

        if at_risk_items:
            findings.append(Finding(
                id=f"monday-atrisk-{board.get('id', '')}",
                severity=Severity.WARNING,
                category=FindingCategory.PERFORMANCE,
                title=f"At-risk items on {board_name}",
                description=(
                    f"{len(at_risk_items)} item(s) are at risk or behind "
                    f"on the '{board_name}' board."
                ),
                resolution=(
                    "Review timeline and resources for at-risk items. "
                    "Consider adjusting scope or deadlines."
                ),
                technical_detail=f"At-risk items: {', '.join(at_risk_items[:5])}",
            ))

        if overdue_items:
            findings.append(Finding(
                id=f"monday-overdue-{board.get('id', '')}",
                severity=Severity.WARNING,
                category=FindingCategory.PERFORMANCE,
                title=f"Overdue items on {board_name}",
                description=(
                    f"{len(overdue_items)} item(s) have passed their due "
                    f"date on the '{board_name}' board."
                ),
                resolution=(
                    "Review overdue items and update due dates or mark "
                    "as complete if finished."
                ),
                technical_detail=f"Overdue: {', '.join(overdue_items[:5])}",
            ))

        return findings

    def _get_item_status(self, item: dict[str, Any]) -> str:
        for col in item.get("column_values", []):
            if col.get("type") == "color" or col.get("id") == "status":
                text = (col.get("text") or "").lower().strip()
                if text:
                    return text
        return ""

    def _is_overdue(self, item: dict[str, Any]) -> bool:
        today = datetime.now(timezone.utc).date()
        for col in item.get("column_values", []):
            if col.get("type") == "date":
                text = col.get("text", "")
                if text:
                    try:
                        due = datetime.strptime(text, "%Y-%m-%d").date()
                        return due < today
                    except ValueError:
                        pass
        return False

    def _query(
        self,
        query: str,
        variables: Optional[dict] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            resp = self._session.post(_API_URL, json=payload, timeout=15)
        except requests.RequestException as exc:
            raise ConnectorError(f"Monday.com request failed: {exc}") from exc

        if resp.status_code == 401:
            raise ConnectorAuthError("Monday.com API token rejected (401).")
        if not resp.ok:
            raise ConnectorError(
                f"Monday.com returned {resp.status_code}: {resp.text[:200]}"
            )

        result = resp.json()
        errors = result.get("errors")
        if errors:
            raise ConnectorError(f"Monday.com GraphQL error: {errors}")

        return result

    @staticmethod
    def _compute_overall_severity(findings: list[Finding]) -> Severity:
        if any(f.severity == Severity.CRITICAL for f in findings):
            return Severity.CRITICAL
        if any(f.severity == Severity.WARNING for f in findings):
            return Severity.WARNING
        if any(f.severity == Severity.INFO for f in findings):
            return Severity.INFO
        return Severity.OK
