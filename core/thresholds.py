"""
core/thresholds.py

Configurable severity thresholds for connector findings.

Enterprise customers may have different tolerances for what constitutes
a WARNING vs CRITICAL. A startup might accept 80% CPU as normal while
a trading firm treats anything above 60% as critical.

Thresholds are defined per connector and loaded from:
1. Environment variables (SAVVY_THRESHOLDS_JSON)
2. A JSON config file (thresholds.json)
3. Built-in defaults as fallback

Usage:
    from core.thresholds import thresholds
    cpu_warn = thresholds.get("system_health", "cpu_warning", 80.0)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional, Union


_DEFAULTS: dict[str, dict[str, Any]] = {
    "system_health": {
        "cpu_warning": 80.0,
        "cpu_critical": 95.0,
        "memory_warning": 80.0,
        "memory_critical": 95.0,
        "disk_warning": 85.0,
        "disk_critical": 95.0,
        "battery_warning": 20,
        "battery_critical": 10,
        "latency_warning_ms": 100.0,
        "latency_critical_ms": 300.0,
        "loss_warning_percent": 1.0,
        "loss_critical_percent": 5.0,
    },
    "network_weather": {
        "dropout_warning": 10,
        "dropout_critical": 100,
        "rssi_warning_dbm": -70,
        "rssi_critical_dbm": -85,
        "latency_warning_ms": 100.0,
        "latency_critical_ms": 300.0,
        "loss_warning_percent": 1.0,
        "loss_critical_percent": 5.0,
    },
    "salesforce": {
        "stalled_opp_days_warning": 14,
        "stalled_opp_days_critical": 30,
        "past_close_date_days_critical": 0,
        "high_priority_cases_warning": 1,
        "high_priority_cases_critical": 5,
        "overdue_tasks_warning": 1,
        "overdue_tasks_critical": 10,
    },
    "monday_com": {
        "stuck_items_warning": 1,
        "stuck_items_critical": 5,
        "at_risk_items_warning": 1,
        "overdue_items_warning": 1,
    },
    "zoom": {
        "poor_quality_meetings_warning": 1,
        "poor_quality_meetings_critical": 5,
        "latency_warning_ms": 150.0,
        "latency_critical_ms": 300.0,
        "loss_warning_percent": 3.0,
        "loss_critical_percent": 8.0,
        "jitter_warning_ms": 40.0,
        "jitter_critical_ms": 80.0,
    },
    "google_meet": {
        "empty_meetings_warning": 1,
        "long_meeting_minutes_warning": 120,
        "long_meeting_minutes_critical": 240,
    },
}


class ThresholdConfig:
    """
    Manages severity thresholds per connector.

    Thresholds are loaded in priority order:
    1. Environment variable SAVVY_THRESHOLDS_JSON (JSON string)
    2. thresholds.json file in the working directory
    3. Built-in defaults

    Overrides are merged at the connector level so you only need to
    specify the values you want to change.
    """

    def __init__(self) -> None:
        self._config: dict[str, dict[str, Any]] = {}
        self._load()

    def get(
        self,
        connector: str,
        key: str,
        default: Optional[Union[float, int]] = None,
    ) -> Any:
        """
        Get a threshold value for a connector.

        Falls back to built-in defaults if not configured.
        Falls back to the provided default if not in built-ins either.
        """
        connector_config = self._config.get(connector, {})
        if key in connector_config:
            return connector_config[key]
        builtin = _DEFAULTS.get(connector, {})
        if key in builtin:
            return builtin[key]
        return default

    def set(self, connector: str, key: str, value: Any) -> None:
        """Override a threshold at runtime."""
        if connector not in self._config:
            self._config[connector] = {}
        self._config[connector][key] = value

    def get_connector_thresholds(self, connector: str) -> dict[str, Any]:
        """Return all thresholds for a connector, merged with defaults."""
        defaults = _DEFAULTS.get(connector, {})
        overrides = self._config.get(connector, {})
        return {**defaults, **overrides}

    def _load(self) -> None:
        env_json = os.environ.get("SAVVY_THRESHOLDS_JSON", "")
        if env_json:
            try:
                self._config = json.loads(env_json)
                return
            except json.JSONDecodeError:
                pass

        config_path = Path("thresholds.json")
        if config_path.exists():
            try:
                with open(config_path) as f:
                    self._config = json.load(f)
                return
            except (json.JSONDecodeError, OSError):
                pass

        self._config = {}

    def export_defaults(self, path: Union[str, Path]) -> None:
        """
        Export the built-in defaults to a JSON file as a starting
        point for enterprise customization.
        """
        with open(path, "w") as f:
            json.dump(_DEFAULTS, f, indent=2)


# Global threshold config instance
thresholds = ThresholdConfig()
