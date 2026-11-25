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
MAX_TOOL_CALLS = 5


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
        "service": "checkout-api", "owner_team": "Commerce Platform",
        "criticality": "tier-1",
        "dependencies": ["payment-provider", "orders-db", "inventory-api"],
        "runbook": "checkout-api / checkout-success-degradation / assess-payment-provider",
        "customer_impact": "Customers may be unable to complete purchases",
    },
    "get_checkout_metrics": {
        "window": "last_5m", "checkout_success_rate": 0.612,
        "baseline_checkout_success_rate": 0.987, "http_5xx_rate": 0.146,
        "baseline_http_5xx_rate": 0.008, "p95_latency_ms": 3200,
        "baseline_p95_latency_ms": 420, "sample_size": 18420,
    },
    "get_dependency_health": {
        "payment_provider": {"timeout_rate": 0.089, "baseline_timeout_rate": 0.004, "p95_latency_ms": 2400, "status": "degraded"},
        "orders_db": {"status": "healthy", "connection_pool_utilization": 0.54},
        "inventory_api": {"status": "healthy", "error_rate": 0.006},
    },
    "get_recent_deployments": {
        "deployments": [{"service": "checkout-api", "version": "checkout-v2.8.1", "minutes_ago": 11,
                         "change": "payment-provider client timeout and retry-policy refactor", "environment": "production"}],
    },
    "search_incident_history": {
        "matches": [{"incident_id": "INC-2025-0187", "service": "checkout-api",
                     "finding": "checkout success degraded after payment-provider retry-policy change",
                     "outcome": "configuration rollback restored checkout success rate", "verified": True}],
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


def get_checkout_metrics(tenant_id: str, incident: Incident) -> Evidence:
    return _mock_tool("get_checkout_metrics", tenant_id, incident)


def get_dependency_health(tenant_id: str, incident: Incident) -> Evidence:
    return _mock_tool("get_dependency_health", tenant_id, incident)


def get_recent_deployments(tenant_id: str, incident: Incident) -> Evidence:
    return _mock_tool("get_recent_deployments", tenant_id, incident)


def search_incident_history(tenant_id: str, incident: Incident) -> Evidence:
    return _mock_tool("search_incident_history", tenant_id, incident)


# ---- Model adapter: deterministic mock by default ------------------------

def _mock_assessment(incident: Incident, evidence: list[Evidence]) -> Assessment:
    evidence_ids = [item.evidence_id for item in evidence if item.status == "ok"]
    return Assessment(
        summary=(
            "Checkout success has fallen materially while 5xx responses and p95 latency "
            "have increased. Payment-provider timeouts are elevated, and a recent "
            "checkout deployment changed timeout and retry behaviour."
        ),
        hypotheses=[
            "The checkout-v2.8.1 timeout/retry-policy change may be causing slow or repeated payment-provider requests.",
            "Degraded payment-provider responses may be the immediate dependency-level cause of checkout failures.",
        ],
        supporting_evidence_ids=evidence_ids,
        contradicting_evidence_ids=[],
        missing_information=[
            "Confirm payment-provider status through its independent status channel.",
            "Compare timeout and retry counts by checkout instance and deployment cohort.",
            "Check whether the provider has rate-limited this tenant or changed its response profile.",
        ],
        recommendation=(
            "Page the Commerce Platform owner and validate checkout-v2.8.1 against the runbook. "
            "A rollback is a candidate mitigation, but first confirm blast radius and compatibility; "
            "execute only after explicit human approval. Verify checkout success and payment latency "
            "with fresh metrics after any approved change."
        ),
        action=ProposedAction(tool="rollback_deployment", arguments={"version": "checkout-v2.8.1"}),
        confidence=0.81,
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
        tools = (get_service_profile, get_checkout_metrics, get_dependency_health,
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
        service="checkout-api", severity="SEV-1",
        summary="Checkout success dropped sharply; payment-provider timeouts increased after checkout-v2.8.1",
    )
    result = run_investigation(incident)
    print(json.dumps(result, indent=2, default=str))
