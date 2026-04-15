"""
connectors/mock_snapshot.py

Mock connector that serves a static snapshot for local development
and testing without requiring live API credentials.

Accepts either a JSON file path or a raw dict at construction time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from connectors.base import BaseConnector
from connectors.network_weather import NetworkWeatherConnector
from core.schema import DiagnosticSnapshot


class MockSnapshotConnector(BaseConnector):
    """
    Serves a pre-recorded snapshot for offline development.
    Uses the Network Weather normalizer so the output is identical
    to what the live connector would produce.
    """

    def __init__(self, snapshot_data: Union[dict, str, Path]) -> None:
        if isinstance(snapshot_data, (str, Path)):
            with open(snapshot_data) as f:
                self._raw = json.load(f)
        else:
            self._raw = snapshot_data

        self._normalizer = NetworkWeatherConnector.__new__(
            NetworkWeatherConnector
        )
        self._normalizer._client_id = "mock"
        self._normalizer._client_secret = "mock"

    @property
    def name(self) -> str:
        return "mock_snapshot"

    def fetch(self, device_id: str) -> DiagnosticSnapshot:
        snapshot = self._normalizer._normalize(device_id, self._raw)
        snapshot.source_connector = self.name
        return snapshot

    def health_check(self) -> bool:
        return True
