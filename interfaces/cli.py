"""
interfaces/cli.py

Interactive command-line interface for the diagnostic agent.

Loads connector and agent from environment configuration,
fetches the latest snapshot for a given device, and starts
a conversation loop in the terminal.

Usage:
    python -m interfaces.cli --connector network_weather --device <client_id>
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from agents.diagnostic import DiagnosticAgent
from connectors.network_weather import NetworkWeatherConnector
from connectors.base import ConnectorError
from core.context import ConversationContext
from core.schema import DiagnosticSnapshot, Severity

load_dotenv()

_SEVERITY_INDICATOR = {
    Severity.OK: "OK",
    Severity.INFO: "INFO",
    Severity.WARNING: "WARNING",
    Severity.CRITICAL: "CRITICAL",
}


def _print_snapshot_summary(snapshot: DiagnosticSnapshot) -> None:
    indicator = _SEVERITY_INDICATOR[snapshot.overall_severity]
    print(f"\nSnapshot captured: {snapshot.captured_at}")
    print(f"Overall status:    {indicator}")

    if not snapshot.findings:
        print("Findings:          None")
    else:
        print(f"Findings:          {len(snapshot.findings)}")
        for f in snapshot.findings:
            print(f"  [{f.severity.value.upper():8}] {f.title}")

    if snapshot.wifi:
        w = snapshot.wifi
        print(f"WiFi:              {w.ssid}  {w.rssi_dbm} dBm  {w.protocol}")

    if snapshot.network_quality:
        nq = snapshot.network_quality
        if nq.destination_latency_ms is not None:
            print(f"Latency:           {nq.destination_latency_ms:.1f} ms")
        if nq.destination_loss_percent is not None:
            print(f"Packet loss:       {nq.destination_loss_percent:.1f}%")

    print()


def _print_follow_ups(suggestions: list[str]) -> None:
    if not suggestions:
        return
    print("\nSuggested follow-up questions:")
    for i, s in enumerate(suggestions, 1):
        print(f"  {i}. {s}")


def run_cli(connector_name: str, device_id: str) -> None:
    print(f"Connecting to {connector_name}...")

    if connector_name == "network_weather":
        connector = NetworkWeatherConnector()
    else:
        print(f"Unknown connector: {connector_name}", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching snapshot for device {device_id}...")
    try:
        snapshot = connector.fetch(device_id)
    except ConnectorError as exc:
        print(f"Failed to fetch snapshot: {exc}", file=sys.stderr)
        sys.exit(1)

    _print_snapshot_summary(snapshot)

    agent = DiagnosticAgent()
    context = ConversationContext(
        system_prompt=(
            "You are a helpful network diagnostic assistant. "
            "Be concise, accurate, and actionable."
        )
    )

    print("Ready. Type your question or 'quit' to exit.")
    print("-" * 60)

    while True:
        try:
            question = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit", "q"):
            print("Exiting.")
            break

        context.add("user", question)

        try:
            response = agent.query(
                snapshot=snapshot,
                question=question,
                history=context.to_messages()[:-1],
            )
        except Exception as exc:
            print(f"Agent error: {exc}", file=sys.stderr)
            continue

        print(f"\nAgent: {response.answer}")
        context.add("assistant", response.answer)

        _print_follow_ups(response.follow_up_suggestions)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactive diagnostic agent CLI"
    )
    parser.add_argument(
        "--connector",
        default="network_weather",
        help="Connector to use (default: network_weather)",
    )
    parser.add_argument(
        "--device",
        required=True,
        help="Device ID to fetch diagnostics for",
    )
    args = parser.parse_args()
    run_cli(args.connector, args.device)


if __name__ == "__main__":
    main()
