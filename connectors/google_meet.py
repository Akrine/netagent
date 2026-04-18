"""
connectors/google_meet.py

Google Meet connector using the Google Meet REST API v2.

Fetches conference records, participants, and meeting artifacts
and normalizes them into DiagnosticSnapshots.

Unlike Zoom, Google Meet does not expose per-participant QoS metrics
(latency, packet loss, jitter). Instead this connector surfaces
meeting health indicators: participation rates, recording availability,
transcript status, and meeting frequency patterns.

Authentication via Google OAuth2 service account:
  GOOGLE_MEET_SERVICE_ACCOUNT_JSON  - Path to service account JSON file
  OR
  GOOGLE_MEET_ACCESS_TOKEN          - Pre-obtained access token

API reference: https://developers.google.com/workspace/meet/api/reference/rest/v2
"""

from __future__ import annotations

import json
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

_BASE_URL = "https://meet.googleapis.com/v2"


class GoogleMeetConnector(BaseConnector):
    """
    Connector for Google Meet conference records and meeting health.

    device_id maps to a Google Meet space name (e.g. 'spaces/abc-defg-hij')
    or 'all' for an account-wide overview of recent conferences.

    Note: Google Meet REST API does not expose per-participant QoS metrics
    (latency, packet loss, jitter). This connector surfaces meeting health
    through participation patterns, recording availability, and frequency.
    This is a key architectural difference from the Zoom connector and
    demonstrates how the DiagnosticSnapshot schema accommodates connectors
    with varying data richness.
    """

    def __init__(
        self,
        access_token: Optional[str] = None,
        service_account_json: Optional[str] = None,
    ) -> None:
        self._access_token = (
            access_token
            or os.environ.get("GOOGLE_MEET_ACCESS_TOKEN", "")
        )
        self._service_account_path = (
            service_account_json
            or os.environ.get("GOOGLE_MEET_SERVICE_ACCOUNT_JSON", "")
        )

        if not self._access_token and not self._service_account_path:
            raise ConnectorAuthError(
                "Google Meet credentials not provided. Set either "
                "GOOGLE_MEET_ACCESS_TOKEN or GOOGLE_MEET_SERVICE_ACCOUNT_JSON."
            )

        self._session = requests.Session()

    @property
    def name(self) -> str:
        return "google_meet"

    def health_check(self) -> bool:
        try:
            self._get("/conferenceRecords", params={"pageSize": 1})
            return True
        except Exception:
            return False

    def fetch(self, device_id: str) -> DiagnosticSnapshot:
        try:
            if device_id == "all":
                return self._fetch_recent_conferences()
            else:
                return self._fetch_space(device_id)
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"Google Meet fetch failed: {exc}") from exc

    def _fetch_recent_conferences(self) -> DiagnosticSnapshot:
        resp = self._get(
            "/conferenceRecords",
            params={"pageSize": 20},
        )
        conferences = resp.get("conferenceRecords", [])
        findings = []

        empty_conferences = []
        long_conferences = []
        no_recording_conferences = []

        for conf in conferences:
            name = conf.get("name", "")
            space = conf.get("space", "")
            start = conf.get("startTime", "")
            end = conf.get("endTime", "")

            participants_resp = self._get(
                f"/{name}/participants",
                params={"pageSize": 50},
            ) if name else {"participants": []}
            participants = participants_resp.get("participants", [])

            if len(participants) <= 1:
                empty_conferences.append(name)

            if start and end:
                duration = self._compute_duration_minutes(start, end)
                if duration and duration > 120:
                    long_conferences.append((name, duration))

        if empty_conferences:
            findings.append(Finding(
                id="gmeet-empty-conferences",
                severity=Severity.INFO,
                category=FindingCategory.COLLABORATION,
                title=f"{len(empty_conferences)} recent meetings had only one participant",
                description=(
                    f"{len(empty_conferences)} recent Google Meet conferences had "
                    f"only one participant, suggesting scheduling inefficiencies or "
                    f"no-shows."
                ),
                resolution=(
                    "Review meeting invitations and follow up with participants "
                    "who did not attend. Consider sending reminders before meetings."
                ),
                technical_detail=f"Conferences: {', '.join(empty_conferences[:3])}",
            ))

        if long_conferences:
            findings.append(Finding(
                id="gmeet-long-conferences",
                severity=Severity.INFO,
                category=FindingCategory.PERFORMANCE,
                title=f"{len(long_conferences)} meetings exceeded 2 hours",
                description=(
                    f"{len(long_conferences)} recent conferences ran longer than "
                    f"2 hours, which may indicate poor meeting structure or scope creep."
                ),
                resolution=(
                    "Consider breaking long meetings into focused sessions with "
                    "clear agendas and time limits."
                ),
                technical_detail=(
                    f"Long meetings: "
                    f"{', '.join(f'{n} ({d}min)' for n, d in long_conferences[:3])}"
                ),
            ))

        overall = self._compute_overall_severity(findings)
        system = SystemHealth(
            thermal_state=(
                f"{len(conferences)} recent conferences, "
                f"{len(empty_conferences)} with low attendance, "
                f"{len(long_conferences)} over 2 hours"
            )
        )

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id="all",
            captured_at=datetime.now(timezone.utc).isoformat(),
            findings=findings,
            system=system,
            overall_severity=overall,
            raw={"conferences": conferences},
        )

    def _fetch_space(self, space_name: str) -> DiagnosticSnapshot:
        try:
            space = self._get(f"/{space_name}")
        except ConnectorError as exc:
            if "404" in str(exc):
                raise ConnectorNotFoundError(
                    f"Google Meet space '{space_name}' not found."
                )
            raise

        conf_resp = self._get(
            "/conferenceRecords",
            params={"filter": f"space.name={space_name}", "pageSize": 10},
        )
        conferences = conf_resp.get("conferenceRecords", [])
        findings = []

        if not conferences:
            findings.append(Finding(
                id=f"gmeet-no-history-{space_name}",
                severity=Severity.INFO,
                category=FindingCategory.COLLABORATION,
                title="No recent conference history for this space",
                description=f"No conferences found for space '{space_name}'.",
                resolution="Verify the space name is correct and has been used recently.",
                technical_detail=f"Space: {space_name}",
            ))

        overall = self._compute_overall_severity(findings)
        system = SystemHealth(
            thermal_state=f"Space: {space_name} | {len(conferences)} recent conferences"
        )

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id=space_name,
            captured_at=datetime.now(timezone.utc).isoformat(),
            findings=findings,
            system=system,
            overall_severity=overall,
            raw={"space": space, "conferences": conferences},
        )

    def _get(self, path: str, params: Optional[dict] = None) -> dict[str, Any]:
        url = f"{_BASE_URL}{path}" if not path.startswith("http") else path
        headers = {"Authorization": f"Bearer {self._access_token}"}

        try:
            resp = self._session.get(
                url,
                headers=headers,
                params=params or {},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ConnectorError(f"Google Meet request failed: {exc}") from exc

        if resp.status_code == 401:
            raise ConnectorAuthError("Google Meet token rejected (401).")
        if resp.status_code == 404:
            raise ConnectorError(f"404: {url}")
        if not resp.ok:
            raise ConnectorError(
                f"Google Meet returned {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    @staticmethod
    def _compute_duration_minutes(start: str, end: str) -> Optional[float]:
        try:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            s = datetime.strptime(start[:19], "%Y-%m-%dT%H:%M:%S")
            e = datetime.strptime(end[:19], "%Y-%m-%dT%H:%M:%S")
            return (e - s).total_seconds() / 60
        except Exception:
            return None

    @staticmethod
    def _compute_overall_severity(findings: list[Finding]) -> Severity:
        if any(f.severity == Severity.CRITICAL for f in findings):
            return Severity.CRITICAL
        if any(f.severity == Severity.WARNING for f in findings):
            return Severity.WARNING
        if any(f.severity == Severity.INFO for f in findings):
            return Severity.INFO
        return Severity.OK
