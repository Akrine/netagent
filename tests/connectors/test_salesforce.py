"""
tests/connectors/test_salesforce.py

Unit tests for the Salesforce connector normalization logic.
No API credentials or network access required.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from connectors.salesforce import SalesforceConnector
from connectors.base import ConnectorAuthError
from core.schema import FindingCategory, Severity


@pytest.fixture
def connector() -> SalesforceConnector:
    c = SalesforceConnector.__new__(SalesforceConnector)
    c._username = "test"
    c._password = "test"
    c._security_token = "test"
    c._client_id = "test"
    c._client_secret = "test"
    c._domain = "login.salesforce.com"
    c._access_token = "test_token"
    c._instance_url = "https://test.salesforce.com"
    import requests
    c._session = requests.Session()
    return c


def past_date(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def future_date(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


class TestStalledOpportunities:
    def test_no_findings_when_no_stalled_opps(self, connector):
        connector._soql = lambda q: {"records": [], "totalSize": 0}
        findings = connector._check_stalled_opportunities()
        assert findings == []

    def test_warning_finding_for_stalled_opps(self, connector):
        records = [
            {"Id": "1", "Name": "Deal A", "StageName": "Proposal",
             "Amount": 50000, "CloseDate": future_date(30),
             "LastActivityDate": past_date(20)},
            {"Id": "2", "Name": "Deal B", "StageName": "Negotiation",
             "Amount": 75000, "CloseDate": future_date(15),
             "LastActivityDate": past_date(16)},
        ]
        connector._soql = lambda q: {"records": records, "totalSize": 2}
        findings = connector._check_stalled_opportunities()
        assert len(findings) == 1
        assert findings[0].severity == Severity.WARNING
        assert "2 stalled" in findings[0].title
        assert "$125,000" in findings[0].description

    def test_stalled_opp_resolution_mentions_followup(self, connector):
        records = [{"Id": "1", "Name": "Deal A", "StageName": "Proposal",
                    "Amount": 10000, "CloseDate": future_date(10),
                    "LastActivityDate": past_date(15)}]
        connector._soql = lambda q: {"records": records, "totalSize": 1}
        findings = connector._check_stalled_opportunities()
        assert "follow-up" in findings[0].resolution.lower()


class TestOverdueCases:
    def test_no_findings_when_no_overdue_cases(self, connector):
        connector._soql = lambda q: {"records": [], "totalSize": 0}
        findings = connector._check_overdue_cases()
        assert findings == []

    def test_critical_finding_for_high_priority_cases(self, connector):
        records = [
            {"Id": "1", "CaseNumber": "00001", "Subject": "System Down",
             "Priority": "High", "Status": "Open",
             "CreatedDate": past_date(2) + "T00:00:00Z"},
        ]
        connector._soql = lambda q: {"records": records, "totalSize": 1}
        findings = connector._check_overdue_cases()
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL
        assert findings[0].category == FindingCategory.PERFORMANCE

    def test_case_finding_mentions_escalation(self, connector):
        records = [{"Id": "1", "CaseNumber": "00001", "Subject": "Outage",
                    "Priority": "High", "Status": "Open",
                    "CreatedDate": past_date(1) + "T00:00:00Z"}]
        connector._soql = lambda q: {"records": records, "totalSize": 1}
        findings = connector._check_overdue_cases()
        assert "escalat" in findings[0].resolution.lower()


class TestDealsPastCloseDate:
    def test_no_findings_when_all_deals_on_track(self, connector):
        connector._soql = lambda q: {"records": [], "totalSize": 0}
        findings = connector._check_deals_past_close_date()
        assert findings == []

    def test_critical_finding_for_past_close_date(self, connector):
        records = [
            {"Id": "1", "Name": "Big Deal", "StageName": "Proposal",
             "Amount": 200000, "CloseDate": past_date(5)},
        ]
        connector._soql = lambda q: {"records": records, "totalSize": 1}
        findings = connector._check_deals_past_close_date()
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL
        assert "$200,000" in findings[0].description


class TestSeverityComputation:
    def test_ok_when_no_findings(self, connector):
        assert connector._compute_overall_severity([]) == Severity.OK

    def test_critical_takes_precedence(self, connector):
        from core.schema import Finding
        findings = [
            Finding(id="1", severity=Severity.WARNING,
                    category=FindingCategory.PERFORMANCE,
                    title="w", description="", resolution=""),
            Finding(id="2", severity=Severity.CRITICAL,
                    category=FindingCategory.PERFORMANCE,
                    title="c", description="", resolution=""),
        ]
        assert connector._compute_overall_severity(findings) == Severity.CRITICAL


class TestConnectorInit:
    def test_raises_auth_error_without_credentials(self):
        import os
        keys = ["SALESFORCE_USERNAME", "SALESFORCE_PASSWORD",
                "SALESFORCE_CLIENT_ID", "SALESFORCE_CLIENT_SECRET"]
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            with pytest.raises(ConnectorAuthError):
                SalesforceConnector(
                    username="", password="",
                    client_id="", client_secret=""
                )
        finally:
            for k, v in saved.items():
                if v:
                    os.environ[k] = v
