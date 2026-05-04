"""
core/scheduler.py

Autonomous fleet diagnostic scheduler.

Runs FleetAgent on a configurable cadence in a background thread.
Stores reports, detects changes between runs, and surfaces when
fleet health degrades without any user prompt.

This is what makes Savvy autonomous — it checks your fleet whether
or not anyone is looking, and knows when something changed.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from core.logger import ConversationLogger


@dataclass
class ScheduledReport:
    """A fleet report produced by the scheduler."""
    report_id: str
    generated_at: str
    overall_assessment: str
    severity: str
    priority_actions: list[dict]
    patterns_detected: list[str]
    healthy_locations: list[str]
    changed_from_previous: bool = False
    change_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "overall_assessment": self.overall_assessment,
            "severity": self.severity,
            "priority_actions": self.priority_actions,
            "patterns_detected": self.patterns_detected,
            "healthy_locations": self.healthy_locations,
            "changed_from_previous": self.changed_from_previous,
            "change_summary": self.change_summary,
        }


class FleetScheduler:
    """
    Background scheduler for autonomous fleet diagnostics.

    Runs FleetAgent on a cadence, stores reports, and detects
    changes between consecutive runs. When fleet health changes,
    the scheduler records what changed and when.

    Usage:
        scheduler = FleetScheduler(interval_seconds=300)
        scheduler.start()
        reports = scheduler.get_reports()
        latest = scheduler.get_latest()
        scheduler.stop()
    """

    def __init__(
        self,
        interval_seconds: float = 300,
        max_reports: int = 50,
        connector_fixture: Optional[str] = None,
    ) -> None:
        self._interval = interval_seconds
        self._max_reports = max_reports
        self._connector_fixture = connector_fixture
        self._reports: list[ScheduledReport] = []
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._run_count = 0
        self._last_run: Optional[str] = None
        self._next_run: Optional[str] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="savvy-fleet-scheduler",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=15)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_now(self) -> Optional[ScheduledReport]:
        """Trigger an immediate run outside the normal schedule."""
        return self._execute()

    def get_reports(self, limit: int = 20) -> list[ScheduledReport]:
        with self._lock:
            return list(reversed(self._reports))[:limit]

    def get_latest(self) -> Optional[ScheduledReport]:
        with self._lock:
            return self._reports[-1] if self._reports else None

    def get_changes(self, limit: int = 10) -> list[ScheduledReport]:
        """Return only reports where something changed from the previous run."""
        with self._lock:
            changed = [r for r in self._reports if r.changed_from_previous]
        return list(reversed(changed))[:limit]

    def stats(self) -> dict:
        with self._lock:
            total = len(self._reports)
            changes = sum(1 for r in self._reports if r.changed_from_previous)
            latest_severity = self._reports[-1].severity if self._reports else None
        return {
            "running": self.is_running(),
            "interval_seconds": self._interval,
            "run_count": self._run_count,
            "total_reports": total,
            "total_changes_detected": changes,
            "last_run": self._last_run,
            "next_run": self._next_run,
            "latest_severity": latest_severity,
        }

    def _run(self) -> None:
        # Run immediately on start
        self._execute()
        while not self._stop_event.is_set():
            next_time = time.time() + self._interval
            self._next_run = datetime.fromtimestamp(
                next_time, tz=timezone.utc
            ).isoformat()
            self._stop_event.wait(timeout=self._interval)
            if not self._stop_event.is_set():
                self._execute()

    def _execute(self) -> Optional[ScheduledReport]:
        try:
            import pathlib
            from connectors.mock_fleet import MockFleetConnector
            from agents.fleet_agent import FleetAgent

            fixture = self._connector_fixture or str(
                pathlib.Path(__file__).parent.parent / "fixtures" / "mock_fleet.json"
            )
            connector = MockFleetConnector(fixture)
            snapshot = connector.fetch("all")

            agent = FleetAgent()
            fleet_report = agent.analyze(snapshot)

            report_id = f"fleet-{int(time.time())}"
            now = datetime.now(timezone.utc).isoformat()

            changed, change_summary = self._detect_change(fleet_report)

            scheduled = ScheduledReport(
                report_id=report_id,
                generated_at=now,
                overall_assessment=fleet_report.overall_assessment,
                severity=fleet_report.raw_snapshot_severity.value,
                priority_actions=[a.__dict__ for a in fleet_report.priority_actions],
                patterns_detected=fleet_report.patterns_detected,
                healthy_locations=fleet_report.healthy_locations,
                changed_from_previous=changed,
                change_summary=change_summary,
            )

            with self._lock:
                self._reports.append(scheduled)
                if len(self._reports) > self._max_reports:
                    self._reports = self._reports[-self._max_reports:]

            self._run_count += 1
            self._last_run = now
            return scheduled

        except Exception as exc:
            self._run_count += 1
            self._last_run = datetime.now(timezone.utc).isoformat()
            return None

    def _detect_change(self, new_report) -> tuple[bool, str]:
        with self._lock:
            if not self._reports:
                return False, ""
            previous = self._reports[-1]

        prev_severity = previous.severity
        curr_severity = new_report.raw_snapshot_severity.value

        prev_healthy = set(previous.healthy_locations)
        curr_healthy = set(new_report.healthy_locations)

        prev_action_count = len(previous.priority_actions)
        curr_action_count = len(new_report.priority_actions)

        changes = []

        if prev_severity != curr_severity:
            changes.append(
                f"severity changed from {prev_severity} to {curr_severity}"
            )

        newly_healthy = curr_healthy - prev_healthy
        newly_degraded = prev_healthy - curr_healthy
        if newly_healthy:
            changes.append(f"recovered: {', '.join(newly_healthy)}")
        if newly_degraded:
            changes.append(f"degraded: {', '.join(newly_degraded)}")

        if curr_action_count != prev_action_count:
            changes.append(
                f"action count changed from {prev_action_count} to {curr_action_count}"
            )

        if changes:
            return True, "; ".join(changes)
        return False, ""


# Global scheduler instance
fleet_scheduler = FleetScheduler(interval_seconds=300)
