"""
agents/base.py

Abstract base class for all agents.

An agent receives a DiagnosticSnapshot and a natural language query,
reasons over the data, and returns a natural language response.
The agent has no knowledge of where the data came from.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from core.schema import DiagnosticSnapshot


@dataclass
class AgentResponse:
    """Structured response from an agent."""
    answer: str
    confidence: float = 1.0
    sources: list[str] = field(default_factory=list)
    follow_up_suggestions: list[str] = field(default_factory=list)


class BaseAgent(ABC):
    """
    Contract that all agents must satisfy.

    Agents are stateless with respect to the snapshot data.
    Conversation history is managed externally by the interface layer
    and passed in on each call.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this agent."""

    @abstractmethod
    def query(
        self,
        snapshot: DiagnosticSnapshot,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> AgentResponse:
        """
        Answer a natural language question about a diagnostic snapshot.

        Parameters
        ----------
        snapshot:
            Normalized diagnostic data from any connector.
        question:
            The user's natural language question.
        history:
            Prior conversation turns as list of {"role": ..., "content": ...}
            dicts, oldest first. None means no prior context.

        Returns
        -------
        AgentResponse
            The agent's answer and associated metadata.
        """
