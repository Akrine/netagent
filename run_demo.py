"""
run_demo.py

End-to-end demo showing the same agent working across two different
connectors: Network Weather (network diagnostics) and SystemHealth
(local machine metrics).

This demonstrates the core framework principle: the agent has no
knowledge of where data comes from. Swap the connector, get the
same reasoning capability over different data.

Usage:
    export ANTHROPIC_API_KEY=your_key_here
    python3 run_demo.py
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()

from agents.diagnostic import DiagnosticAgent
from connectors.mock_snapshot import MockSnapshotConnector
from connectors.system_health import SystemHealthConnector
from core.context import ConversationContext
from core.schema import DiagnosticSnapshot, Severity

_SEVERITY_LABEL = {
    Severity.OK: "OK",
    Severity.INFO: "INFO",
    Severity.WARNING: "WARNING",
    Severity.CRITICAL: "CRITICAL",
}


def print_separator(title: str) -> None:
    width = 60
    print("\n" + "=" * width)
    print(f" {title}")
    print("=" * width)


def print_snapshot_summary(snapshot: DiagnosticSnapshot) -> None:
    print(f"Connector:      {snapshot.source_connector}")
    print(f"Device:         {snapshot.device_id}")
    print(f"Captured at:    {snapshot.captured_at}")
    print(f"Overall status: {_SEVERITY_LABEL[snapshot.overall_severity]}")
    if snapshot.findings:
        print(f"Findings:       {len(snapshot.findings)}")
        for f in snapshot.findings:
            print(f"  [{f.severity.value.upper():8}] {f.title}")
    else:
        print("Findings:       None")


def run_conversation(
    agent: DiagnosticAgent,
    snapshot: DiagnosticSnapshot,
    questions: list[str],
) -> None:
    context = ConversationContext()
    for question in questions:
        print(f"\nQ: {question}")
        context.add("user", question)
        response = agent.query(
            snapshot=snapshot,
            question=question,
            history=context.to_messages()[:-1],
        )
        print(f"\nA: {response.answer}")
        context.add("assistant", response.answer)
        if response.follow_up_suggestions:
            print("\nSuggested follow-ups:")
            for s in response.follow_up_suggestions:
                print(f"  - {s}")
        print("-" * 60)


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set. Add it to .env or export it.")
        sys.exit(1)

    agent = DiagnosticAgent()

    print_separator("CONNECTOR 1: Network Weather (recorded snapshot)")
    nw_connector = MockSnapshotConnector("fixtures/my_network.json")
    nw_snapshot = nw_connector.fetch("local-device")
    print_snapshot_summary(nw_snapshot)
    run_conversation(agent, nw_snapshot, [
        "Why does my Zoom keep freezing?",
        "What is the most urgent issue I should fix?",
    ])

    print_separator("CONNECTOR 2: System Health (live machine data)")
    sys_connector = SystemHealthConnector()
    sys_snapshot = sys_connector.fetch("local")
    print_snapshot_summary(sys_snapshot)
    run_conversation(agent, sys_snapshot, [
        "How is my machine performing right now?",
        "Is there anything I should be concerned about?",
    ])

    print_separator("FRAMEWORK SUMMARY")
    print("Same agent. Same reasoning layer. Two completely different data sources.")
    print(f"Network Weather findings: {len(nw_snapshot.findings)}")
    print(f"System Health findings:   {len(sys_snapshot.findings)}")
    print(f"Combined coverage:        {len(nw_snapshot.findings) + len(sys_snapshot.findings)} issues across network and machine health")


if __name__ == "__main__":
    main()
