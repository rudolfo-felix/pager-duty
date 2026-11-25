from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from agent import (
    AgentState, Incident, IncidentStore, build_graph, run_investigation,
    get_service_metrics, Evidence,
)


class LangGraphAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = IncidentStore(Path(self.tmp.name) / "agent.db")
        self.incident = Incident(
            incident_id="INC-test-1", tenant_id="tenant-a",
            service="payments-api", severity="SEV-1",
            summary="Elevated 503 errors after deployment",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_core_loop_gathers_mock_context_and_recommends_next_step(self):
        result = run_investigation(self.incident, store=self.store)
        self.assertEqual(result["status"], "AWAITING_HUMAN_DECISION")
        self.assertEqual(result["tool_calls"], 4)
        self.assertEqual(len(result["evidence"]), 4)
        self.assertIn("rollback", result["assessment"]["recommendation"].lower())

    def test_evidence_has_provenance_and_tenant_scope(self):
        result = run_investigation(self.incident, store=self.store)
        for item in result["evidence"]:
            evidence = Evidence.model_validate(item)
            self.assertEqual(evidence.tenant_id, "tenant-a")
            self.assertTrue(evidence.source)
            self.assertTrue(evidence.observed_at)

    def test_tenant_mismatch_is_rejected_before_tool_calls(self):
        graph = build_graph(store=self.store)
        state: AgentState = {
            "incident": self.incident.model_dump(),
            "tenant_id": "tenant-b",
            "status": "RECEIVED",
        }
        with self.assertRaises(PermissionError):
            graph.invoke(state, {"configurable": {"thread_id": "bad-tenant"}})

    def test_mock_tool_rejects_other_tenant_and_model_cannot_supply_scope(self):
        with self.assertRaises(PermissionError):
            get_service_metrics("tenant-b", self.incident)

    def test_persisted_incident_event_created(self):
        run_investigation(self.incident, store=self.store)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT status FROM incidents WHERE tenant_id=? AND incident_id=?",
                ("tenant-a", "INC-test-1"),
            ).fetchone()
            self.assertEqual(row["status"], "AWAITING_HUMAN_DECISION")
            events = db.execute("SELECT count(*) FROM incident_events").fetchone()[0]
            self.assertGreaterEqual(events, 1)


if __name__ == "__main__":
    unittest.main()
