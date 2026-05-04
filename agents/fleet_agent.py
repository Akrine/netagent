"""
agents/fleet_agent.py

Autonomous fleet diagnostic agent.

Unlike DiagnosticAgent which answers single-device questions on demand,
FleetAgent runs autonomously over an entire fleet snapshot and produces
a prioritized action report without any user prompt.

This is the "Check for you" layer Alisson described:
- Ingests fleet-wide data across all organizations and devices
- Uses Claude to reason across devices and identify patterns
- Distinguishes individual device failures from location-wide outages
- Produces a ranked action report with specific next steps

The key insight: when 6 devices in the same NYC office all go offline
at the same time, that's not 6 individual problems — it's one ISP or
building-level problem. Claude can see this pattern. A simple alert
system cannot.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import anthropic

from core.schema import DiagnosticSnapshot, Severity


_DEFAULT_MODEL = "claude-sonnet-4-20250514"

_FLEET_SYSTEM_PROMPT = """\
You are an expert network operations analyst reviewing fleet-wide diagnostic data.
Your job is to analyze data across multiple locations and devices, identify patterns,
and produce a prioritized action report.

Critical thinking rules:
- When multiple devices in the same location fail simultaneously, treat it as a
  location-level problem (ISP outage, building network, router failure) not individual failures.
- Prioritize by business impact: critical locations with many users first.
- Be specific: name the locations, device counts, and exact metrics.
- Distinguish between: ISP issues, WiFi interference, hardware failure, software issues.
- Your output must be a valid JSON object matching the schema provided.
- Do not include markdown, code fences, or any text outside the JSON object.
"""

_FLEET_PROMPT_TEMPLATE = """\
Analyze this fleet diagnostic data and produce a prioritized action report.

Fleet snapshot captured at: {captured_at}
Total devices: {total_devices} across {total_orgs} organizations
Status breakdown: {healthy} healthy, {warning} warning, {critical} critical

Organization summaries:
{org_summaries}

Device details (problem devices only):
{device_details}

Produce a JSON object with this exact schema:
{{
  "overall_assessment": "one sentence summary of fleet health",
  "priority_actions": [
    {{
      "rank": 1,
      "location": "location name",
      "issue": "specific issue description",
      "affected_devices": number,
      "root_cause": "ISP outage | WiFi interference | Hardware failure | Software issue | Unknown",
      "confidence": "high | medium | low",
      "action": "specific step to take right now",
      "urgency": "immediate | today | this_week"
    }}
  ],
  "patterns_detected": ["pattern 1", "pattern 2"],
  "healthy_locations": ["location names that need no action"]
}}

