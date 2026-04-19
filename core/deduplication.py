"""
core/deduplication.py

Finding deduplication and correlation for multi-connector queries.

When multiple connectors surface findings simultaneously, the same
underlying issue may appear multiple times. This module correlates
related findings and deduplicates them so the agent receives a clean,
non-redundant view of the system state.

Correlation is based on:
- Category similarity (two CONNECTIVITY findings likely related)
- Severity alignment (both WARNING or both CRITICAL)
- Semantic similarity of titles (simple keyword overlap)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.schema import DiagnosticSnapshot, Finding, FindingCategory, Severity


@dataclass
class CorrelatedFinding:
    """
    A finding that may have been observed by multiple connectors.

    primary:        The most severe / most informative instance
    duplicates:     Other findings that were correlated with primary
    connectors:     Which connectors observed this issue
    """
    primary: Finding
    duplicates: list[Finding] = field(default_factory=list)
    connectors: list[str] = field(default_factory=list)

    @property
    def occurrence_count(self) -> int:
        return 1 + len(self.duplicates)

    @property
    def is_cross_connector(self) -> bool:
        return len(set(self.connectors)) > 1


class FindingDeduplicator:
    """
    Deduplicates and correlates findings across multiple snapshots.

    Two findings are considered duplicates if:
    1. They share the same category AND
    2. They share the same severity AND
    3. Their titles share at least one significant keyword

    The finding with the most technical detail is kept as primary.
    """

    # Words too common to be meaningful for correlation
    _STOP_WORDS = {
        "the", "a", "an", "and", "or", "is", "are", "was", "were",
        "in", "on", "at", "to", "for", "of", "with", "your", "this",
        "that", "has", "have", "had", "been", "be", "not", "no",
        "high", "low", "issues", "issue", "detected", "found",
    }

    def deduplicate(
        self,
        snapshots: dict[str, DiagnosticSnapshot],
    ) -> list[CorrelatedFinding]:
        """
        Deduplicate findings across multiple snapshots.

        Parameters
        ----------
        snapshots:
            Mapping of connector name to DiagnosticSnapshot.

        Returns
        -------
        List of CorrelatedFinding objects, sorted by severity
        (critical first) then by occurrence count (most observed first).
        """
        all_findings: list[tuple[str, Finding]] = []
        for connector_name, snapshot in snapshots.items():
            for finding in snapshot.findings:
                all_findings.append((connector_name, finding))

        if not all_findings:
            return []

        correlated: list[CorrelatedFinding] = []

        for connector_name, finding in all_findings:
            matched = self._find_match(finding, correlated)
            if matched:
                matched.duplicates.append(finding)
                if connector_name not in matched.connectors:
                    matched.connectors.append(connector_name)
                if self._is_more_informative(finding, matched.primary):
                    matched.primary = finding
            else:
                correlated.append(CorrelatedFinding(
                    primary=finding,
                    connectors=[connector_name],
                ))

        return sorted(
            correlated,
            key=lambda c: (
                self._severity_rank(c.primary.severity),
                -c.occurrence_count,
            ),
        )

    def _find_match(
        self,
        finding: Finding,
        existing: list[CorrelatedFinding],
    ) -> Optional[CorrelatedFinding]:
        for correlated in existing:
            if self._are_duplicates(finding, correlated.primary):
                return correlated
        return None

    def _are_duplicates(self, a: Finding, b: Finding) -> bool:
        if a.severity != b.severity:
            return False
        if a.category != b.category:
            return False
        keywords_a = self._keywords(a.title)
        keywords_b = self._keywords(b.title)
        if not keywords_a or not keywords_b:
            return False
        overlap = keywords_a & keywords_b
        return len(overlap) >= 1

    def _keywords(self, text: str) -> set[str]:
        words = text.lower().split()
        return {w.strip(".,;:!?") for w in words} - self._STOP_WORDS

    @staticmethod
    def _is_more_informative(candidate: Finding, current: Finding) -> bool:
        return len(candidate.technical_detail) > len(current.technical_detail)

    @staticmethod
    def _severity_rank(severity: Severity) -> int:
        return {
            Severity.CRITICAL: 0,
            Severity.WARNING: 1,
            Severity.INFO: 2,
            Severity.OK: 3,
        }.get(severity, 99)


def deduplicate_snapshots(
    snapshots: dict[str, DiagnosticSnapshot],
) -> list[CorrelatedFinding]:
    """Convenience function wrapping FindingDeduplicator.deduplicate()."""
    return FindingDeduplicator().deduplicate(snapshots)
