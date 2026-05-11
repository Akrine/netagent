#!/usr/bin/env python3
"""
scripts/export_training_data.py

Export logged conversations as a filtered Oumi-compatible training dataset.

Usage:
    python scripts/export_training_data.py
    python scripts/export_training_data.py --min-answer-words 20 --output logs/oumi_dataset.jsonl

Filters applied:
    - Answer must be at least N words (default: 15)
    - Answer must not be an error or access denied message
    - Connector must not be a demo/mock source (configurable)

Run this periodically as Savvy accumulates more real conversations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.logger import ConversationLogger


def is_quality_example(entry: dict, min_answer_words: int, exclude_mock: bool) -> bool:
    """Return True if this conversation is worth training on."""
    conversation = entry.get("conversation", {})
    answer = conversation.get("answer", "")
    connector = entry.get("connector", "")

    # Filter out access denied responses
    if "access denied" in answer.lower():
        return False

    # Filter out error responses
    if answer.lower().startswith("error") or "agent error" in answer.lower():
        return False

    # Filter out very short answers
    if len(answer.split()) < min_answer_words:
        return False

    # Optionally filter mock/demo connectors
    if exclude_mock and ("mock" in connector or "demo" in connector):
        return False

    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Oumi training dataset")
    parser.add_argument("--log", default="logs/conversations.ndjson", help="Input log file")
    parser.add_argument("--output", default="logs/oumi_dataset.jsonl", help="Output dataset file")
    parser.add_argument("--min-answer-words", type=int, default=15, help="Minimum answer word count")
    parser.add_argument("--exclude-mock", action="store_true", help="Exclude mock/demo connectors")
    parser.add_argument("--dry-run", action="store_true", help="Show stats without writing")
    args = parser.parse_args()

    logger = ConversationLogger(log_path=args.log)
    entries = logger.read_all()

    total = len(entries)
    filtered = [
        e for e in entries
        if is_quality_example(e, args.min_answer_words, args.exclude_mock)
    ]
    rejected = total - len(filtered)

    print(f"Total conversations logged : {total}")
    print(f"Quality examples           : {len(filtered)}")
    print(f"Rejected (low quality)     : {rejected}")

    if args.dry_run:
        print("Dry run — no file written.")
        return

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for entry in filtered:
            training = entry.get("training", {})
            system_prompt = training.get("system_prompt", "")
            messages = training.get("messages", [])
            if not messages:
                continue
            oumi_example = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    *messages,
                ],
                "metadata": {
                    "id": entry.get("id"),
                    "connector": entry.get("connector"),
                    "severity": entry.get("overall_severity"),
                    "timestamp": entry.get("timestamp"),
                },
            }
            f.write(json.dumps(oumi_example) + "\n")
            count += 1

    print(f"Written to                 : {output_path}")
    print(f"Examples exported          : {count}")

    # Connector breakdown
    from collections import Counter
    connectors = Counter(e.get("connector") for e in filtered)
    print("\nBy connector:")
    for connector, n in connectors.most_common():
        print(f"  {connector}: {n}")


if __name__ == "__main__":
    main()
