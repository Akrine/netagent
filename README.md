# Savvy

An AI control plane for enterprise software. Savvy gives teams a single conversational interface to understand and act on everything happening across their software stack.

Instead of switching between Network Weather, Monday.com, Salesforce, Workday, and dozens of other tools, you ask Savvy what you need to know and what you need to do.

## How it works

Savvy decouples three concerns:

- Connectors: integrate with any software that exposes structured data. Each connector normalizes its data into a DiagnosticSnapshot, a standard schema the agent always receives regardless of the source.
- Agent: a Claude-backed reasoning layer that operates exclusively on DiagnosticSnapshots. It has no knowledge of where data came from. Swap the connector, get the same reasoning capability over different data.
- Interfaces: a CLI for local development and a REST API for embedding Savvy in any frontend, Slack bot, or enterprise integration.

Adding a new connector means implementing two methods. The agent requires zero changes.

## Setup

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

## Environment variables

ANTHROPIC_API_KEY        Required for agent reasoning
NWX_CLIENT_ID            Network Weather Partner API
NWX_CLIENT_SECRET        Network Weather Partner API
MONDAY_API_TOKEN         Monday.com personal API token

## Run the demo

python3 run_demo.py

## Start the REST API

uvicorn interfaces.api:app --reload --port 8000

## Run tests

python3 -m pytest tests/ -v
