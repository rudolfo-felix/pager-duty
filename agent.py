"""Minimal LangGraph SRE-agent take-home prototype.

External systems are deterministic mocks. The graph gathers evidence, asks a
model (mock by default) for a structured assessment, validates it, and stops at
a human-approval boundary. No write action is executed by the graph.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ConfigDict, ValidationError

DB_PATH = Path(os.getenv("AGENT_DB_PATH", "runtime/agent.db"))
MAX_TOOL_CALLS = 4


# ---- Domain contracts -----------------------------------------------------

class Incident(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: str
    tenant_id: str
    service: str
    severity: Literal["SEV-1", "SEV-2", "SEV-3", "SEV-4"]
    summary: str


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    tenant_id: str
    source: str
    observed_at: str
    freshness_seconds: int = Field(ge=0)
    status: Literal["ok", "partial", "unavailable"]
    data: dict[str, Any]


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["rollback_deployment"]
    arguments: dict[str, str]


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    hypotheses: list[str]
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str] = []
    missing_information: list[str] = []
    recommendation: str
    action: ProposedAction | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class AgentState(TypedDict, total=False):
    incident: dict[str, Any]
    tenant_id: str                 # trusted server context, not model supplied
    evidence: list[dict[str, Any]]
    tool_errors: list[str]
    tool_calls: int
    assessment: dict[str, Any]
    status: str
    error: str


# ---- Durable local store --------------------------------------------------

class IncidentStore:
    """SQLite WAL snapshot + append-only event journal for the demo."""
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS incidents(
                    tenant_id TEXT NOT NULL, incident_id TEXT NOT NULL,
                    status TEXT NOT NULL, state_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(tenant_id, incident_id)
                );
                CREATE TABLE IF NOT EXISTS incident_events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL, incident_id TEXT NOT NULL,
                    event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def persist(self, state: AgentState, event_type: str) -> None:
        incident = state["incident"]
        tenant = state["tenant_id"]
        incident_id = incident["incident_id"]
        payload = json.dumps(state, sort_keys=True, default=str)
        with self.connect() as db:
            db.execute("""INSERT INTO incidents VALUES(?,?,?,?,?)
                ON CONFLICT(tenant_id, incident_id) DO UPDATE SET
                status=excluded.status, state_json=excluded.state_json,
                updated_at=excluded.updated_at""",
                (tenant, incident_id, state.get("status", "UNKNOWN"), payload, time.time()))
            db.execute("""INSERT INTO incident_events
                (tenant_id, incident_id, event_type, payload_json, created_at)
                VALUES(?,?,?,?,?)""",
                (tenant, incident_id, event_type, payload, time.time()))


# ---- Mock MCP-shaped tools ------------------------------------------------
# These functions have typed inputs/outputs and enforce tenant scope.
# In the production design, this boundary is replaced by MCP clients.

MOCK_DATA: dict[str, dict[str, Any]] = {
    "get_service_profile": {
        "service": "payments-api", "owner_team": "Payments Platform",
        "criticality": "tier-1", "dependencies": ["payment-gateway", "redis"],
        "runbook": "payments-api / elevated-5xx / rollback-last-deploy",
    },
    "get_service_metrics": {
        "window": "last_5m", "error_rate": 0.18, "baseline_error_rate": 0.01,
        "p95_latency_ms": 1900, "baseline_p95_latency_ms": 240,
    },
    "get_recent_deployments": {
        "deployments": [{"version": "v1.4.2", "minutes_ago": 8,
                         "change": "payment gateway connection pool refactor"}],
    },
    "search_incident_history": {
        "matches": [{"incident_id": "INC-104", "service": "payments-api",
                     "finding": "similar 503 spike after connection-pool change",
                     "outcome": "rollback restored baseline", "verified": True}],
    },
}


def _mock_tool(name: str, tenant_id: str, incident: Incident) -> Evidence:
    """Simulate a read-only MCP tool call, scoped by trusted tenant context."""
    if tenant_id != incident.tenant_id:
        raise PermissionError("tenant context does not match incident")
    if name not in MOCK_DATA:
        raise ValueError(f"tool is not available: {name}")
    return Evidence(
        evidence_id=f"ev-{incident.incident_id}-{name}",
        tenant_id=tenant_id,
        source=f"mcp.mock.{name}",
        observed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        freshness_seconds=15,
        status="ok",
        data=MOCK_DATA[name],
    )


def get_service_profile(tenant_id: str, incident: Incident) -> Evidence:
    return _mock_tool("get_service_profile", tenant_id, incident)


def get_service_metrics(tenant_id: str, incident: Incident) -> Evidence:
    return _mock_tool("get_service_metrics", tenant_id, incident)


def get_recent_deployments(tenant_id: str, incident: Incident) -> Evidence:
    return _mock_tool("get_recent_deployments", tenant_id, incident)


def search_incident_history(tenant_id: str, incident: Incident) -> Evidence:
    return _mock_tool("search_incident_history", tenant_id, incident)


# ---- Model adapter: deterministic mock by default ------------------------

def _mock_assessment(incident: Incident, evidence: list[Evidence]) -> Assessment:
    evidence_ids = [item.evidence_id for item in evidence if item.status == "ok"]
    return Assessment(
        summary=(
            "Payments API has a sharp increase in 5xx errors and p95 latency. "
            "A recent connection-pool deployment is temporally correlated, and "
            "a verified historical incident reports a similar failure pattern."
        ),
        hypotheses=[
            "The v1.4.2 connection-pool change may be exhausting or misconfiguring upstream connections.",
            "The payment-gateway dependency may be contributing to elevated 503 responses.",
        ],
        supporting_evidence_ids=evidence_ids,
        contradicting_evidence_ids=[],
        missing_information=[
            "Confirm payment-gateway health independently.",
            "Check whether the deployment affected all instances or only one availability zone.",
        ],
        recommendation=(
            "Ask the service owner to validate the v1.4.2 change against the runbook. "
            "A rollback is a candidate mitigation, but should only be executed after explicit approval."
        ),
        action=ProposedAction(tool="rollback_deployment", arguments={"version": "v1.4.2"}),
        confidence=0.78,
    )


def _live_assessment(incident: Incident, evidence: list[Evidence]) -> Assessment:
    """Optional Gemini adapter; deliberately requires explicit opt-in."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    model = ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        temperature=0,
    ).with_structured_output(Assessment)
    prompt = (
        "You are assisting a human SRE responder. Use only supplied evidence. "
        "Do not invent facts. A proposed action is not authorization to execute it. "
        "Prefer abstention when evidence is insufficient. Return a structured assessment.\n"
        f"Incident: {incident.model_dump_json()}\n"
        f"Evidence: {json.dumps([x.model_dump() for x in evidence])}"
    )
    return model.invoke(prompt)


