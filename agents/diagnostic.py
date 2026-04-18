"""
agents/diagnostic.py

Domain-agnostic diagnostic reasoning agent.

Uses Claude as the reasoning engine. The system prompt is constructed
from the normalized DiagnosticSnapshot so the model has full context
about the current state of the system being diagnosed.

The agent is intentionally domain-agnostic: it does not contain any
Network Weather-specific logic. All domain knowledge arrives through
the snapshot and the user's questions.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import time

import anthropic

from agents.base import AgentResponse, BaseAgent
from core.logger import ConversationLogger
from core.schema import DiagnosticSnapshot, FindingCategory, Severity


_DEFAULT_MODEL = "claude-sonnet-4-20250514"

_SYSTEM_TEMPLATE = """\
You are a diagnostic assistant for {connector} data. You have access to a \
real-time snapshot of a system's current state. Your job is to answer the \
user's questions clearly and accurately based solely on the data provided.

Guidelines:
- Be direct and specific. Reference actual values from the data.
- Prioritize findings by severity: critical first, then warning, then info.
- When recommending a fix, give step-by-step instructions if available.
- If the data does not contain enough information to answer confidently, say so.
- Do not invent data that is not present in the snapshot.
- Keep responses concise unless the user asks for detail.
- Do not use emoticons or emoji in your responses.

Current snapshot captured at: {captured_at}
Overall severity: {overall_severity}

--- DIAGNOSTIC DATA ---
{snapshot_json}
--- END DIAGNOSTIC DATA ---
"""


class DiagnosticAgent(BaseAgent):
    """
    Reasoning agent that answers natural language questions about
    any DiagnosticSnapshot using Claude as the LLM backend.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = _DEFAULT_MODEL,
        max_tokens: int = 1024,
        enable_logging: bool = True,
    ) -> None:
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        )
        self._model = model
        self._max_tokens = max_tokens
        self._logger = ConversationLogger() if enable_logging else None

    @property
    def name(self) -> str:
        return "diagnostic"

    def query(
        self,
        snapshot: DiagnosticSnapshot,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> AgentResponse:
        system_prompt = self._build_system_prompt(snapshot)
        messages = self._build_messages(question, history)

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system_prompt,
            messages=messages,
        )

        t1 = time.time()
        answer = response.content[0].text
        latency_ms = (time.time() - t1) * 1000
        sources = self._extract_sources(snapshot, answer)
        follow_ups = self._suggest_follow_ups(snapshot)

        if self._logger:
            self._logger.log(
                snapshot=snapshot,
                question=question,
                answer=answer,
                history=history,
                latency_ms=latency_ms,
            )

        return AgentResponse(
            answer=answer,
            sources=sources,
            follow_up_suggestions=follow_ups,
        )

    def _build_system_prompt(self, snapshot: DiagnosticSnapshot) -> str:
        snapshot_data = self._snapshot_to_context(snapshot)
        return _SYSTEM_TEMPLATE.format(
            connector=snapshot.source_connector,
            captured_at=snapshot.captured_at,
            overall_severity=snapshot.overall_severity.value,
            snapshot_json=json.dumps(snapshot_data, indent=2),
        )

    def _snapshot_to_context(self, snapshot: DiagnosticSnapshot) -> dict:
        """
        Produce a compact, readable representation of the snapshot
        for inclusion in the system prompt. Excludes raw connector
        data to keep the context window manageable.
        """
        ctx: dict = {
            "source": snapshot.source_connector,
            "device_id": snapshot.device_id,
            "captured_at": snapshot.captured_at,
            "overall_severity": snapshot.overall_severity.value,
        }

        if snapshot.findings:
            ctx["findings"] = [
                {
                    "severity": f.severity.value,
                    "category": f.category.value,
                    "title": f.title,
                    "description": f.description,
                    "resolution": f.resolution,
                    "technical_detail": f.technical_detail,
                    "auto_fixable": f.is_auto_fixable,
                }
                for f in sorted(
                    snapshot.findings,
                    key=lambda x: (
                        ["critical", "warning", "info", "ok"].index(x.severity.value)
                        if x.severity.value in ["critical", "warning", "info", "ok"]
                        else 99
                    ),
                )
            ]

        if snapshot.network_quality:
            nq = snapshot.network_quality
            ctx["network_quality"] = {
                k: v for k, v in {
                    "gateway_latency_ms": nq.gateway_latency_ms,
                    "gateway_loss_percent": nq.gateway_loss_percent,
                    "destination_latency_ms": nq.destination_latency_ms,
                    "destination_loss_percent": nq.destination_loss_percent,
                    "destination_jitter_ms": nq.destination_jitter_ms,
                }.items() if v is not None
            }

        if snapshot.wifi:
            w = snapshot.wifi
            ctx["wifi"] = {
                k: v for k, v in {
                    "ssid": w.ssid,
                    "rssi_dbm": w.rssi_dbm,
                    "channel": w.channel,
                    "channel_width_mhz": w.channel_width_mhz,
                    "protocol": w.protocol,
                    "security": w.security,
                    "transmit_rate_mbps": w.transmit_rate_mbps,
                }.items() if v is not None and v != ""
            }

        if snapshot.system:
            s = snapshot.system
            ctx["system_health"] = {
                k: v for k, v in {
                    "cpu_percent": s.cpu_percent,
                    "memory_percent": s.memory_percent,
                    "disk_percent": s.disk_percent,
                    "thermal_state": s.thermal_state,
                    "uptime_seconds": s.uptime_seconds,
                    "battery_percent": s.battery_percent,
                }.items() if v is not None and v != ""
            }

        if snapshot.gateway:
            g = snapshot.gateway
            ctx["gateway"] = {
                k: v for k, v in {
                    "vendor": g.vendor,
                    "model": g.model,
                    "management_reachable": g.management_reachable,
                    "supports_integration": g.supports_integration,
                    "web_admin_url": g.web_admin_url,
                }.items() if v is not None and v != ""
            }

        return ctx

    @staticmethod
    def _build_messages(
        question: str,
        history: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        messages = list(history) if history else []
        messages.append({"role": "user", "content": question})
        return messages

    @staticmethod
    def _extract_sources(snapshot: DiagnosticSnapshot, answer: str) -> list[str]:
        """
        Return finding titles that are likely referenced in the answer.
        Used to surface which data points drove the response.
        """
        sources = []
        for finding in snapshot.findings:
            if (
                finding.title.lower() in answer.lower()
                or finding.description[:40].lower() in answer.lower()
            ):
                sources.append(finding.title)
        return sources

    @staticmethod
    def _suggest_follow_ups(snapshot: DiagnosticSnapshot) -> list[str]:
        """
        Generate contextual follow-up question suggestions based on
        what issues are present in the snapshot.
        """
        suggestions = []
        severity_titles = {
            f.severity: f.title
            for f in snapshot.findings
        }

        if Severity.WARNING in severity_titles:
            suggestions.append(
                f"How do I fix the {severity_titles[Severity.WARNING]} issue?"
            )
        if FindingCategory.SECURITY in {f.category for f in snapshot.findings}:
            suggestions.append("What are the security risks on my network?")
        if snapshot.network_quality and snapshot.network_quality.destination_jitter_ms:
            if snapshot.network_quality.destination_jitter_ms > 10:
                suggestions.append(
                    "Why is my connection jitter high and how does it affect calls?"
                )
        if snapshot.wifi and snapshot.wifi.rssi_dbm and snapshot.wifi.rssi_dbm < -70:
            suggestions.append("How can I improve my WiFi signal strength?")

        return suggestions[:3]
