from pathlib import Path
import argparse, json, shutil
from agent import Agent, Incident, Store

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve", action="store_true", help="simulate explicit human approval")
    parser.add_argument("--reset", action="store_true", help="remove local runtime database first")
    args = parser.parse_args()
    db = Path("runtime") / ("approved.db" if args.approve else "agent.db")
    if args.reset and db.exists():
        db.unlink()
    incident = Incident("INC-001", "tenant-demo", "payments", "SEV-1", "Elevated 503 errors after deploy")
    agent = Agent(Store(db))
    state, reasoning = agent.investigate(incident)
    print("INVESTIGATION")
    print(json.dumps({"state": state.__dict__, "reasoning": reasoning}, indent=2))
    if args.approve:
        approval = agent.broker.request_approval(incident, state, reasoning["action"]["tool"],
                                                 reasoning["action"]["arguments"])
        result = agent.broker.execute(incident, state, approval)
        print("\nHUMAN APPROVAL SIMULATED — MOCK ACTION RESULT")
        print(json.dumps(result, indent=2))
    else:
        print("\nNo write action executed; human approval is required.")

if __name__ == "__main__":
    main()
