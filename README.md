# PagerDuty SRE Agent — Session 1

A runnable **incident-response agent prototype** built with LangGraph, Pydantic and SQLite. It investigates a synthetic SEV-1 checkout incident, gathers evidence, produces a structured assessment and recommends a mitigation. With a matching trusted approval grant, it can also run a simulated rollback and verify synthetic post-action metrics.

> **Offline by design:** incident data, tool integrations and model assessment are mocked. No PagerDuty account, API key or external service is required.

## Contents

1. [Context and objective](#1-context-and-objective)
2. [Synthetic incident scenario](#2-synthetic-incident-scenario)
3. [How the agent works](#3-how-the-agent-works)
4. [Interview brief coverage](#4-interview-brief-coverage)
5. [Run the prototype](#5-run-the-prototype)
6. [Implementation and repository map](#6-implementation-and-repository-map)
7. [Scope and production readiness](#7-scope-and-production-readiness)

## 1. Context and objective

PagerDuty's SRE Agent acts as a virtual responder: when an incident triggers, it gathers context from observability, on-call and service ownership data, and incident history. It suggests likely root causes and next actions. **Where an action has been explicitly authorised, it can also run the approved automation and verify whether the fix worked.**

The important distinction is that authorisation is not the same as a recommendation. The agent must never grant itself permission. For this exercise, any state-changing action requires explicit human approval, consistent with the brief's assumption of predefined actions that need approval and no autonomous actions. A broader production policy may support tightly scoped pre-authorized automations, but that is outside this prototype. Every executed action must be verified using fresh operational signals.

This take-home implements the investigation path and a mock-only authorized action path. No real deployment, PagerDuty, MCP, observability or model-provider API is called.

## 2. Synthetic incident scenario

At 03:00, checkout success drops while payment-provider timeouts, HTTP 5xx errors and latency rise. A recent deployment changed payment-provider timeout and retry behaviour.

| Signal | Baseline | Incident |
|---|---:|---:|
| Checkout success | 98.7% | **61.2%** |
| HTTP 5xx rate | 0.8% | **14.6%** |
| p95 latency | 420 ms | **3,200 ms** |
| Payment-provider timeout rate | 0.4% | **8.9%** |

**Candidate contributor:** `checkout-v2.8.1`, deployed 11 minutes before the incident.

**Proposed mitigation:** `rollback_deployment("checkout-v2.8.1")`.

This is a hypothesis, not a confirmed root cause. For this take-home, the proposed rollback runs only when the caller supplies an explicit approval grant matching the tenant, incident, action and arguments. Model confidence is never authorization.

> All incident IDs, metrics, services, deployments and historical records are fabricated for this exercise. The scenario is inspired by the engineering context of building an AI-enabled e-commerce platform at a stealth AI startup; it does not describe an actual outage.

## 3. How the agent works

The end-to-end lifecycle below separates the **implemented agent path** from the **external human-decision boundary**. The prototype validates a caller-supplied approval grant; it does not implement the responder UI, approval service or notification/resume workflow.

```mermaid
flowchart TD
    A(["Incident triggers"]) --> B["Validate incident + tenant scope"]
    B --> C["Initialise workflow state"]

    subgraph G["1 · Gather context — five mocked read tools"]
      direction TB
      T1["Service profile<br/>Owner, dependencies, runbook"]
      T2["Checkout metrics<br/>Success rate, errors, latency"]
      T3["Dependency health<br/>Payment provider, database, inventory"]
      T4["Recent deployments<br/>Version, time, changes"]
      T5["Incident history<br/>Similar incidents, previous outcomes"]
      T1 --> T2 --> T3 --> T4 --> T5
      T5 --> E["Evidence bundle"]
    end

    C --> T1
    E --> F["2 · Assess likely causes + next steps"]
    F --> V["3 · Validate assessment + evidence"]
    V --> H["4 · Produce recommendation + exact proposed action"]

    subgraph HUMAN["External human decision — not implemented"]
      direction TB
      U["Responder reviews evidence, impact and proposed action"]
      D{"Decision"}
      U --> D
      D -- "Approve exact proposal" --> G1["Caller supplies ActionAuthorization grant"]
      D -- "Reject / defer / no response" --> G0["No approval grant supplied"]
    end

    H --> U
    G1 -. "grant input" .-> Z{"Grant matches tenant, incident,<br/>action, arguments and allowlist?"}
    G0 -. "no grant" .-> M(["AWAITING HUMAN DECISION<br/>No write; incident remains open"])
    Z -- "Absent" --> M
    Z -- "Invalid / mismatched" --> R(["AUTHORIZATION REJECTED<br/>No write executed"])
    Z -- "Exact match" --> J["Mock rollback executor"]
    J --> N["Verify using fresh mock metrics"]
    N --> O{"Recovery thresholds met?"}
    O -- "Yes" --> P(["RECOVERY VERIFIED<br/>Synthetic metrics only"])
    O -- "No" --> Q(["RECOVERY NOT CONFIRMED<br/>Keep incident open; no automatic repeat"])
    Q --> X["Gather updated evidence and request a new human decision"]

    classDef implemented stroke-width:2px
    classDef boundary stroke-dasharray:5 5,stroke-width:1px
    class A,B,C,T1,T2,T3,T4,T5,E,F,V,H,Z,J,N,O,P,Q,M,R,X implemented
    class U,D,G1,G0 boundary
```

**Decision semantics:** approving means the caller passes a grant for the exact proposed tenant, incident, action and arguments; only a matching grant reaches the mock executor. Rejecting, deferring or not responding means no grant is supplied, so the implemented path stops at `AWAITING_HUMAN_DECISION` with no write. A malformed or mismatched grant stops at `AUTHORIZATION_REJECTED`. The current prototype does not record an explicit human-denial event or resume a paused graph; those belong to the external approval workflow. The `ActionAuthorization` object is caller-supplied and its fields are checked, but this prototype does not authenticate the approver or prove the grant's origin.

**Diagram legend:** solid nodes are implemented with deterministic local mocks; dashed nodes are external and not implemented. The rollback executor and post-action metrics are simulations, not real deployment or observability integrations.

**What the tests cover:**

| Test | Verified behaviour |
|---|---|
| Core investigation | Runs all five mocked reads, produces an assessment and waits when no approval is supplied |
| Evidence and tenancy | Checks evidence provenance and rejects tenant mismatches before tool access |
| Persistence | Saves incident state and an event record |
| Authorized execution | A matching tenant, incident, action and argument grant allows the mock rollback to run |
| Authorization rejection | A grant for another tenant or deployment version is rejected; no action executes |
| Recovery verification | Fresh synthetic metrics determine whether recovery is verified |
| Failed recovery | Keeps the incident open and requires a new human decision; it does not repeat the write |

**Still outside this prototype:** real approval UI or policy service, real deployment execution, real post-action observability queries, read-tool failure/degraded-assessment tests, distributed idempotency and production load tests.

**What each read tool provides:**
- **Service profile:** who owns the service, what it depends on and where its runbook is.
- **Checkout metrics:** whether checkout success, error rate and latency have changed.
- **Dependency health:** whether connected systems, such as the payment provider, database or inventory service, are degraded.
- **Recent deployments:** what changed recently and whether the timing correlates with the incident.
- **Incident history:** whether similar incidents happened before and what happened after previous mitigations.

The tools run sequentially in the order shown. Each successful read contributes evidence to the bundle; a failed read is recorded as a tool error, and the remaining reads continue. The prototype uses deterministic mock data for all five.

**How to read the boundary:**
- **Implemented:** incident investigation, approval-grant validation, simulated rollback, post-action metric verification and failed-recovery handoff.
- **Not implemented:** real approval UI/policy service, real deployment action or real observability integration.
- **Safety behaviour:** an absent or mismatched grant cannot execute; unsuccessful recovery does not trigger an automatic repeat.

The agent must not infer authorization from its own assessment. Policy enforcement belongs in deterministic controls and the destination system, not in model-generated text.

## 4. Interview brief coverage

The brief separates the discussion into **Session 1: designing the agent** and **Session 2: operating it in production**. This table makes the coverage explicit, including what the take-home does not attempt to implement.

### Session 1 — “In the box”

| Area from the brief | What this submission demonstrates | Coverage |
|---|---|---|
| **Core loop** | Incident intake → tenant validation → five context reads → evidence bundle → assessment → recommendation. | <strong>Implemented</strong> |
| **State** | Typed workflow state, SQLite snapshot and append-only event history for a local run. | <strong>Partial</strong> — no distributed recovery or long-running incident lifecycle |
| **Memory** | Synthetic incident history is queried as one of the context tools. | <strong>Limited</strong> — no curated cross-incident memory or learning |
| **Multiplayer** | Tenant context is validated before tool access. | <strong>Not implemented</strong> — no concurrent responders, agent coordination or shared-surface synchronization |
| **Actions and safety** | Typed proposed mitigation, trusted grant validation, mock rollback executor and mock recovery verification. | <strong>Partial</strong> — no production approval service or real write integration |
| **Scale** | Identifies likely burst bottlenecks and production controls in the design discussion below. | <strong>Not implemented / not load-tested</strong> |
| **Extensibility** | Five local read tools illustrate a narrow integration boundary. | <strong>Limited</strong> — no real MCP transport, integration registry or multilingual support |

### Session 2 — “Around the box”

| Area from the brief | What this submission covers | Coverage |
|---|---|---|
| **Knowing it works** | Unit tests cover workflow and safety invariants; production evaluation proposes incident-trace datasets and responder review. | <strong>Partial</strong> — no model-quality evaluation; assessment is deterministic and mocked |
| **Shipping changes** | CI runs validation; production design describes versioning, shadow mode, canaries and rollback. | <strong>Partial</strong> — no deployed release pipeline or canary system |
| **Monitoring** | Production signals and degraded-mode behaviour are specified. | <strong>Design only</strong> — no production telemetry or alerting |
| **Cost and latency** | Budgets, quotas, retries and likely bottlenecks are identified. | <strong>Design only</strong> — no live model costs, latency measurements or load benchmarks |
| **Trust and governance** | Tenant validation, evidence provenance and local event history; production controls include authorization, audit and approval expiry. | <strong>Partial</strong> — no production identity, policy engine or approval service |
| **The recursive case** | Independent kill switch, read-only containment, trace preservation and regression follow-up are proposed. | <strong>Design only</strong> — no production executor exists to trigger this scenario |

**Reading the coverage accurately:** “Implemented” means present in this runnable prototype. “Partial” means a bounded slice is implemented and the remaining production capability is stated. “Design only” means an approach is described, not built or validated. This is a focused take-home, not a claim that all interview areas are production-ready.

## 5. Run the prototype

Requirements: Python 3.11+

```bash
git clone https://github.com/rudolfo-felix/pager-duty.git
cd pager-duty
git checkout take-home-complete

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python demo.py --reset
```

To exercise the mock write path, run either of these alternatives:

```bash
# Simulate a matching human approval, mock rollback and successful recovery
python demo.py --approve-rollback --reset

# Simulate an approved mock rollback where fresh metrics do not show recovery
python demo.py --approve-rollback --simulate-no-recovery --reset
```

The demo prints the incident, gathered evidence, structured assessment and final safety status:

```text
AWAITING_HUMAN_DECISION
No write action executed.
```

Run the tests:

```bash
python -m unittest discover -s tests -v
```

The demo creates a local SQLite database at `runtime/agent.db`. Use `--reset` to start with a clean demo database.

## 6. Implementation and repository map

| Concern | Prototype implementation |
|---|---|
| Orchestration | LangGraph state graph |
| Contracts | Pydantic models for incident, evidence, assessment and proposed action |
| Context | Five deterministic, tenant-scoped, read-only mock tools |
| Assessment | Deterministic local `_mock_assessment`; no LLM API call |
| Persistence | SQLite snapshot + append-only incident event history |
| Actions & safety | Requires a matching trusted grant; mock rollback + mock post-action verification only |

### Mocked read tools

`get_service_profile` · `get_checkout_metrics` · `get_dependency_health` · `get_recent_deployments` · `search_incident_history`

### Repository map

| File | Purpose |
|---|---|
| `agent.py` | Contracts, mock tools, graph, assessment and persistence |
| `demo.py` | Synthetic incident and runnable entry point |
| `tests/test_agent.py` | Tests for the core flow, tenant boundary, evidence and persistence |
| `requirements.txt` | Python dependencies |
| `.github/workflows/validate.yml` | CI: install, tests and demo |

**Suggested reading order:** run the default demo → try the approved and failed-recovery mock paths → inspect `Incident`, `ActionAuthorization` and the graph in `agent.py` → read `tests/test_agent.py` → review the production-readiness checklist below.

## 7. Scope and production readiness

### Deliberately not implemented

- Real PagerDuty, MCP or observability integrations.
- A live LLM call or model-provider adapter.
- Production policy/approval service, real deployment executor or real post-action observability integration.
- Cross-incident memory or multiplayer collaboration.
- Distributed workers, multi-region failover or load testing.

MCP is represented by local MCP-shaped functions; there is no MCP client/server or network transport. The rollback executor and post-action metrics are also local mocks. SQLite and in-process checkpointing make this a local prototype, not a distributed runtime.

### Production considerations

These are production design considerations, not implemented capabilities. They describe what would need to be added before operating the prototype as a production responder.

| Area | What production needs |
|---|---|
| **Evaluation** | Versioned incident traces; test grounding, evidence quality, tool failures, ambiguity and safe abstention. Combine automated regression metrics with responder review. |
| **Release safety** | Version models, prompts, graph, tools and policies together. Gate releases with unit, contract, security and offline evaluations; deploy through shadow mode and tenant canaries with fast rollback. |
| **Monitoring** | Track queue age, latency, throughput, tool/model errors, retries, cost, evidence freshness, approvals and action outcomes. Degrade or escalate when critical evidence is missing or stale. |
| **Resilience** | Bound time, tool/model calls, tokens and retries. Use backpressure, tenant-fair queues, circuit breakers, dependency bulkheads and severity-aware load shedding. |
| **Trust & tenancy** | Enforce tenant scope and authorization at every tool boundary. Audit decisions and tool I/O; expire approvals when incident state changes; enforce idempotency at the destination. |
| **Agent-caused incidents** | Keep an independent kill switch. Revoke pending approvals, force read-only mode, preserve traces, recover with a human and add the failure to regression tests. |
| **Regional outage / burst load** | Use durable queues, priority lanes, tenant quotas, bounded concurrency and retry budgets. Watch observability/MCP pools and model RPM/TPM limits. Burst capacity is not tested here. |

---

**Design principle:** the agent can recommend an action and invoke the mock executor only when the caller supplies a matching trusted approval grant. It must never authorize itself. Production tenant scope, policy, approvals and idempotency must be enforced again by destination systems. A checkpoint does not make external side effects exactly-once.
