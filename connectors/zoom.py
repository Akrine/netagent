"""
connectors/zoom.py

Zoom connector using the Zoom Dashboard and Reports API.

Fetches meeting quality metrics, participant QoS data, and
meeting health indicators, normalizing them into DiagnosticSnapshots
so the agent can reason over call quality in natural language.

Authentication via Server-to-Server OAuth2:
  ZOOM_ACCOUNT_ID
  ZOOM_CLIENT_ID
  ZOOM_CLIENT_SECRET

API reference: https://developers.zoom.us/docs/api/
"""

from __future__ import annotations

import base64
import os
import time
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
    NetworkQuality,
    Severity,
    SystemHealth,
)

_BASE_URL = "https://api.zoom.us/v2"
_TOKEN_URL = "https://zoom.us/oauth/token"

_QUALITY_THRESHOLDS = {
    "latency_warning_ms": 150.0,
    "latency_critical_ms": 300.0,
    "loss_warning_percent": 3.0,
    "loss_critical_percent": 8.0,
    "jitter_warning_ms": 40.0,
    "jitter_critical_ms": 80.0,
    "mos_warning": 3.5,
    "mos_critical": 2.5,
}


class ZoomConnector(BaseConnector):
    """
    Connector for Zoom meeting quality and health data.

    device_id maps to a Zoom meeting ID or user email.
    Pass 'all' for an account-wide quality overview.
    Pass a meeting ID to analyze a specific meeting.
    Pass a user email to analyze a specific user's recent meetings.

    Requires a Zoom account with Dashboard feature enabled
    (Business plan or higher).
    """

    def __init__(
        self,
        account_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ) -> None:
        self._account_id = account_id or os.environ.get("ZOOM_ACCOUNT_ID", "")
        self._client_id = client_id or os.environ.get("ZOOM_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("ZOOM_CLIENT_SECRET", "")

        if not all([self._account_id, self._client_id, self._client_secret]):
            raise ConnectorAuthError(
                "Zoom credentials not provided. Set ZOOM_ACCOUNT_ID, "
                "ZOOM_CLIENT_ID, and ZOOM_CLIENT_SECRET."
            )

        self._session = requests.Session()
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0

    @property
    def name(self) -> str:
        return "zoom"

    def health_check(self) -> bool:
        try:
            token = self._get_token()
            return token is not None
        except Exception:
            return False

    def fetch(self, device_id: str) -> DiagnosticSnapshot:
        try:
            if device_id == "all":
                return self._fetch_account_overview()
            elif "@" in device_id:
                return self._fetch_user_meetings(device_id)
            else:
                return self._fetch_meeting(device_id)
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(f"Zoom fetch failed: {exc}") from exc

    def _fetch_account_overview(self) -> DiagnosticSnapshot:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

        meetings = self._get(
            "/metrics/meetings",
            params={"type": "past", "from": week_ago, "to": today, "page_size": 30},
        )

        meeting_list = meetings.get("meetings", [])
        findings = []
        total_participants = 0
        poor_quality_meetings = []
        high_latency_meetings = []

        for meeting in meeting_list:
            total_participants += meeting.get("participants", 0)
            avg_latency = self._extract_latency(meeting)
            has_issues = self._meeting_has_quality_issues(meeting)

            if has_issues:
                poor_quality_meetings.append(meeting.get("topic", "Unknown"))
            if avg_latency and avg_latency > _QUALITY_THRESHOLDS["latency_warning_ms"]:
                high_latency_meetings.append(meeting.get("topic", "Unknown"))

        if poor_quality_meetings:
            findings.append(Finding(
                id="zoom-poor-quality-meetings",
                severity=Severity.WARNING,
                category=FindingCategory.COLLABORATION,
                title=f"{len(poor_quality_meetings)} meetings had poor call quality this week",
                description=(
                    f"{len(poor_quality_meetings)} out of {len(meeting_list)} meetings "
                    f"experienced quality issues including high latency, packet loss, or jitter."
                ),
                resolution=(
                    "Review participant network conditions. Consider scheduling "
                    "calls during off-peak hours or advising participants to use "
                    "wired connections."
                ),
                technical_detail=f"Affected meetings: {', '.join(poor_quality_meetings[:3])}",
            ))

        if high_latency_meetings:
            findings.append(Finding(
                id="zoom-high-latency",
                severity=Severity.WARNING,
                category=FindingCategory.CONNECTIVITY,
                title=f"High latency detected in {len(high_latency_meetings)} meetings",
                description=(
                    f"{len(high_latency_meetings)} meetings had average latency above "
                    f"{_QUALITY_THRESHOLDS['latency_warning_ms']}ms, which causes "
                    f"audio delays and video freezing."
                ),
                resolution=(
                    "Advise participants to close bandwidth-heavy applications "
                    "during calls. Check ISP performance during meeting times."
                ),
                technical_detail=f"High latency meetings: {', '.join(high_latency_meetings[:3])}",
            ))

        overall = self._compute_overall_severity(findings)
        system = SystemHealth(
            thermal_state=(
                f"{len(meeting_list)} meetings, {total_participants} participants, "
                f"{len(poor_quality_meetings)} quality issues this week"
            )
        )

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id="all",
            captured_at=datetime.now(timezone.utc).isoformat(),
            findings=findings,
            system=system,
            overall_severity=overall,
            raw={"meetings": meeting_list},
        )

    def _fetch_meeting(self, meeting_id: str) -> DiagnosticSnapshot:
        try:
            detail = self._get(f"/metrics/meetings/{meeting_id}")
        except ConnectorError as exc:
            if "404" in str(exc):
                raise ConnectorNotFoundError(
                    f"Meeting '{meeting_id}' not found or not accessible."
                )
            raise

        participants_resp = self._get(
            f"/metrics/meetings/{meeting_id}/participants",
            params={"type": "past", "page_size": 50},
        )
        participants = participants_resp.get("participants", [])

        findings = self._analyze_meeting_quality(meeting_id, detail, participants)
        network_quality = self._extract_meeting_network_quality(participants)
        overall = self._compute_overall_severity(findings)

        system = SystemHealth(
            thermal_state=(
                f"Meeting: {detail.get('topic', 'Unknown')} | "
                f"{len(participants)} participants | "
                f"Duration: {detail.get('duration', 0)} min"
            )
        )

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id=meeting_id,
            captured_at=datetime.now(timezone.utc).isoformat(),
            findings=findings,
            network_quality=network_quality,
            system=system,
            overall_severity=overall,
            raw={"meeting": detail, "participants": participants},
        )

    def _fetch_user_meetings(self, email: str) -> DiagnosticSnapshot:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")

        resp = self._get(
            f"/report/users/{email}/meetings",
            params={"from": month_ago, "to": today, "page_size": 20},
        )

        meetings = resp.get("meetings", [])
        findings = []

        if not meetings:
            findings.append(Finding(
                id="zoom-no-meetings",
                severity=Severity.INFO,
                category=FindingCategory.COLLABORATION,
                title=f"No meetings found for {email} in the last 30 days",
                description=f"User {email} has no recorded meetings in the last 30 days.",
                resolution="Verify the email address is correct and the account is active.",
                technical_detail=f"Date range: {month_ago} to {today}",
            ))

        overall = self._compute_overall_severity(findings)
        system = SystemHealth(
            thermal_state=f"User: {email} | {len(meetings)} meetings in last 30 days"
        )

        return DiagnosticSnapshot(
            source_connector=self.name,
            device_id=email,
            captured_at=datetime.now(timezone.utc).isoformat(),
            findings=findings,
            system=system,
            overall_severity=overall,
            raw={"meetings": meetings},
        )

    def _analyze_meeting_quality(
        self,
        meeting_id: str,
        detail: dict,
        participants: list[dict],
    ) -> list[Finding]:
        findings = []

        high_loss_participants = []
        high_latency_participants = []
        high_jitter_participants = []

        for p in participants:
            name = p.get("user_name", "Unknown")
            audio = p.get("audio_quality", "")
            video = p.get("video_quality", "")

            if audio in ("bad", "poor") or video in ("bad", "poor"):
                high_loss_participants.append(name)

            latency = p.get("network_type")
            if latency == "Wifi" and p.get("audio_quality") == "bad":
                high_latency_participants.append(name)

        if high_loss_participants:
            findings.append(Finding(
                id=f"zoom-poor-quality-{meeting_id}",
                severity=Severity.WARNING,
                category=FindingCategory.COLLABORATION,
                title=f"{len(high_loss_participants)} participants had poor audio/video quality",
                description=(
                    f"{len(high_loss_participants)} participants experienced poor "
                    f"audio or video quality during this meeting."
                ),
                resolution=(
                    "These participants should check their network connection, "
                    "close bandwidth-heavy applications, or switch to a wired connection."
                ),
                technical_detail=f"Affected: {', '.join(high_loss_participants[:5])}",
            ))

        if len(participants) == 0:
            findings.append(Finding(
                id=f"zoom-no-participants-{meeting_id}",
                severity=Severity.INFO,
                category=FindingCategory.COLLABORATION,
                title="No participant quality data available",
                description="Quality metrics are not available for this meeting.",
                resolution="Quality data may not be available for older meetings.",
                technical_detail=f"Meeting ID: {meeting_id}",
            ))

        return findings

    def _extract_meeting_network_quality(
        self, participants: list[dict]
    ) -> Optional[NetworkQuality]:
        if not participants:
            return None

        latencies = []
        losses = []

        for p in participants:
            if p.get("audio_quality") == "good":
                latencies.append(50.0)
                losses.append(0.0)
            elif p.get("audio_quality") == "fair":
                latencies.append(150.0)
                losses.append(2.0)
            elif p.get("audio_quality") in ("bad", "poor"):
                latencies.append(300.0)
                losses.append(8.0)

        if not latencies:
            return None

        return NetworkQuality(
            destination_latency_ms=sum(latencies) / len(latencies),
            destination_loss_percent=sum(losses) / len(losses),
        )

    @staticmethod
    def _extract_latency(meeting: dict) -> Optional[float]:
        quality = meeting.get("quality_score", "")
        if quality == "good":
            return 50.0
        elif quality == "fair":
            return 150.0
        elif quality in ("bad", "poor"):
            return 300.0
        return None

    @staticmethod
    def _meeting_has_quality_issues(meeting: dict) -> bool:
        quality = meeting.get("quality_score", "")
        return quality in ("bad", "poor", "fair")

    def _get(self, path: str, params: Optional[dict] = None) -> dict[str, Any]:
        token = self._get_token()
        url = f"{_BASE_URL}{path}"
        try:
            resp = self._session.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params=params or {},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ConnectorError(f"Zoom request failed: {exc}") from exc

        if resp.status_code == 401:
            raise ConnectorAuthError("Zoom token rejected (401).")
        if resp.status_code == 404:
            raise ConnectorError(f"404: {url}")
        if not resp.ok:
            raise ConnectorError(
                f"Zoom returned {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        credentials = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()

        try:
            resp = self._session.post(
                _TOKEN_URL,
                params={
                    "grant_type": "account_credentials",
                    "account_id": self._account_id,
                },
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            raise ConnectorAuthError(f"Zoom token request failed: {exc}") from exc

        if not resp.ok:
            raise ConnectorAuthError(
                f"Zoom authentication failed ({resp.status_code}): {resp.text[:200]}"
            )

        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expiry = time.time() + payload.get("expires_in", 3600)
        return self._token

    @staticmethod
    def _compute_overall_severity(findings: list[Finding]) -> Severity:
        if any(f.severity == Severity.CRITICAL for f in findings):
            return Severity.CRITICAL
        if any(f.severity == Severity.WARNING for f in findings):
            return Severity.WARNING
        if any(f.severity == Severity.INFO for f in findings):
            return Severity.INFO
        return Severity.OK
