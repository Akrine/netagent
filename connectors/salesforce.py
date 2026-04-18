"""
connectors/salesforce.py

Salesforce connector using the Salesforce REST API.

Fetches opportunities, cases, tasks, and account health data
and normalizes them into DiagnosticSnapshots so the agent can
reason over CRM health in natural language.

Authentication via OAuth2 username-password flow or connected app.
Credentials stored in environment variables:
  SALESFORCE_USERNAME
  SALESFORCE_PASSWORD
  SALESFORCE_SECURITY_TOKEN
  SALESFORCE_CLIENT_ID
  SALESFORCE_CLIENT_SECRET
  SALESFORCE_DOMAIN (default: login.salesforce.com)

API reference: https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
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

_AUTH_URL = "https://{domain}/services/oauth2/token"
_API_VERSION = "v59.0"


class SalesforceConnector(BaseConnector):
    """
    Connector for Salesforce CRM data.

    The device_id parameter maps to a Salesforce account ID.
    Pass 'all' to get an org-wide health overview.
    Pass a specific account ID to focus on that account.

    Surfaces findings for:
    - Stalled opportunities (no activity in 14+ days)
    - Overdue high-priority cases
    - Overdue tasks assigned to current user
    - Deals at risk (close date passed, not closed)
    """

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        security_token: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        domain: str = "login.salesforce.com",
    ) -> None:
        self._username = username or os.environ.get("SALESFORCE_USERNAME", "")
        self._password = password or os.environ.get("SALESFORCE_PASSWORD", "")
        self._security_token = security_token or os.environ.get("SALESFORCE_SECURITY_TOKEN", "")
        self._client_id = client_id or os.environ.get("SALESFORCE_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("SALESFORCE_CLIENT_SECRET", "")
        self._domain = os.environ.get("SALESFORCE_DOMAIN", domain)

        if not all([self._username, self._password, self._client_id, self._client_secret]):
            raise ConnectorAuthError(
                "Salesforce credentials not provided. Set SALESFORCE_USERNAME, "
                "SALESFORCE_PASSWORD, SALESFORCE_CLIENT_ID, SALESFORCE_CLIENT_SECRET."
            )

        self._session = requests.Session()
        self._access_token: Optional[str] = None
        self._instance_url: Optional[str] = None

    @property
    def name(self) -> str:
        return "salesforce"

    def health_check(self) -> bool:
        try:
            self._ensure_auth()
            return self._access_token is not None
        except Exception:
            return False

    def fetch(self, device_id: str) -> DiagnosticSnapshot:
        try:
            self._ensure_auth()
            if device_id == "all":
                return self._fetch_org_overview()
            else:
                return self._fetch_account(device_id)
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"Salesforce fetch failed: {exc}") from exc

    def _fetch_org_overview(self) -> DiagnosticSnapshot:
        findings = []
        findings.extend(self._check_stalled_opportunities())
        findings.extend(self._check_overdue_cases())
        findings.extend(self._check_overdue_tasks())
        findings.extend(self._check_deals_past_close_date())

        overall = self._compute_overall_severity(findings)

        counts = self._get_pipeline_counts()
        summary = (
            f"Pipeline: {counts.get('open_opps', 0)} open opportunities, "
            f"{counts.get('open_cases', 0)} open cases, "
            f"{counts.get('overdue_tasks', 0)} overdue tasks"
        )

        system = SystemHealth(thermal_state=summary)

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id="all",
            captured_at=datetime.now(timezone.utc).isoformat(),
            findings=findings,
            system=system,
            overall_severity=overall,
            raw={"counts": counts},
        )

    def _fetch_account(self, account_id: str) -> DiagnosticSnapshot:
        query = (
            f"SELECT Id, Name, Type, Industry, AnnualRevenue, "
            f"NumberOfEmployees, LastActivityDate "
            f"FROM Account WHERE Id = '{account_id}'"
        )
        result = self._soql(query)
        records = result.get("records", [])
        if not records:
            raise ConnectorNotFoundError(
                f"Salesforce account '{account_id}' not found."
            )

        account = records[0]
        findings = []
        findings.extend(self._check_stalled_opportunities(account_id))
        findings.extend(self._check_overdue_cases(account_id))

        last_activity = account.get("LastActivityDate")
        if last_activity:
            days_since = (datetime.now(timezone.utc).date() -
                         datetime.strptime(last_activity, "%Y-%m-%d").date()).days
            if days_since > 30:
                findings.append(Finding(
                    id=f"sf-account-inactive-{account_id}",
                    severity=Severity.WARNING,
                    category=FindingCategory.PERFORMANCE,
                    title=f"No activity on {account.get('Name')} in {days_since} days",
                    description=(
                        f"Account '{account.get('Name')}' has had no logged activity "
                        f"for {days_since} days. This account may be at risk."
                    ),
                    resolution=(
                        "Log a call, email, or meeting with this account to "
                        "maintain the relationship and update CRM records."
                    ),
                    technical_detail=f"Last activity: {last_activity}",
                ))

        overall = self._compute_overall_severity(findings)
        system = SystemHealth(
            thermal_state=f"Account: {account.get('Name')} | {account.get('Industry', 'Unknown')} | {len(findings)} findings"
        )

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id=account_id,
            captured_at=datetime.now(timezone.utc).isoformat(),
            findings=findings,
            system=system,
            overall_severity=overall,
            raw={"account": account},
        )

    def _check_stalled_opportunities(
        self, account_id: Optional[str] = None
    ) -> list[Finding]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
        where = f"WHERE IsClosed = false AND LastActivityDate < {cutoff}"
        if account_id:
            where += f" AND AccountId = '{account_id}'"

        query = (
            f"SELECT Id, Name, StageName, Amount, CloseDate, "
            f"AccountId, LastActivityDate "
            f"FROM Opportunity {where} "
            f"ORDER BY LastActivityDate ASC LIMIT 20"
        )
        result = self._soql(query)
        records = result.get("records", [])
        if not records:
            return []

        names = [r.get("Name", "Unknown") for r in records[:5]]
        total_value = sum(r.get("Amount") or 0 for r in records)

        return [Finding(
            id="sf-stalled-opps",
            severity=Severity.WARNING,
            category=FindingCategory.PERFORMANCE,
            title=f"{len(records)} stalled opportunities with no activity in 14+ days",
            description=(
                f"{len(records)} open opportunities have had no activity for over "
                f"14 days. Total pipeline at risk: ${total_value:,.0f}."
            ),
            resolution=(
                "Review each stalled opportunity. Schedule follow-up calls, "
                "send check-in emails, or update the stage to reflect current status."
            ),
            technical_detail=f"Stalled deals: {', '.join(names)}",
        )]

    def _check_overdue_cases(
        self, account_id: Optional[str] = None
    ) -> list[Finding]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        where = (
            f"WHERE IsClosed = false AND Priority = 'High' "
            f"AND CreatedDate < {today}"
        )
        if account_id:
            where += f" AND AccountId = '{account_id}'"

        query = (
            f"SELECT Id, CaseNumber, Subject, Priority, Status, "
            f"CreatedDate, AccountId "
            f"FROM Case {where} "
            f"ORDER BY CreatedDate ASC LIMIT 20"
        )
        result = self._soql(query)
        records = result.get("records", [])
        if not records:
            return []

        subjects = [r.get("Subject", "Unknown")[:40] for r in records[:3]]

        return [Finding(
            id="sf-high-priority-cases",
            severity=Severity.CRITICAL,
            category=FindingCategory.PERFORMANCE,
            title=f"{len(records)} open high-priority support cases",
            description=(
                f"{len(records)} high-priority cases are currently open and "
                f"require immediate attention."
            ),
            resolution=(
                "Assign owners to unowned cases immediately. Escalate cases "
                "that have been open for more than 24 hours."
            ),
            technical_detail=f"Cases: {', '.join(subjects)}",
        )]

    def _check_overdue_tasks(self) -> list[Finding]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        query = (
            f"SELECT Id, Subject, ActivityDate, Priority, Status "
            f"FROM Task "
            f"WHERE IsClosed = false AND ActivityDate < {today} "
            f"AND Priority = 'High' "
            f"ORDER BY ActivityDate ASC LIMIT 20"
        )
        result = self._soql(query)
        records = result.get("records", [])
        if not records:
            return []

        subjects = [r.get("Subject", "Unknown")[:40] for r in records[:3]]

        return [Finding(
            id="sf-overdue-tasks",
            severity=Severity.WARNING,
            category=FindingCategory.PERFORMANCE,
            title=f"{len(records)} overdue high-priority tasks",
            description=(
                f"{len(records)} high-priority tasks are past their due date "
                f"and have not been completed."
            ),
            resolution=(
                "Complete or reschedule overdue tasks. Update due dates to "
                "reflect realistic timelines."
            ),
            technical_detail=f"Tasks: {', '.join(subjects)}",
        )]

    def _check_deals_past_close_date(self) -> list[Finding]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        query = (
            f"SELECT Id, Name, StageName, Amount, CloseDate, AccountId "
            f"FROM Opportunity "
            f"WHERE IsClosed = false AND CloseDate < {today} "
            f"ORDER BY CloseDate ASC LIMIT 20"
        )
        result = self._soql(query)
        records = result.get("records", [])
        if not records:
            return []

        names = [r.get("Name", "Unknown") for r in records[:5]]
        total_value = sum(r.get("Amount") or 0 for r in records)

        return [Finding(
            id="sf-past-close-date",
            severity=Severity.CRITICAL,
            category=FindingCategory.PERFORMANCE,
            title=f"{len(records)} deals past their close date",
            description=(
                f"{len(records)} open opportunities have passed their "
                f"expected close date. Total value: ${total_value:,.0f}."
            ),
            resolution=(
                "Update close dates to reflect realistic timelines, or "
                "mark deals as closed-lost if no longer active."
            ),
            technical_detail=f"Deals: {', '.join(names)}",
        )]

    def _get_pipeline_counts(self) -> dict:
        counts = {}
        try:
            r = self._soql("SELECT COUNT() FROM Opportunity WHERE IsClosed = false")
            counts["open_opps"] = r.get("totalSize", 0)
        except Exception:
            counts["open_opps"] = 0
        try:
            r = self._soql("SELECT COUNT() FROM Case WHERE IsClosed = false")
            counts["open_cases"] = r.get("totalSize", 0)
        except Exception:
            counts["open_cases"] = 0
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            r = self._soql(
                f"SELECT COUNT() FROM Task WHERE IsClosed = false AND ActivityDate < {today}"
            )
            counts["overdue_tasks"] = r.get("totalSize", 0)
        except Exception:
            counts["overdue_tasks"] = 0
        return counts

    def _soql(self, query: str) -> dict[str, Any]:
        url = f"{self._instance_url}/services/data/{_API_VERSION}/query"
        try:
            resp = self._session.get(
                url,
                params={"q": query},
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ConnectorError(f"Salesforce SOQL request failed: {exc}") from exc

        if resp.status_code == 401:
            self._access_token = None
            self._ensure_auth()
            resp = self._session.get(
                url,
                params={"q": query},
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=15,
            )

        if not resp.ok:
            raise ConnectorError(
                f"Salesforce returned {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    def _ensure_auth(self) -> None:
        if self._access_token:
            return
        auth_url = _AUTH_URL.format(domain=self._domain)
        try:
            resp = self._session.post(
                auth_url,
                data={
                    "grant_type": "password",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "username": self._username,
                    "password": self._password + self._security_token,
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ConnectorAuthError(
                f"Salesforce auth request failed: {exc}"
            ) from exc

        if not resp.ok:
            raise ConnectorAuthError(
                f"Salesforce authentication failed ({resp.status_code}): "
                f"{resp.text[:200]}"
            )

        payload = resp.json()
        self._access_token = payload["access_token"]
        self._instance_url = payload["instance_url"]

    @staticmethod
    def _compute_overall_severity(findings: list[Finding]) -> Severity:
        if any(f.severity == Severity.CRITICAL for f in findings):
            return Severity.CRITICAL
        if any(f.severity == Severity.WARNING for f in findings):
            return Severity.WARNING
        if any(f.severity == Severity.INFO for f in findings):
            return Severity.INFO
        return Severity.OK
