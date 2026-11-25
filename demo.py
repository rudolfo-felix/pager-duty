"""Run the mocked SRE-agent investigation locally."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from agent import Incident, IncidentStore, run_investigation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="delete the local SQLite demo database")
    args = parser.parse_args()
    db_path = Path("runtime/agent.db")
    if args.reset and db_path.exists():
        db_path.unlink()
    incident = Incident(
        incident_id="INC-2026-0042",
        tenant_id="acme-prod",
        service="checkout-api",
        severity="SEV-1",
        summary="Checkout success dropped sharply; payment-provider timeouts increased after checkout-v2.8.1",
    )
    result = run_investigation(incident, store=IncidentStore(db_path))
    print("\n=== SRE AGENT INVESTIGATION ===")
    print(json.dumps({
        "incident": result["incident"],
        "status": result["status"],
        "tool_calls": result["tool_calls"],
        "evidence": result["evidence"],
        "assessment": result["assessment"],
        "tool_errors": result["tool_errors"],
    }, indent=2))
    print("\n=== SAFETY BOUNDARY ===")
    print("No write action executed. The checkout deployment rollback is only a recommendation.")
    print("A human responder must decide and use an explicitly authorised action path.")


if __name__ == "__main__":
    main()
