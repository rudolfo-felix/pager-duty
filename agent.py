"""A small, deterministic SRE-agent take-home sketch.

All external dependencies are mocked. This is an interview prototype, not production code.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

DB_DEFAULT = Path("runtime/agent.db")
READ_TOOLS = {"get_service_metrics", "get_error_logs", "get_recent_deployments", "search_past_incidents"}
WRITE_TOOLS = {"rollback_deployment"}


@dataclass(frozen=True)
class Incident:
    incident_id: str
    tenant_id: str
    service: str
    severity: str
    summary: str


@dataclass
class IncidentState:
    incident_id: str
    tenant_id: str
    status: str = "RECEIVED"
    revision: int = 0
    evidence: list[dict[str, Any]] | None = None
    recommendation: str | None = None
    action: dict[str, Any] | None = None


class Store:
    """SQLite WAL state, append-only event log and idempotent mock effects."""
    def __init__(self, path: Path = DB_DEFAULT):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS incidents(
              tenant_id TEXT NOT NULL, incident_id TEXT NOT NULL,
              state_json TEXT NOT NULL, revision INTEGER NOT NULL,
              PRIMARY KEY(tenant_id, incident_id));
            CREATE TABLE IF NOT EXISTS events(
              seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
              incident_id TEXT NOT NULL, event_type TEXT NOT NULL,
              payload TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS idempotency(
              tenant_id TEXT NOT NULL, key TEXT NOT NULL, result TEXT NOT NULL,
              PRIMARY KEY(tenant_id, key));
            CREATE TABLE IF NOT EXISTS mock_effects(
              tenant_id TEXT NOT NULL, incident_id TEXT NOT NULL,
              action TEXT NOT NULL, args TEXT NOT NULL, created_at REAL NOT NULL);
            """)
    def connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c
    def save(self, state: IncidentState, expected_revision: int | None = None):
        state.revision += 1
        raw = json.dumps(asdict(state), sort_keys=True)
        with self.connect() as c:
            row = c.execute("SELECT revision FROM incidents WHERE tenant_id=? AND incident_id=?",
                            (state.tenant_id, state.incident_id)).fetchone()
            current = row["revision"] if row else 0
            if expected_revision is not None and current != expected_revision:
                raise RuntimeError("stale revision")
            c.execute("""INSERT INTO incidents VALUES(?,?,?,?)
              ON CONFLICT(tenant_id,incident_id) DO UPDATE SET state_json=excluded.state_json, revision=excluded.revision""",
              (state.tenant_id, state.incident_id, raw, state.revision))
            c.execute("INSERT INTO events(tenant_id,incident_id,event_type,payload,created_at) VALUES(?,?,?,?,?)",
                      (state.tenant_id, state.incident_id, state.status, raw, time.time()))
    def load(self, tenant_id: str, incident_id: str) -> IncidentState | None:
        with self.connect() as c:
            row = c.execute("SELECT state_json FROM incidents WHERE tenant_id=? AND incident_id=?",
                            (tenant_id, incident_id)).fetchone()
        return IncidentState(**json.loads(row["state_json"])) if row else None
    def execute_once(self, tenant_id: str, incident_id: str, key: str, action: str, args: dict):
        with self.connect() as c:
            row = c.execute("SELECT result FROM idempotency WHERE tenant_id=? AND key=?",
                            (tenant_id, key)).fetchone()
            if row:
                return json.loads(row["result"])
            result = {"status": "mock_executed", "action": action, "arguments": args}
            c.execute("INSERT INTO mock_effects VALUES(?,?,?,?,?)",
                      (tenant_id, incident_id, action, json.dumps(args, sort_keys=True), time.time()))
            c.execute("INSERT INTO idempotency VALUES(?,?,?)",
                      (tenant_id, key, json.dumps(result, sort_keys=True)))
            return result


class MCP:
    """MCP-shaped mock boundary; tenant is injected from trusted server context."""
    def __init__(self):
        self.data = {
            "get_service_metrics": {"error_rate": 0.18, "p95_ms": 1900, "window": "5m"},
            "get_error_logs": {"sample": ["HTTP 503 upstream timeout", "HTTP 503 upstream timeout", "cache miss (red herring)"]},
            "get_recent_deployments": {"latest": {"version": "v1.4.2", "minutes_ago": 8}},
            "search_past_incidents": {"matches": [{"incident": "INC-104", "finding": "v1.4.2 introduced upstream timeout"}]},
        }
    def call(self, name: str, *, trusted_tenant_id: str, arguments: dict):
        if name not in READ_TOOLS:
            raise PermissionError("tool is not read-only")
        if "tenant_id" in arguments:
            raise PermissionError("tenant_id must come from trusted context")
        return {"tenant_id": trusted_tenant_id, "tool": name, "data": self.data[name]}


