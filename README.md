# netagent

A generic framework for building natural language agents over diagnostic and monitoring software.

The framework decouples three concerns:
- Data connectors: integrate with any software that exposes structured diagnostic data
- Agent reasoning: interpret domain-specific data and generate actionable responses
- Conversation interfaces: expose the agent through any interaction surface

Network Weather is the reference implementation. The architecture is designed so that
onboarding a new software integration is a repeatable, well-defined process.

## Architecture

connectors/   - One module per software integration (data ingestion + normalization)
agents/       - Reasoning layer (LLM orchestration, domain interpretation)
interfaces/   - Conversation surfaces (CLI, API, future: chat embed)
tests/        - Integration and unit tests per connector and agent

## Setup

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
