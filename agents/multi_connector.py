"""
agents/multi_connector.py

Multi-connector agent that queries multiple connectors simultaneously
and synthesizes a unified response across all data sources.

This is the core of the Savvy control plane vision: ask one question,
get a single coherent answer that spans all connected systems.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
from typing import Optional

import anthropic

from connectors.base import BaseConnector, ConnectorError
from core.logger import ConversationLogger
from core.schema import DiagnosticSnapshot, Severity


_DEFAULT_MODEL = "claude-sonnet-4-20250514"

_SYSTEM_TEMPLATE = """\
You are Savvy, an AI control plane for enterprise software. You have access to
real-time data from multiple connected systems simultaneously.

Your job is to synthesize information across all connected systems and answer
the user's question with a unified, coherent response. When relevant, highlight
relationships between systems — for example, if network issues could be causing
application slowdowns.

Always prioritize critical and warning severity findings. Be specific and
reference actual values from the data. If one system has no issues, say so
briefly and focus on what does need attention.

Connected systems and their current state:
{systems_summary}

--- FULL DIAGNOSTIC DATA ---
{all_snapshots}
--- END DIAGNOSTIC DATA ---
"""


class MultiConnectorAgent:
    """
    Queries multiple connectors in parallel and synthesizes a unified
    response using Claude as the reasoning engine.

    This is Savvy operating as a control plane — one question answered
    across all connected systems simultaneously.
    """

    def __init__(
        self,
        connectors: dict[str, BaseConnector],
        api_key: Optional[str] = None,
        model: str = _DEFAULT_MODEL,
        max_tokens: int = 2048,
        enable_logging: bool = True,
    ) -> None:
        self._connectors = connectors
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        )
        self._model = model
        self._max_tokens = max_tokens
        self._logger = ConversationLogger() if enable_logging else None

    def query(
        self,
        question: str,
        device_ids: Optional[dict[str, str]] = None,
        history: list[dict[str, str]] | None = None,
    ) -> dict:
        """
        Query all connected connectors and return a unified response.

        Parameters
        ----------
        question:
            Natural language question to answer across all systems.
        device_ids:
            Optional mapping of connector name to device ID.
            Defaults to 'local' for all connectors.
        history:
            Prior conversation turns for multi-turn context.

        Returns
        -------
        dict with keys:
            answer: unified natural language response
            snapshots: dict of connector name to snapshot
            errors: dict of connector name to error message
            overall_severity: highest severity across all systems
        """
        device_ids = device_ids or {}
        snapshots, errors = self._fetch_all(device_ids)

        if not snapshots:
            return {
                "answer": "No data could be retrieved from any connected system.",
                "snapshots": {},
                "errors": errors,
                "overall_severity": "unknown",
            }

        system_prompt = self._build_system_prompt(snapshots)
        messages = list(history) if history else []
        messages.append({"role": "user", "content": question})

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system_prompt,
            messages=messages,
        )

        answer = response.content[0].text
        overall = self._compute_overall_severity(snapshots)

        if self._logger:
            for connector_name, snapshot in snapshots.items():
                self._logger.log(
                    snapshot=snapshot,
                    question=question,
                    answer=answer,
                    history=history,
                )

        return {
            "answer": answer,
            "snapshots": snapshots,
            "errors": errors,
            "overall_severity": overall.value,
        }

    def _fetch_all(
        self,
        device_ids: dict[str, str],
    ) -> tuple[dict[str, DiagnosticSnapshot], dict[str, str]]:
        snapshots = {}
        errors = {}

        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(
                    self._fetch_one,
                    name,
                    connector,
                    device_ids.get(name, "local"),
                ): name
                for name, connector in self._connectors.items()
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    snapshot = future.result()
                    snapshots[name] = snapshot
                except Exception as exc:
                    errors[name] = str(exc)

        return snapshots, errors

    @staticmethod
    def _fetch_one(
        name: str,
        connector: BaseConnector,
        device_id: str,
    ) -> DiagnosticSnapshot:
        return connector.fetch(device_id)

    def _build_system_prompt(
        self,
        snapshots: dict[str, DiagnosticSnapshot],
    ) -> str:
        systems_summary = []
        for name, snapshot in snapshots.items():
            finding_count = len(snapshot.findings)
            severity = snapshot.overall_severity.value.upper()
            systems_summary.append(
                f"- {name}: {severity}, {finding_count} finding(s)"
            )

        all_data = {}
        for name, snapshot in snapshots.items():
            all_data[name] = {
                "overall_severity": snapshot.overall_severity.value,
                "captured_at": snapshot.captured_at,
                "findings": [
                    {
                        "severity": f.severity.value,
                        "category": f.category.value,
                        "title": f.title,
                        "description": f.description,
                        "resolution": f.resolution,
                        "technical_detail": f.technical_detail,
                    }
                    for f in sorted(
                        snapshot.findings,
                        key=lambda x: ["critical", "warning", "info", "ok"].index(
                            x.severity.value
                        ) if x.severity.value in ["critical", "warning", "info", "ok"] else 99,
                    )
                ],
                "network_quality": {
                    "gateway_latency_ms": snapshot.network_quality.gateway_latency_ms,
                    "destination_latency_ms": snapshot.network_quality.destination_latency_ms,
                    "destination_loss_percent": snapshot.network_quality.destination_loss_percent,
                } if snapshot.network_quality else None,
                "system": {
                    "cpu_percent": snapshot.system.cpu_percent,
                    "memory_percent": snapshot.system.memory_percent,
                    "disk_percent": snapshot.system.disk_percent,
                    "battery_percent": snapshot.system.battery_percent,
                } if snapshot.system else None,
            }

        return _SYSTEM_TEMPLATE.format(
            systems_summary="\n".join(systems_summary),
            all_snapshots=json.dumps(all_data, indent=2),
        )

    @staticmethod
    def _compute_overall_severity(
        snapshots: dict[str, DiagnosticSnapshot],
    ) -> Severity:
        all_severities = [s.overall_severity for s in snapshots.values()]
        if Severity.CRITICAL in all_severities:
            return Severity.CRITICAL
        if Severity.WARNING in all_severities:
            return Severity.WARNING
        if Severity.INFO in all_severities:
            return Severity.INFO
        return Severity.OK
