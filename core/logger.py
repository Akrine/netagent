"""
core/logger.py

Conversation logging pipeline for Oumi fine-tuning data collection.

Every agent conversation is logged as a structured JSON record.
Each record contains the connector, snapshot context, question,
answer, and metadata needed to evaluate and filter training examples.

Log format is newline-delimited JSON (NDJSON) — one record per line —
which is directly ingestible by Oumi's training pipeline.

Logs are written to logs/conversations.ndjson by default.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.schema import DiagnosticSnapshot


_DEFAULT_LOG_PATH = Path("logs/conversations.ndjson")


class ConversationLogger:
    """
    Logs agent conversations to disk in NDJSON format for Oumi training.

    Each log entry is a self-contained training example with:
    - A system prompt describing the connector and snapshot context
    - The user question
    - The agent answer
    - Metadata for filtering and quality assessment
    """

    def __init__(self, log_path: Path | str = _DEFAULT_LOG_PATH) -> None:
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        snapshot: DiagnosticSnapshot,
        question: str,
        answer: str,
        history: list[dict[str, str]] | None = None,
        latency_ms: Optional[float] = None,
    ) -> str:
        """
        Write a single conversation turn to the log file.

        Parameters
        ----------
        snapshot:
            The DiagnosticSnapshot the agent reasoned over.
        question:
            The user's natural language question.
        answer:
            The agent's response.
        history:
            Prior conversation turns if this is a multi-turn session.
        latency_ms:
            Time taken for the agent to respond in milliseconds.

        Returns
        -------
        str
            The unique ID assigned to this log entry.
        """
        entry_id = str(uuid.uuid4())
        entry = {
            "id": entry_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connector": snapshot.source_connector,
            "device_id": snapshot.device_id,
            "overall_severity": snapshot.overall_severity.value,
            "findings_count": len(snapshot.findings),
            "findings_summary": [
                {
                    "severity": f.severity.value,
                    "category": f.category.value,
                    "title": f.title,
                }
                for f in snapshot.findings
            ],
            "conversation": {
                "history_turns": len(history) if history else 0,
                "question": question,
                "answer": answer,
            },
            "training": {
                "system_prompt": self._build_training_system_prompt(snapshot),
                "messages": self._build_training_messages(
                    question, answer, history
                ),
            },
            "metadata": {
                "latency_ms": latency_ms,
                "has_findings": snapshot.has_issues(),
                "connector_version": "1.0",
            },
        }

        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        return entry_id

    def count(self) -> int:
        """Return the number of logged conversations."""
        if not self._log_path.exists():
            return 0
        with open(self._log_path, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def read_all(self) -> list[dict]:
        """Return all logged entries as a list of dicts."""
        if not self._log_path.exists():
            return []
        entries = []
        with open(self._log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries

    def export_oumi_dataset(self, output_path: Path | str) -> int:
        """
        Export logged conversations in Oumi-compatible fine-tuning format.

        Oumi expects a JSONL file where each line is a training example
        with a messages array in the OpenAI chat format.

        Parameters
        ----------
        output_path:
            Path to write the Oumi dataset file.

        Returns
        -------
        int
            Number of examples exported.
        """
        entries = self.read_all()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for entry in entries:
                training = entry.get("training", {})
                system_prompt = training.get("system_prompt", "")
                messages = training.get("messages", [])
                if not messages:
                    continue

                oumi_example = {
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        *messages,
                    ],
                    "metadata": {
                        "id": entry.get("id"),
                        "connector": entry.get("connector"),
                        "severity": entry.get("overall_severity"),
                        "timestamp": entry.get("timestamp"),
                    },
                }
                f.write(json.dumps(oumi_example) + "\n")
                count += 1

        return count

    @staticmethod
    def _build_training_system_prompt(snapshot: DiagnosticSnapshot) -> str:
        findings_text = ""
        if snapshot.findings:
            findings_text = "\n".join(
                f"- [{f.severity.value.upper()}] {f.title}: {f.description}"
                for f in snapshot.findings
            )
        else:
            findings_text = "No issues found."

        return (
            f"You are a diagnostic assistant for {snapshot.source_connector} data. "
            f"Answer questions accurately based on the following diagnostic snapshot.\n\n"
            f"Overall severity: {snapshot.overall_severity.value}\n"
            f"Findings:\n{findings_text}"
        )

    @staticmethod
    def _build_training_messages(
        question: str,
        answer: str,
        history: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        messages = []
        if history:
            for turn in history:
                if turn.get("role") in ("user", "assistant"):
                    messages.append(turn)
        messages.append({"role": "user", "content": question})
        messages.append({"role": "assistant", "content": answer})
        return messages
