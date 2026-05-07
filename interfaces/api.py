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
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

load_dotenv()

from agents.diagnostic import DiagnosticAgent
from agents.multi_connector import MultiConnectorAgent
from agents.fleet_agent import FleetAgent
from core.scheduler import fleet_scheduler
from connectors.base import ConnectorAuthError, ConnectorError, ConnectorNotFoundError
from connectors.system_health import SystemHealthConnector
from connectors.mock_snapshot import MockSnapshotConnector
from core.cache import snapshot_cache
from core.monitor import Monitor
from core.registry import registry
from connectors.ollama import OllamaConnector
from core.schema import DiagnosticSnapshot

app = FastAPI(
    title="Savvy",
    description="AI control plane for enterprise software. Natural language interface over any connected data source.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory="interfaces/static"), name="static")

@app.get("/")
def root() -> FileResponse:
    return FileResponse("interfaces/static/index.html")

_agent = DiagnosticAgent()
def _build_multi_connectors():
    connectors = {}
    for name in registry.available_names():
        if name == "network_weather_fleet":
            continue
        connectors[name] = registry.get(name)
    return connectors

_multi_agent = MultiConnectorAgent(connectors=_build_multi_connectors())
_fleet_agent = FleetAgent()


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
    ollama_host: Optional[str] = Field(
        default=None,
        description="If set, use local Ollama at this host instead of Claude API. Example: http://192.168.64.165:11434",
    )
    ollama_model: str = Field(
        default="phi3:mini",
        description="Ollama model to use when ollama_host is set",
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
        connectors=registry.available_names(),
    )


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    if request.connector not in registry:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown connector '{request.connector}'. Available: {registry.available_names()}",
        )

    try:
        spec = registry.get_spec(request.connector)
        snapshot: DiagnosticSnapshot = snapshot_cache.get_or_fetch(
            request.connector,
            registry.get(request.connector).fetch,
            request.device_id or spec.default_device_id,
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
        if request.ollama_host:
            ollama = OllamaConnector(
                host=request.ollama_host,
                model=request.ollama_model,
            )
            answer = ollama.query(snapshot, request.question)
            from agents.base import AgentResponse
            response = AgentResponse(answer=answer, sources=[], follow_up_suggestions=[])
        else:
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


@app.get("/taxonomy")
def get_taxonomy() -> dict:
    """Return the full connector taxonomy — domains, categories, and tagged connectors."""
    from core.taxonomy import TAXONOMY, Tag
    from core.registry import registry

    taxonomy = TAXONOMY.to_dict()

    # Attach connectors to their primary category
    for spec in registry.all_specs():
        try:
            domain = TAXONOMY.get_domain_for_category(spec.category)
            domain_key = domain.value
            cat_key = spec.category.value
            for cat in taxonomy[domain_key]["categories"]:
                if cat["id"] == cat_key:
                    if "connectors" not in cat:
                        cat["connectors"] = []
                    cat["connectors"].append(spec.to_dict())
        except Exception:
            pass

    return {"taxonomy": taxonomy}


@app.get("/connectors")
def get_connectors(tag: str = None) -> dict:
    """
    Return all registered connectors, optionally filtered by tag.

    Tag-based filtering is the discovery mechanism at scale —
    instead of browsing a tree, you filter by what you care about.
    e.g. ?tag=network returns system_health, network_weather, zoom, fleet connectors.
    """
    from core.taxonomy import Tag
    from core.registry import registry

    specs = registry.all_specs()

    if tag:
        try:
            tag_enum = Tag(tag)
            specs = [s for s in specs if s.matches_tag(tag_enum)]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown tag: {tag}")

    return {
        "connectors": [s.to_dict() for s in specs],
        "total": len(specs),
        "filter": {"tag": tag} if tag else None,
    }


@app.get("/config")
def config() -> dict:
    """Return client configuration including Ollama settings."""
    return {
        "ollama_host": os.environ.get("OLLAMA_HOST", "http://192.168.64.165:11434"),
        "ollama_model": os.environ.get("OLLAMA_MODEL", "phi3:mini"),
    }


@app.get("/cache/stats")
def cache_stats() -> dict:
    """Return current cache statistics."""
    return snapshot_cache.stats()


@app.post("/cache/invalidate/{connector}")
def cache_invalidate(connector: str, device_id: str = "local") -> dict:
    """Invalidate a specific cache entry."""
    snapshot_cache.invalidate(connector, device_id)
    return {"invalidated": connector, "device_id": device_id}


@app.post("/cache/clear")
def cache_clear() -> dict:
    """Clear the entire cache."""
    snapshot_cache.invalidate_all()
    return {"cleared": True}


_monitor = Monitor(interval_seconds=300)


@app.get("/fleet/analyze")
def fleet_analyze() -> dict:
    """Run autonomous fleet diagnostic and return prioritized action report."""
    try:
        import pathlib
        from connectors.mock_fleet import MockFleetConnector
        fixture = pathlib.Path(__file__).parent.parent / "fixtures" / "mock_fleet.json"
        connector = MockFleetConnector(str(fixture))
        snapshot = connector.fetch("all")
        report = _fleet_agent.analyze(snapshot)
        return report.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.on_event("startup")
async def startup() -> None:
    _monitor.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    _monitor.stop()


@app.get("/fleet/reports")
def fleet_reports(limit: int = 20) -> dict:
    """Return recent autonomous fleet diagnostic reports."""
    reports = fleet_scheduler.get_reports(limit=limit)
    return {
        "reports": [r.to_dict() for r in reports],
        "stats": fleet_scheduler.stats(),
    }


@app.get("/fleet/reports/latest")
def fleet_latest() -> dict:
    """Return the most recent fleet diagnostic report."""
    report = fleet_scheduler.get_latest()
    if not report:
        raise HTTPException(status_code=404, detail="No reports yet.")
    return report.to_dict()


@app.get("/fleet/reports/changes")
def fleet_changes(limit: int = 10) -> dict:
    """Return only reports where fleet health changed from previous run."""
    changes = fleet_scheduler.get_changes(limit=limit)
    return {
        "changes": [r.to_dict() for r in changes],
        "total_changes": len(changes),
    }


@app.post("/fleet/reports/run-now")
def fleet_run_now() -> dict:
    """Trigger an immediate fleet diagnostic run outside the normal schedule."""
    report = fleet_scheduler.run_now()
    if not report:
        raise HTTPException(status_code=500, detail="Fleet diagnostic run failed.")
    return report.to_dict()


@app.get("/alerts")
def get_alerts(unacknowledged_only: bool = False, limit: int = 50) -> dict:
    alerts = _monitor.get_alerts(
        unacknowledged_only=unacknowledged_only,
        limit=limit,
    )
    return {
        "alerts": [a.to_dict() for a in alerts],
        "total": len(alerts),
        "stats": _monitor.stats(),
    }


@app.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str) -> dict:
    found = _monitor.acknowledge(alert_id)
    if not found:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    return {"acknowledged": alert_id}


@app.post("/alerts/acknowledge-all")
def acknowledge_all_alerts() -> dict:
    count = _monitor.acknowledge_all()
    return {"acknowledged_count": count}


@app.get("/monitor/stats")
def monitor_stats() -> dict:
    return _monitor.stats()
