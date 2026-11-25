# Session 2 — Production discussion notes

This document is a discussion aid; the take-home implementation remains intentionally small.

## Evaluation
Build versioned incident-trace datasets from resolved incidents and responder-reviewed recommendations. Reconstruct the evidence available at decision time to avoid future-outcome leakage. Include ambiguity, incomplete or contradictory evidence, tool failures, and correct abstention/escalation. Measure evidence grounding, citation validity, hypothesis usefulness, recommendation utility, policy compliance and safe abstention. Use DeepEval-style regression metrics plus human review; model-as-judge is a signal, not ground truth.

## Shipping changes
Version model, prompt, graph code, tool schemas, retrieval rules, safety policy and evaluation dataset together. Gate with unit, contract, security and offline regression tests; then shadow, canary by tenant/cohort and progressively roll out. Keep a fast rollback path. Prompts are not security boundaries.

## Monitoring and degraded mode
Track per tenant and release: queue age, throughput, worker saturation, per-stage latency, tool/model errors, retries, cost/token budgets, evidence freshness, schema/citation failures, approvals, escalations and action outcomes. A fluent answer is not evidence of healthy operation. Mark outputs degraded when critical evidence is stale/missing or a dependency fails; return partial evidence and hand over rather than fabricate.

## Failure, cost and latency
Use independent budgets for wall time, tool calls, model calls, tokens and retries. Apply bounded retries with jitter, circuit breakers, per-dependency bulkheads, tenant-fair queues, backpressure and severity-aware load shedding. Treat a fallback provider as a separately evaluated model route, not a semantic equivalent by default.

## Trust, tenant isolation and multiplayer
Enforce tenant identity and authorization server-side at every tool boundary. Persist actor, tenant, incident/revision, policy decision, approval identity, tool arguments/results, agent release and evidence references in an auditable timeline, with redaction and retention controls. Use one authoritative incident timeline with optimistic concurrency or serialized writes. Invalidate approvals after incident state changes. Use destination-enforced idempotency and fencing where available.

## Recursive failure — agent worsens the incident
Detect via action audit records, telemetry and customer signals. Stop the implicated action capability through a control plane independent of the model; revoke pending approvals and force read-only mode. Preserve the full trace, state versions, approval and tool I/O. Support human-led recovery. Determine whether evidence, reasoning, authorization, policy, tool semantics or orchestration failed. Add the case to regression evaluations and convert the lesson into deterministic policy/tests, not only a prompt change. Re-enable only after controlled validation.

## Region-outage burst
The exercise does not provide a numeric traffic profile. State any multiplier as a hypothetical. Likely early bottlenecks are downstream observability/MCP pools and model RPM/TPM quotas; correlated retries amplify load. Protect ingestion with durable queues, priority lanes, tenant quotas, bounded concurrency, backpressure, deadlines, circuit breakers, retry budgets and partial/degraded responses.