def assess_incident(incident: Incident, evidence: list[Evidence]) -> Assessment:
    if os.getenv("USE_LIVE_MODEL", "0") == "1":
        return _live_assessment(incident, evidence)
    return _mock_assessment(incident, evidence)


# ---- LangGraph nodes ------------------------------------------------------

def build_graph(store: IncidentStore | None = None, checkpointer=None):
    store = store or IncidentStore()

    def validate_incident(state: AgentState) -> AgentState:
        incident = Incident.model_validate(state["incident"])
        # Trusted tenant is injected by the calling service, not by the LLM.
        tenant_id = state["tenant_id"]
        if tenant_id != incident.tenant_id:
            raise PermissionError("tenant boundary violation")
        return {"incident": incident.model_dump(), "tenant_id": tenant_id,
                "evidence": [], "tool_errors": [], "tool_calls": 0,
                "status": "VALIDATED"}

    def gather_context(state: AgentState) -> AgentState:
        incident = Incident.model_validate(state["incident"])
        tenant = state["tenant_id"]
        tools = (get_service_profile, get_service_metrics,
                 get_recent_deployments, search_incident_history)
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        for tool in tools:
            if len(results) + len(errors) >= MAX_TOOL_CALLS:
                break
            try:
                # Independent reads are sequential in this small demo.
                results.append(tool(tenant, incident).model_dump())
            except Exception as exc:  # a failed source is explicit, not fabricated
                errors.append(f"{tool.__name__}: {type(exc).__name__}: {exc}")
        return {"evidence": results, "tool_errors": errors,
                "tool_calls": len(results) + len(errors), "status": "CONTEXT_GATHERED"}

    def reason(state: AgentState) -> AgentState:
        incident = Incident.model_validate(state["incident"])
        evidence = [Evidence.model_validate(x) for x in state["evidence"]]
        assessment = assess_incident(incident, evidence)
        valid_ids = {x.evidence_id for x in evidence}
        if not set(assessment.supporting_evidence_ids + assessment.contradicting_evidence_ids) <= valid_ids:
            raise ValueError("model referenced evidence outside the retrieved context")
        return {"assessment": assessment.model_dump(), "status": "ASSESSED"}

    def prepare_handoff(state: AgentState) -> AgentState:
        # Session 1 assumption: no autonomous writes. The graph ends here.
        return {"status": "AWAITING_HUMAN_DECISION"}

    def persist(state: AgentState) -> AgentState:
        store.persist(state, state.get("status", "UNKNOWN"))
        return {}

    graph = StateGraph(AgentState)
    graph.add_node("validate_incident", validate_incident)
    graph.add_node("gather_context", gather_context)
    graph.add_node("reason", reason)
    graph.add_node("prepare_handoff", prepare_handoff)
    graph.add_node("persist", persist)
    graph.add_edge(START, "validate_incident")
    graph.add_edge("validate_incident", "gather_context")
    graph.add_edge("gather_context", "reason")
    graph.add_edge("reason", "prepare_handoff")
    graph.add_edge("prepare_handoff", "persist")
    graph.add_edge("persist", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def run_investigation(incident: Incident, *, store: IncidentStore | None = None,
                      thread_id: str | None = None) -> AgentState:
    store = store or IncidentStore()
    graph = build_graph(store=store)
    config = {"configurable": {"thread_id": thread_id or f"{incident.tenant_id}:{incident.incident_id}"}}
    initial: AgentState = {
        "incident": incident.model_dump(),
        "tenant_id": incident.tenant_id,  # injected from authenticated request context in production
        "status": "RECEIVED",
    }
    return graph.invoke(initial, config=config)


if __name__ == "__main__":
    incident = Incident(
        incident_id="INC-2026-0042", tenant_id="acme-prod",
        service="payments-api", severity="SEV-1",
        summary="Elevated 503s and p95 latency following deployment v1.4.2",
    )
    result = run_investigation(incident)
    print(json.dumps(result, indent=2, default=str))