class MockLLM:
    """Deterministic model stub. It proposes; it never executes tools."""
    def reason(self, incident: Incident, evidence: list[dict]):
        return {
            "summary": "Recent deployment is the strongest correlated signal; logs and metrics show elevated upstream failures.",
            "recommendation": "Review the v1.4.2 deployment and consider rollback after human verification.",
            "action": {"tool": "rollback_deployment", "arguments": {"version": "v1.4.2"}},
            "confidence": 0.78,
        }


class Broker:
    def __init__(self, store: Store, secret: bytes = b"local-demo-secret-change-me"):
        self.store, self.secret = store, secret
    def _signature(self, tenant: str, incident: str, revision: int, tool: str, args: dict, expires: int):
        payload = json.dumps([tenant, incident, revision, tool, args, expires], sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(self.secret, payload, hashlib.sha256).hexdigest()
    def request_approval(self, incident: Incident, state: IncidentState, tool: str, args: dict, ttl: int = 300):
        if tool not in WRITE_TOOLS:
            raise PermissionError("write tool not allowlisted")
        if not isinstance(args.get("version"), str) or set(args) != {"version"}:
            raise ValueError("invalid action arguments")
        expires = int(time.time()) + ttl
        sig = self._signature(incident.tenant_id, incident.incident_id, state.revision,
                              tool, args, expires)
        return {"tenant": incident.tenant_id, "incident": incident.incident_id,
                "revision": state.revision, "tool": tool, "arguments": args,
                "expires": expires, "signature": sig}
    def execute(self, incident: Incident, state: IncidentState, approval: dict):
        fields = ("tenant", "incident", "revision", "tool", "arguments", "expires")
        expected = self._signature(*(approval[k] for k in fields))
        if not hmac.compare_digest(expected, approval.get("signature", "")):
            raise PermissionError("invalid or tampered approval")
        if approval["tenant"] != incident.tenant_id or approval["incident"] != incident.incident_id:
            raise PermissionError("tenant/incident mismatch")
        if approval["revision"] != state.revision:
            raise PermissionError("approval is stale")
        if approval["expires"] < int(time.time()):
            raise PermissionError("approval expired")
        if approval["tool"] not in WRITE_TOOLS:
            raise PermissionError("write tool not allowlisted")
        key = hashlib.sha256(approval["signature"].encode()).hexdigest()
        return self.store.execute_once(incident.tenant_id, incident.incident_id, key,
                                       approval["tool"], approval["arguments"])


class Agent:
    def __init__(self, store: Store, mcp: MCP | None = None, llm: MockLLM | None = None):
        self.store, self.mcp, self.llm = store, mcp or MCP(), llm or MockLLM()
        self.broker = Broker(store)
    def investigate(self, incident: Incident):
        if incident.severity not in {"SEV-1", "SEV-2", "SEV-3", "SEV-4"}:
            raise ValueError("unknown severity")
        state = self.store.load(incident.tenant_id, incident.incident_id) or IncidentState(
            incident.incident_id, incident.tenant_id, evidence=[])
        if state.status not in {"RECEIVED", "ESCALATED"}:
            raise RuntimeError(f"incident is not investigable: {state.status}")
        state.status = "INVESTIGATING"
        self.store.save(state, expected_revision=state.revision)
        calls = 0
        for name in ("get_service_metrics", "get_error_logs", "get_recent_deployments", "search_past_incidents"):
            if calls >= 4:
                state.status = "ESCALATED"
                break
            state.evidence.append(self.mcp.call(name, trusted_tenant_id=incident.tenant_id, arguments={}))
            calls += 1
        reasoning = self.llm.reason(incident, state.evidence)
        state.recommendation = reasoning["recommendation"]
        state.action = reasoning["action"]
        state.status = "AWAITING_APPROVAL"
        self.store.save(state, expected_revision=state.revision)
        return state, reasoning
