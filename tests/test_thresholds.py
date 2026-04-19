"""
tests/test_thresholds.py

Unit tests for the threshold configuration system.
"""

from __future__ import annotations

import json
import os
import pytest
from pathlib import Path

from core.thresholds import ThresholdConfig, _DEFAULTS


@pytest.fixture
def config() -> ThresholdConfig:
    return ThresholdConfig()


class TestDefaults:
    def test_returns_builtin_default(self, config):
        val = config.get("system_health", "cpu_warning")
        assert val == 80.0

    def test_returns_provided_default_when_unknown(self, config):
        val = config.get("system_health", "nonexistent_key", 42.0)
        assert val == 42.0

    def test_returns_none_when_no_default(self, config):
        val = config.get("unknown_connector", "unknown_key")
        assert val is None

    def test_all_connectors_have_defaults(self):
        expected = {
            "system_health", "network_weather", "salesforce",
            "monday_com", "zoom", "google_meet"
        }
        assert set(_DEFAULTS.keys()) == expected

    def test_get_connector_thresholds_returns_all(self, config):
        thresholds = config.get_connector_thresholds("system_health")
        assert "cpu_warning" in thresholds
        assert "cpu_critical" in thresholds
        assert "memory_warning" in thresholds
        assert thresholds["cpu_warning"] == 80.0


class TestOverrides:
    def test_runtime_override(self, config):
        config.set("system_health", "cpu_warning", 60.0)
        assert config.get("system_health", "cpu_warning") == 60.0

    def test_override_does_not_affect_other_keys(self, config):
        config.set("system_health", "cpu_warning", 60.0)
        assert config.get("system_health", "cpu_critical") == 95.0

    def test_override_does_not_affect_other_connectors(self, config):
        config.set("system_health", "cpu_warning", 60.0)
        zoom_latency = config.get("zoom", "latency_warning_ms")
        assert zoom_latency == 150.0

    def test_get_connector_thresholds_includes_overrides(self, config):
        config.set("system_health", "cpu_warning", 55.0)
        thresholds = config.get_connector_thresholds("system_health")
        assert thresholds["cpu_warning"] == 55.0
        assert thresholds["cpu_critical"] == 95.0


class TestEnvironmentLoading:
    def test_loads_from_env_variable(self, tmp_path):
        custom = {"system_health": {"cpu_warning": 50.0}}
        os.environ["SAVVY_THRESHOLDS_JSON"] = json.dumps(custom)
        try:
            config = ThresholdConfig()
            assert config.get("system_health", "cpu_warning") == 50.0
            assert config.get("system_health", "cpu_critical") == 95.0
        finally:
            del os.environ["SAVVY_THRESHOLDS_JSON"]

    def test_loads_from_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        custom = {"zoom": {"latency_warning_ms": 200.0}}
        (tmp_path / "thresholds.json").write_text(json.dumps(custom))
        config = ThresholdConfig()
        assert config.get("zoom", "latency_warning_ms") == 200.0
        assert config.get("zoom", "latency_critical_ms") == 300.0

    def test_env_takes_priority_over_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "thresholds.json").write_text(
            json.dumps({"system_health": {"cpu_warning": 70.0}})
        )
        os.environ["SAVVY_THRESHOLDS_JSON"] = json.dumps(
            {"system_health": {"cpu_warning": 55.0}}
        )
        try:
            config = ThresholdConfig()
            assert config.get("system_health", "cpu_warning") == 55.0
        finally:
            del os.environ["SAVVY_THRESHOLDS_JSON"]


class TestExport:
    def test_export_defaults_creates_file(self, tmp_path):
        config = ThresholdConfig()
        output = tmp_path / "thresholds.json"
        config.export_defaults(output)
        assert output.exists()
        data = json.loads(output.read_text())
        assert "system_health" in data
        assert "salesforce" in data
        assert data["system_health"]["cpu_warning"] == 80.0
