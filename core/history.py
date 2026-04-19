"""
core/history.py

Snapshot history store with change detection.

Persists snapshots to disk as NDJSON and computes diffs between
the current snapshot and historical ones. Enables the agent to
answer trend questions: "is this getting better or worse?"

History is stored in logs/snapshots/{connector}/{device_id}.ndjson
One line per snapshot, oldest first.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.schema import DiagnosticSnapshot, Finding, Severity


_DEFAULT_HISTORY_DIR = Path("logs/snapshots")
_MAX_HISTORY = 100


@dataclass
class FindingDiff:
    """A finding that appeared or disappeared between two snapshots."""
    finding: Finding
    change: str  # "appeared", "resolved", "worsened", "improved"
    connector: str


@dataclass
class SnapshotDiff:
    """
    Comparison between two snapshots of the same connector/device.
    """
    connector: str
    device_id: str
    previous_captured_at: str
    current_captured_at: str
    previous_severity: Severity
    current_severity: Severity
    new_findings: list[Finding] = field(default_factory=list)
    resolved_findings: list[Finding] = field(default_factory=list)
    severity_changed: bool = False
    severity_improved: bool = False
    severity_worsened: bool = False

    def has_changes(self) -> bool:
        return bool(self.new_findings or self.resolved_findings or self.severity_changed)

    def summary(self) -> str:
        parts = []
        if self.severity_worsened:
            parts.append(
                f"severity worsened from {self.previous_severity.value} "
                f"to {self.current_severity.value}"
            )
        elif self.severity_improved:
            parts.append(
                f"severity improved from {self.previous_severity.value} "
                f"to {self.current_severity.value}"
            )
        if self.new_findings:
            titles = [f.title for f in self.new_findings[:3]]
            parts.append(f"{len(self.new_findings)} new finding(s): {', '.join(titles)}")
        if self.resolved_findings:
            titles = [f.title for f in self.resolved_findings[:3]]
            parts.append(f"{len(self.resolved_findings)} resolved: {', '.join(titles)}")
        if not parts:
            return "No changes detected."
        return "; ".join(parts)


class SnapshotHistory:
    """
    Persists and retrieves historical snapshots for change detection.

    Each connector/device combination has its own history file.
    Snapshots are appended on each store() call and trimmed to
    MAX_HISTORY entries to prevent unbounded growth.
    """

    def __init__(self, history_dir: Path = _DEFAULT_HISTORY_DIR) -> None:
        self._dir = history_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def store(self, snapshot: DiagnosticSnapshot) -> None:
        """Append a snapshot to the history file."""
        path = self._path(snapshot.source_connector, snapshot.device_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        record = {
            "captured_at": snapshot.captured_at,
            "overall_severity": snapshot.overall_severity.value,
            "findings_count": len(snapshot.findings),
            "findings": [
                {
                    "id": f.id,
                    "severity": f.severity.value,
                    "category": f.category.value,
                    "title": f.title,
                    "technical_detail": f.technical_detail,
                }
                for f in snapshot.findings
            ],
            "network_quality": {
                "destination_latency_ms": snapshot.network_quality.destination_latency_ms,
                "destination_loss_percent": snapshot.network_quality.destination_loss_percent,
                "destination_jitter_ms": snapshot.network_quality.destination_jitter_ms,
            } if snapshot.network_quality else None,
            "system": {
                "cpu_percent": snapshot.system.cpu_percent,
                "memory_percent": snapshot.system.memory_percent,
                "disk_percent": snapshot.system.disk_percent,
            } if snapshot.system else None,
        }

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        self._trim(path)

    def get_history(
        self,
        connector: str,
        device_id: str,
        limit: int = 10,
    ) -> list[dict]:
        """Return the most recent N snapshots for a connector/device."""
        path = self._path(connector, device_id)
        if not path.exists():
            return []
        records = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records[-limit:]

    def get_previous(
        self,
        connector: str,
        device_id: str,
    ) -> Optional[dict]:
        """Return the second-most-recent snapshot (the one before current)."""
        history = self.get_history(connector, device_id, limit=2)
        if len(history) < 2:
            return None
        return history[-2]

    def diff(
        self,
        current: DiagnosticSnapshot,
    ) -> Optional[SnapshotDiff]:
        """
        Compare current snapshot against the previous one.
        Returns None if no history exists yet.
        """
        previous = self.get_previous(
            current.source_connector, current.device_id
        )
        if not previous:
            return None

        prev_severity = Severity(previous["overall_severity"])
        curr_severity = current.overall_severity

        prev_finding_titles = {
            f["title"] for f in previous.get("findings", [])
        }
        curr_finding_titles = {f.title for f in current.findings}

        new_findings = [
            f for f in current.findings
            if f.title not in prev_finding_titles
        ]
        resolved_findings_titles = prev_finding_titles - curr_finding_titles
        resolved_findings = [
            Finding(
                id=f["id"],
                severity=Severity(f["severity"]),
                category=current.findings[0].category if current.findings else __import__('core.schema', fromlist=['FindingCategory']).FindingCategory.UNKNOWN,
                title=f["title"],
                description="",
                resolution="",
                technical_detail=f.get("technical_detail", ""),
            )
            for f in previous.get("findings", [])
            if f["title"] in resolved_findings_titles
        ]

        severity_order = [
            Severity.OK, Severity.INFO, Severity.WARNING, Severity.CRITICAL
        ]
        prev_idx = severity_order.index(prev_severity)
        curr_idx = severity_order.index(curr_severity)
        severity_changed = prev_severity != curr_severity
        severity_worsened = curr_idx > prev_idx
        severity_improved = curr_idx < prev_idx

        return SnapshotDiff(
            connector=current.source_connector,
            device_id=current.device_id,
            previous_captured_at=previous.get("captured_at", ""),
            current_captured_at=current.captured_at,
            previous_severity=prev_severity,
            current_severity=curr_severity,
            new_findings=new_findings,
            resolved_findings=resolved_findings,
            severity_changed=severity_changed,
            severity_improved=severity_improved,
            severity_worsened=severity_worsened,
        )

    def count(self, connector: str, device_id: str) -> int:
        """Return the number of stored snapshots for a connector/device."""
        path = self._path(connector, device_id)
        if not path.exists():
            return 0
        with open(path, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def _path(self, connector: str, device_id: str) -> Path:
        safe_device = device_id.replace("/", "_").replace(":", "_")
        return self._dir / connector / f"{safe_device}.ndjson"

    def _trim(self, path: Path) -> None:
        """Keep only the last MAX_HISTORY entries."""
        with open(path, encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        if len(lines) > _MAX_HISTORY:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[-_MAX_HISTORY:])


# Global history instance
snapshot_history = SnapshotHistory()
