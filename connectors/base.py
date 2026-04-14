"""
connectors/base.py

Abstract base class that every connector must implement.
A connector is responsible for two things only:
  1. Fetching raw data from its source (API call, file read, socket, etc.)
  2. Normalizing that raw data into a DiagnosticSnapshot

The agent layer never calls the source API directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.schema import DiagnosticSnapshot


class BaseConnector(ABC):
    """
    Contract that all connectors must satisfy.

    Subclasses own authentication, pagination, retry logic,
    and the mapping from source-specific fields to the
    normalized DiagnosticSnapshot schema.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this connector, e.g. 'network_weather'."""

    @abstractmethod
    def fetch(self, device_id: str) -> DiagnosticSnapshot:
        """
        Retrieve and normalize the latest diagnostic state for a device.

        Parameters
        ----------
        device_id:
            The connector-specific identifier for the target device.

        Returns
        -------
        DiagnosticSnapshot
            Normalized snapshot ready for the agent to reason over.

        Raises
        ------
        ConnectorAuthError
            Authentication failed or credentials are invalid.
        ConnectorNotFoundError
            The requested device does not exist or has no data.
        ConnectorError
            Any other failure during fetch or normalization.
        """

    def health_check(self) -> bool:
        """
        Verify the connector can reach its data source.
        Default implementation attempts a fetch with a known-good device_id.
        Subclasses may override with a cheaper probe (e.g. GET /health).
        """
        return True


class ConnectorError(Exception):
    """Base class for all connector failures."""


class ConnectorAuthError(ConnectorError):
    """Raised when authentication with the data source fails."""


class ConnectorNotFoundError(ConnectorError):
    """Raised when the requested resource does not exist."""
