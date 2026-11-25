from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from agent import (
    ActionAuthorization, AgentState, Evidence, Incident, IncidentStore,
    build_graph, get_checkout_metrics, run_investigation,
)


class LangGraphAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = IncidentStore(Path(self.tmp.name) / "agent.db")
        self.incident = Incident(
            incident_id="INC-test-1", tenant_id="tenant-a",
            service="checkout-api", severity="SEV-1",
            summary="Checkout success degraded after deployment",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def authorization(self, *, tenant_id="tenant-a", incident_id="INC-test-1",
                      action="rollback_deployment", version="checkout-v2.8.1",
                      approved_by="test-responder"):
        return ActionAuthorization(
            tenant_id=tenant_id, incident_id=incident_id, action=action,
            arguments={"version": version}, approved_by=approved_by,
        )

    def test_core_loop_gathers_context_and_stops_without_approval(self):
        result = run_investigation(self.incident, store=self.store)
        self.assertEqual(result["status"], "AWAITING_HUMAN_DECISION")
        self.assertEqual(result["tool_calls"], 5)
        self.assertEqual(len(result["evidence"]), 5)
        self.assertIn("rollback", result["assessment"]["recommendation"].lower())
        self.assertEqual(result["assessment"]["action"]["arguments"]["version"], "checkout-v2.8.1")
        self.assertNotIn("action_result", result)

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

    def test_mock_tool_rejects_other_tenant(self):
        with self.assertRaises(PermissionError):
            get_checkout_metrics("tenant-b", self.incident)

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

    def test_matching_approval_runs_mock_executor_and_verifies_recovery(self):
        result = run_investigation(
            self.incident, store=self.store, authorization=self.authorization()
        )
        self.assertEqual(result["authorization_status"], "approved")
        self.assertEqual(result["status"], "RECOVERY_VERIFIED")
        self.assertEqual(result["action_result"]["status"], "simulated_execution_succeeded")
        self.assertTrue(result["action_result"]["simulated"])
        self.assertEqual(result["action_result"]["approved_by"], "test-responder")
        self.assertTrue(result["verification"]["verified"])
        self.assertEqual(
            result["verification"]["evidence_id"],
            "ev-INC-test-1-post-action-verification",
        )
        self.assertGreaterEqual(result["verification"]["metrics"]["checkout_success_rate"], 0.95)

    def test_authorization_for_another_tenant_is_rejected_without_execution(self):
        result = run_investigation(
            self.incident, store=self.store,
            authorization=self.authorization(tenant_id="tenant-b"),
        )
        self.assertEqual(result["authorization_status"], "rejected")
        self.assertEqual(result["status"], "AUTHORIZATION_REJECTED")
        self.assertNotIn("action_result", result)

    def test_non_matching_action_arguments_are_rejected(self):
        result = run_investigation(
            self.incident, store=self.store,
            authorization=self.authorization(version="checkout-v2.7.9"),
        )
        self.assertEqual(result["authorization_status"], "rejected")
        self.assertNotIn("action_result", result)

    def test_execution_without_recovery_requires_new_human_decision(self):
        result = run_investigation(
            self.incident, store=self.store,
            authorization=self.authorization(), mock_recovery=False,
        )
        self.assertEqual(result["action_result"]["status"], "simulated_execution_succeeded")
        self.assertEqual(result["status"], "RECOVERY_NOT_CONFIRMED")
        self.assertFalse(result["verification"]["verified"])
        self.assertIn("do not repeat the write automatically", result["next_recommendation"])


if __name__ == "__main__":
    unittest.main()