Return only the JSON object. No markdown, no explanation.
"""


@dataclass
class FleetActionItem:
    rank: int
    location: str
    issue: str
    affected_devices: int
    root_cause: str
    confidence: str
    action: str
    urgency: str


@dataclass
class FleetReport:
    overall_assessment: str
    priority_actions: list[FleetActionItem]
    patterns_detected: list[str]
    healthy_locations: list[str]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    raw_snapshot_severity: Severity = Severity.OK

    def to_dict(self) -> dict:
        return {
            "overall_assessment": self.overall_assessment,
            "generated_at": self.generated_at,
            "raw_snapshot_severity": self.raw_snapshot_severity.value,
            "priority_actions": [
                {
                    "rank": a.rank,
                    "location": a.location,
                    "issue": a.issue,
                    "affected_devices": a.affected_devices,
                    "root_cause": a.root_cause,
                    "confidence": a.confidence,
                    "action": a.action,
                    "urgency": a.urgency,
                }
                for a in self.priority_actions
            ],
            "patterns_detected": self.patterns_detected,
            "healthy_locations": self.healthy_locations,
        }

    def to_text(self) -> str:
        lines = [
            f"Fleet Diagnostic Report — {self.generated_at}",
            f"Overall: {self.overall_assessment}",
            "",
            "Priority Actions:",
        ]
        for action in self.priority_actions:
            lines.append(
                f"  [{action.urgency.upper()}] #{action.rank} {action.location} — {action.issue}"
            )
            lines.append(f"    Affected: {action.affected_devices} devices")
            lines.append(f"    Root cause: {action.root_cause} (confidence: {action.confidence})")
            lines.append(f"    Action: {action.action}")
            lines.append("")
        if self.patterns_detected:
            lines.append("Patterns detected:")
            for p in self.patterns_detected:
                lines.append(f"  - {p}")
            lines.append("")
        if self.healthy_locations:
            lines.append(f"No action needed: {', '.join(self.healthy_locations)}")
        return "\n".join(lines)


class FleetAgent:
    """
    Autonomous fleet diagnostic agent.

    Analyzes an entire fleet snapshot and produces a prioritized
    action report using Claude as the reasoning engine.

    Usage:
        agent = FleetAgent()
        report = agent.analyze(snapshot)
        print(report.to_text())
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = _DEFAULT_MODEL,
        max_tokens: int = 2048,
    ) -> None:
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        )
        self._model = model
        self._max_tokens = max_tokens

    def analyze(self, snapshot: DiagnosticSnapshot) -> FleetReport:
        """
        Analyze a fleet snapshot and return a prioritized action report.
        """
        prompt = self._build_prompt(snapshot)

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_FLEET_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)

        actions = [
            FleetActionItem(
                rank=a["rank"],
                location=a["location"],
                issue=a["issue"],
                affected_devices=a["affected_devices"],
                root_cause=a["root_cause"],
                confidence=a["confidence"],
                action=a["action"],
                urgency=a["urgency"],
            )
            for a in data.get("priority_actions", [])
        ]

        return FleetReport(
            overall_assessment=data["overall_assessment"],
            priority_actions=actions,
            patterns_detected=data.get("patterns_detected", []),
            healthy_locations=data.get("healthy_locations", []),
            raw_snapshot_severity=snapshot.overall_severity,
        )

    def _build_prompt(self, snapshot: DiagnosticSnapshot) -> str:
        raw = snapshot.raw

        orgs = raw.get("organizations", [])
        devices = raw.get("devices", []) if "devices" in raw else []

        org_summaries = "\n".join(
            f"  - {o.get('name', o.get('orgId', 'Unknown'))}: "
            f"{o.get('stats', {}).get('total', '?')} devices, "
            f"health={o.get('overallHealth', '?')}, "
            f"critical={o.get('stats', {}).get('critical', 0)}, "
            f"warning={o.get('stats', {}).get('warning', 0)}, "
            f"healthy={o.get('stats', {}).get('healthy', 0)}"
            for o in orgs
        ) or "  No organization data available."

        problem_devices = [
            d for d in devices
            if d.get("status") in ("critical", "warning")
        ]

        device_details = "\n".join(
            f"  - {d.get('assetId', d.get('clientId', '?'))} "
            f"[{d.get('orgId', '?')}] "
            f"status={d.get('status', '?')} "
            f"state={d.get('connectionState', '?')} "
            f"os={d.get('os', '?')} "
            f"version={'current' if d.get('isCurrentVersion') else 'outdated'} "
            + (
                f"latency={d['networkQuality']['latencyMs']}ms "
                f"loss={d['networkQuality']['lossPercent']}% "
                f"jitter={d['networkQuality']['jitterMs']}ms"
                if d.get("networkQuality") else "networkQuality=none"
            )
            for d in problem_devices
        ) or "  No problem devices found."

        total = raw.get("total_devices", len(devices))
        critical = raw.get("critical", sum(1 for d in devices if d.get("status") == "critical"))
        warning = raw.get("warning", sum(1 for d in devices if d.get("status") == "warning"))
        healthy = raw.get("healthy", sum(1 for d in devices if d.get("status") == "healthy"))

        return _FLEET_PROMPT_TEMPLATE.format(
            captured_at=snapshot.captured_at,
            total_devices=total,
            total_orgs=len(orgs),
            healthy=healthy,
            warning=warning,
            critical=critical,
            org_summaries=org_summaries,
            device_details=device_details,
        )
