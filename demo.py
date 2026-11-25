"""Run the mocked SRE-agent investigation locally."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from agent import ActionAuthorization, Incident, IncidentStore, run_investigation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete the local SQLite demo database")
    parser.add_argument(
        "--approve-rollback", action="store_true",
        help="supply a simulated trusted approval grant and run the mock rollback",
    )
    parser.add_argument(
        "--simulate-no-recovery", action="store_true",
        help="simulate a mock rollback that does not restore checkout metrics",
    )
    args = parser.parse_args()
    if args.simulate_no_recovery and not args.approve_rollback:
        parser.error("--simulate-no-recovery requires --approve-rollback")

    db_path = Path("runtime/agent.db")
    if args.reset and db_path.exists():
        db_path.unlink()

    incident = Incident(
        incident_id="INC-2026-0042", tenant_id="acme-prod",
        service="checkout-api", severity="SEV-1",
        summary="Checkout success dropped sharply; payment-provider timeouts increased after checkout-v2.8.1",
    )
    authorization = None
    if args.approve_rollback:
        authorization = ActionAuthorization(
            tenant_id=incident.tenant_id,
            incident_id=incident.incident_id,
            action="rollback_deployment",
            arguments={"version": "checkout-v2.8.1"},
            approved_by="demo-human-responder",
        )

    result = run_investigation(
        incident, store=IncidentStore(db_path), authorization=authorization,
        mock_recovery=not args.simulate_no_recovery,
    )
    print("\n=== SRE AGENT INVESTIGATION ===")
    print(json.dumps({
        "incident": result["incident"],
        "status": result["status"],
        "tool_calls": result["tool_calls"],
        "evidence": result["evidence"],
        "assessment": result["assessment"],
        "authorization_status": result.get("authorization_status", "not supplied"),
        "action_result": result.get("action_result"),
        "verification": result.get("verification"),
        "next_recommendation": result.get("next_recommendation"),
        "tool_errors": result["tool_errors"],
    }, indent=2))
    print("\n=== SAFETY BOUNDARY ===")
    print("All tools and action effects are simulated locally; no real deployment is changed.")
    if result.get("action_result"):
        print("A mock rollback ran only because a matching explicit approval grant was supplied.")
    else:
        print("No write action was executed; the incident remains for human decision.")


if __name__ == "__main__":
    main()
