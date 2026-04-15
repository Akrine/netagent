"""
run_demo.py

End-to-end demo using the mock connector and a real Anthropic API key.
No Network Weather credentials required.

Usage:
    export ANTHROPIC_API_KEY=your_key_here
    python3 run_demo.py
"""

from __future__ import annotations

import os
import sys
from dotenv import load_dotenv

load_dotenv()

from connectors.mock_snapshot import MockSnapshotConnector
from agents.diagnostic import DiagnosticAgent
from core.context import ConversationContext
from core.schema import Severity

_SEVERITY_LABEL = {
    Severity.OK: "OK",
    Severity.INFO: "INFO",
    Severity.WARNING: "WARNING",
    Severity.CRITICAL: "CRITICAL",
}

def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set. Add it to .env or export it.")
        sys.exit(1)

    print("Loading snapshot from fixtures/my_network.json...")
    connector = MockSnapshotConnector("fixtures/my_network.json")
    snapshot = connector.fetch("local-device")

    print(f"Snapshot loaded. Overall severity: {_SEVERITY_LABEL[snapshot.overall_severity]}")
    print(f"Findings: {len(snapshot.findings)}")
    for f in snapshot.findings:
        print(f"  [{f.severity.value.upper():8}] {f.title}")

    print("\nInitializing agent...")
    agent = DiagnosticAgent()
    context = ConversationContext()

    demo_questions = [
        "Why does my Zoom keep freezing?",
        "What is the most urgent issue I should fix?",
        "Is my network secure?",
    ]

    print("\n" + "=" * 60)
    print("DEMO CONVERSATION")
    print("=" * 60)

    for question in demo_questions:
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

if __name__ == "__main__":
    main()
