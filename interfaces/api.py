"""
interfaces/api.py

FastAPI REST interface for the Savvy diagnostic agent.

Exposes two endpoints:
  POST /query    - Ask a question against a connector's live data
  GET  /health   - Service health check

The API is stateless. Conversation history is managed client-side
and passed in on each request.
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

from agents.diagnostic import DiagnosticAgent
from agents.multi_connector import MultiConnectorAgent
from connectors.base import ConnectorAuthError, ConnectorError, ConnectorNotFoundError
from connectors.mock_snapshot import MockSnapshotConnector
from connectors.monday_com import MondayConnector
from connectors.system_health import SystemHealthConnector
from core.schema import DiagnosticSnapshot

app = FastAPI(
    title="Savvy",
    description="AI control plane for enterprise software. Natural language interface over any connected data source.",
    version="0.1.0",
)

_agent = DiagnosticAgent()
_multi_agent = MultiConnectorAgent(connectors={
    "system_health": SystemHealthConnector(),
    "mock_network_weather": MockSnapshotConnector("fixtures/my_network.json"),
})

_CONNECTORS = {
    "system_health": lambda device_id: SystemHealthConnector().fetch(device_id),
    "mock_network_weather": lambda device_id: MockSnapshotConnector(
        "fixtures/my_network.json"
    ).fetch(device_id),
    "monday_com": lambda device_id: MondayConnector().fetch(device_id),
}


class ConversationTurn(BaseModel):
    role: str = Field(..., description="Either 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class QueryRequest(BaseModel):
    connector: str = Field(
        ...,
        description="Connector to use. Available: system_health, mock_network_weather",
    )
    device_id: str = Field(
        default="local",
        description="Device identifier for the connector",
    )
    question: str = Field(
        ...,
        description="Natural language question to ask about the diagnostic data",
    )
    history: list[ConversationTurn] = Field(
        default_factory=list,
        description="Prior conversation turns for multi-turn context",
    )


class QueryResponse(BaseModel):
    answer: str
    connector: str
    device_id: str
    overall_severity: str
    findings_count: int
    sources: list[str]
    follow_up_suggestions: list[str]


class HealthResponse(BaseModel):
    status: str
    version: str
    connectors: list[str]


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.1.0",
        connectors=list(_CONNECTORS.keys()),
    )


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    if request.connector not in _CONNECTORS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown connector '{request.connector}'. Available: {list(_CONNECTORS.keys())}",
        )

    try:
        snapshot: DiagnosticSnapshot = _CONNECTORS[request.connector](
            request.device_id
        )
    except ConnectorAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except ConnectorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    history = [
        {"role": turn.role, "content": turn.content}
        for turn in request.history
    ]

    try:
        response = _agent.query(
            snapshot=snapshot,
            question=request.question,
            history=history or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}")

    return QueryResponse(
        answer=response.answer,
        connector=request.connector,
        device_id=request.device_id,
        overall_severity=snapshot.overall_severity.value,
        findings_count=len(snapshot.findings),
        sources=response.sources,
        follow_up_suggestions=response.follow_up_suggestions,
    )


class MultiQueryRequest(BaseModel):
    question: str = Field(
        ...,
        description="Natural language question to answer across all connected systems",
    )
    history: list[ConversationTurn] = Field(
        default_factory=list,
        description="Prior conversation turns for multi-turn context",
    )


class MultiQueryResponse(BaseModel):
    answer: str
    overall_severity: str
    systems_queried: list[str]
    errors: dict[str, str]


@app.post("/query/all", response_model=MultiQueryResponse)
def query_all(request: MultiQueryRequest) -> MultiQueryResponse:
    """Query all connected systems simultaneously and return a unified response."""
    history = [
        {"role": turn.role, "content": turn.content}
        for turn in request.history
    ]

    try:
        result = _multi_agent.query(
            question=request.question,
            history=history or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}")

    return MultiQueryResponse(
        answer=result["answer"],
        overall_severity=result["overall_severity"],
        systems_queried=list(result["snapshots"].keys()),
        errors=result["errors"],
    )
