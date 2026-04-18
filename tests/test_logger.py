"""
tests/test_logger.py

Unit tests for the conversation logging pipeline.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from core.logger import ConversationLogger
from core.schema import (
    DiagnosticSnapshot,
    Finding,
    FindingCategory,
    Severity,
)


@pytest.fixture
def tmp_log(tmp_path) -> ConversationLogger:
    return ConversationLogger(log_path=tmp_path / "test.ndjson")


@pytest.fixture
def sample_snapshot() -> DiagnosticSnapshot:
    return DiagnosticSnapshot(
        source_connector="network_weather",
        device_id="device-abc",
        captured_at="2026-04-16T00:00:00Z",
        findings=[
            Finding(
                id="F1",
                severity=Severity.WARNING,
                category=FindingCategory.WIFI,
                title="Connection dropouts",
                description="220 periods where your internet froze.",
                resolution="Restart your router.",
                technical_detail="RTT avg: 69.8ms",
            )
        ],
        overall_severity=Severity.WARNING,
    )


class TestLogging:
    def test_log_creates_file(self, tmp_log, sample_snapshot):
        tmp_log.log(sample_snapshot, "Why is my Zoom freezing?", "Because of dropouts.")
        assert tmp_log._log_path.exists()

    def test_log_increments_count(self, tmp_log, sample_snapshot):
        assert tmp_log.count() == 0
        tmp_log.log(sample_snapshot, "Q1", "A1")
        assert tmp_log.count() == 1
        tmp_log.log(sample_snapshot, "Q2", "A2")
        assert tmp_log.count() == 2

    def test_log_entry_structure(self, tmp_log, sample_snapshot):
        tmp_log.log(sample_snapshot, "Why is my Zoom freezing?", "Because of dropouts.")
        entries = tmp_log.read_all()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["connector"] == "network_weather"
        assert entry["device_id"] == "device-abc"
        assert entry["overall_severity"] == "warning"
        assert entry["findings_count"] == 1
        assert entry["conversation"]["question"] == "Why is my Zoom freezing?"
        assert entry["conversation"]["answer"] == "Because of dropouts."
        assert "id" in entry
        assert "timestamp" in entry

    def test_log_entry_has_training_data(self, tmp_log, sample_snapshot):
        tmp_log.log(sample_snapshot, "Why is my Zoom freezing?", "Because of dropouts.")
        entries = tmp_log.read_all()
        training = entries[0]["training"]
        assert "system_prompt" in training
        assert "messages" in training
        messages = training["messages"]
        assert messages[-2]["role"] == "user"
        assert messages[-1]["role"] == "assistant"
        assert "network_weather" in training["system_prompt"]

    def test_log_with_history(self, tmp_log, sample_snapshot):
        history = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
        ]
        tmp_log.log(sample_snapshot, "Follow up?", "Follow up answer.", history=history)
        entries = tmp_log.read_all()
        assert entries[0]["conversation"]["history_turns"] == 2
        messages = entries[0]["training"]["messages"]
        assert len(messages) == 4

    def test_log_with_latency(self, tmp_log, sample_snapshot):
        tmp_log.log(sample_snapshot, "Q", "A", latency_ms=142.5)
        entries = tmp_log.read_all()
        assert entries[0]["metadata"]["latency_ms"] == 142.5

    def test_findings_summary_in_log(self, tmp_log, sample_snapshot):
        tmp_log.log(sample_snapshot, "Q", "A")
        entries = tmp_log.read_all()
        summary = entries[0]["findings_summary"]
        assert len(summary) == 1
        assert summary[0]["title"] == "Connection dropouts"
        assert summary[0]["severity"] == "warning"


class TestOumiExport:
    def test_export_produces_valid_jsonl(self, tmp_log, tmp_path, sample_snapshot):
        tmp_log.log(sample_snapshot, "Q1", "A1")
        tmp_log.log(sample_snapshot, "Q2", "A2")
        output = tmp_path / "oumi_dataset.jsonl"
        count = tmp_log.export_oumi_dataset(output)
        assert count == 2
        assert output.exists()
        with open(output) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert len(lines) == 2
        for line in lines:
            assert "messages" in line
            assert line["messages"][0]["role"] == "system"
            assert "metadata" in line

    def test_export_empty_log(self, tmp_log, tmp_path):
        output = tmp_path / "empty.jsonl"
        count = tmp_log.export_oumi_dataset(output)
        assert count == 0
