"""
run_network_weather_demo.py

End-to-end demo: Network Weather snapshot -> Ollama LLM -> natural language report.
This is the "ghost in the web server" demo for Chapter 3.
"""

import sys
sys.path.insert(0, '/Users/sanket/netagent')

from connectors.mock_snapshot import MockSnapshotConnector
from connectors.ollama import OllamaConnector

FIXTURE = "/Users/sanket/netagent/fixtures/my_network.json"

def main():
    print("=== Network Weather + Ollama Demo ===\n")

    print("Fetching network snapshot...")
    connector = MockSnapshotConnector(FIXTURE)
    snapshot = connector.fetch("my-network-01")

    print(f"Source:   {snapshot.source_connector}")
    print(f"Severity: {snapshot.overall_severity.value}")
    print(f"Findings: {len(snapshot.findings)}")
    print()

    questions = [
        "Why are my Zoom calls failing?",
        "Is my network secure?",
        "What is the most urgent issue I should fix right now?",
    ]

    llm = OllamaConnector()

    for question in questions:
        print(f"Q: {question}")
        print("-" * 50)
        response = llm.query(snapshot, question)
        print(response)
        print()

if __name__ == "__main__":
    main()
