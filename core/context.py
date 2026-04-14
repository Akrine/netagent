"""
core/context.py

Conversation context management.

Maintains the rolling history of a conversation session and
provides serialization helpers for passing context to the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Role = Literal["user", "assistant", "system"]


@dataclass
class Turn:
    role: Role
    content: str


class ConversationContext:
    """
    Maintains conversation history for a single session.

    History is bounded to avoid exceeding LLM context windows.
    When the limit is reached, the oldest non-system turns are evicted.
    """

    def __init__(self, max_turns: int = 20, system_prompt: str = "") -> None:
        self._max_turns = max_turns
        self._turns: list[Turn] = []
        if system_prompt:
            self._turns.append(Turn(role="system", content=system_prompt))

    def add(self, role: Role, content: str) -> None:
        self._turns.append(Turn(role=role, content=content))
        self._evict_if_needed()

    def to_messages(self) -> list[dict[str, str]]:
        """Return history in the format expected by OpenAI-compatible APIs."""
        return [{"role": t.role, "content": t.content} for t in self._turns]

    def clear(self) -> None:
        """Reset history, preserving the system prompt if present."""
        system_turns = [t for t in self._turns if t.role == "system"]
        self._turns = system_turns

    def __len__(self) -> int:
        return len(self._turns)

    def _evict_if_needed(self) -> None:
        non_system = [t for t in self._turns if t.role != "system"]
        system = [t for t in self._turns if t.role == "system"]
        if len(non_system) > self._max_turns:
            non_system = non_system[-(self._max_turns):]
        self._turns = system + non_system
