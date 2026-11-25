import tempfile
import unittest
from pathlib import Path
from agent import Agent, Broker, Incident, IncidentState, Store

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "agent.db")
        self.agent = Agent(self.store)
        self.incident = Incident("INC-001", "tenant-a", "payments", "SEV-1", "Elevated 503s")
    def tearDown(self):
        self.tmp.cleanup()
    def test_investigation_collects_four_tool_results_and_waits_for_approval(self):
        state, reasoning = self.agent.investigate(self.incident)
        self.assertEqual(state.status, "AWAITING_APPROVAL")
        self.assertEqual(len(state.evidence), 4)
        self.assertEqual(reasoning["action"]["tool"], "rollback_deployment")
    def test_model_cannot_choose_tenant(self):
        with self.assertRaises(PermissionError):
            self.agent.mcp.call("get_error_logs", trusted_tenant_id="tenant-a",
                                arguments={"tenant_id": "tenant-b"})
    def test_write_not_allowlisted_is_rejected(self):
        state, _ = self.agent.investigate(self.incident)
        with self.assertRaises(PermissionError):
            self.agent.broker.request_approval(self.incident, state, "delete_database", {})
    def test_tampered_approval_is_rejected(self):
        state, reasoning = self.agent.investigate(self.incident)
        approval = self.agent.broker.request_approval(self.incident, state, "rollback_deployment",
                                                      reasoning["action"]["arguments"])
        approval["arguments"] = {"version": "v1.4.1"}
        with self.assertRaises(PermissionError):
            self.agent.broker.execute(self.incident, state, approval)
    def test_approval_bound_to_revision(self):
        state, reasoning = self.agent.investigate(self.incident)
        approval = self.agent.broker.request_approval(self.incident, state, "rollback_deployment",
                                                      reasoning["action"]["arguments"])
        state.status = "HUMAN_UPDATED"
        self.store.save(state, expected_revision=state.revision)
        with self.assertRaises(PermissionError):
            self.agent.broker.execute(self.incident, state, approval)
    def test_approved_action_is_idempotent(self):
        state, reasoning = self.agent.investigate(self.incident)
        approval = self.agent.broker.request_approval(self.incident, state, "rollback_deployment",
                                                      reasoning["action"]["arguments"])
        first = self.agent.broker.execute(self.incident, state, approval)
        second = self.agent.broker.execute(self.incident, state, approval)
        self.assertEqual(first, second)
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM mock_effects").fetchone()[0], 1)

if __name__ == "__main__":
    unittest.main()
