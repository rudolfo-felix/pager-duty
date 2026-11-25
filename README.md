# PagerDuty SRE Agent — design exercise

A small, runnable Python sketch for the PagerDuty Senior AI/ML Engineer design exercise.

## Session 1 — In the box

The prototype demonstrates:
- A bounded incident investigation loop.
- Read-only context collection through an MCP-shaped tool boundary.
- Tenant identity supplied by trusted server context, not model arguments.
- Durable incident state and append-only event history in SQLite.
- A policy broker that gates predefined writes behind explicit human approval.
- Idempotency and revision checks around the mocked side effect.

Everything external is mocked. No API key or third-party service is required.

## Run

```bash
python3 demo.py
python3 demo.py --approve
python3 -m unittest discover -s tests -v
```

The approved path is a simulation: it does not call a real deployment API. To reset local demo state, remove `runtime/`.

## Session 2 — Around the box

Use [PRODUCTION_DISCUSSION.md](PRODUCTION_DISCUSSION.md) as interview preparation, not as a second formal take-home. It covers evaluation, safe rollout, monitoring, provider degradation, cost/latency, tenant governance, and the recursive case where the agent worsens an incident.

## Assumptions and limitations

The exercise references a region-outage burst but supplies no numeric traffic profile in the source PDF. Do not present an invented number as a PagerDuty requirement; state an explicit hypothetical load assumption verbally and reason about bottlenecks.

This is an interview sketch, not production software:
- SQLite is a local durability demonstration, not a multi-region shared state layer.
- MCP is represented by an in-process mock boundary, not a remote MCP deployment.
- Human approval is simulated; no real identity provider or approval UI exists.
- Local idempotency cannot guarantee exactly-once effects in an external system after ambiguous network failures. Production requires downstream idempotency and reconciliation.
- Post-action SLI verification, distributed leases/fencing, multi-responder concurrency, production telemetry, real LLM integration, and multi-region failover are out of scope.

## Where it breaks first

Under a burst, upstream LLM RPM/TPM limits and downstream MCP connection pools are likely to saturate before the local orchestration loop. Retries can amplify this into a thundering herd. Production defenses include alert correlation, tenant-fair queues, bounded retries with jitter, per-dependency bulkheads, circuit breakers, and severity-aware load shedding.

For concurrent responders and workers, use a single-writer/event-sourced incident timeline or equivalent optimistic concurrency control. Bind approvals to an incident revision and invalidate them after any material human or system change. A lease alone is insufficient against zombie workers; use fencing tokens enforced by the destination where possible.

