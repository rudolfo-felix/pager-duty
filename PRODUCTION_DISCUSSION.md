# Session 2 — Production discussion notes

This is interview preparation, not a second formal take-home.

## Evaluation
Create a versioned incident-trace dataset from resolved incidents, expert-reviewed traces, and adversarial scenarios. Evaluate tool correctness, tenant scope, evidence provenance/freshness, diagnosis quality, recommendation safety, calibration, and operational outcomes. Maintain frozen regression, recent-production, and adversarial slices. Human review is the label authority for subjective quality; model-as-judge is only a signal.

## Shipping changes
Treat prompt, model version, parameters, tool schemas, retrieval policy, and safety policy as one immutable release. Gate through deterministic invariants, offline regression evaluation, shadow execution (read-only tools or simulated writes), then tenant/cohort canaries. Use guardrail metrics and fast rollback. Prompts are not security boundaries.

## Monitoring / degraded mode
Track per tenant and release: tool success/latency, model latency/rate limits, queue age, retries, token and cost budgets, evidence freshness, approval latency, invalid write attempts, escalation rate, and post-action SLI changes. A fluent response is not proof of health. Degrade to deterministic evidence gathering and explicitly mark incomplete analysis when tools/providers fail or budgets expire.

## Failure, cost and latency
Use independent budgets for wall time, model calls, tool calls, tokens, and retries. Apply bounded retries with jitter, circuit breakers, per-dependency bulkheads, tenant-fair queues, backpressure, and severity-aware load shedding. A fallback provider is a separately evaluated release, not a semantic equivalent by default.

## Trust and multiplayer
Enforce tenant identity and authorization server-side at every tool boundary. Maintain an append-only audit trail for actor, tenant, incident/revision, policy decision, approval identity, tool arguments/results, agent release, and evidence references with redaction and retention controls. Use one authoritative incident timeline with single-writer or event-sourced updates, optimistic concurrency, and approval invalidation after human changes. Fencing tokens protect against zombie workers where the destination can enforce them.

## Recursive failure — agent worsens the incident
**Detect:** verify target SLIs after every write and correlate regressions with the action ID.
**Contain:** stop writes per action class, tenant, or fleet; revoke pending approvals; force READ_ONLY mode; notify the human owner. Only propose compensating actions with a known safety contract and human oversight.
**Learn:** preserve the full trace, run a postmortem, add the case to regression evals, convert the lesson into deterministic policy/tests (not only a prompt), and lower autonomy for the affected action class until reliability is re-established.

## Region-outage burst
The source exercise does not provide numeric traffic assumptions. State a hypothetical multiplier explicitly during the interview. Likely early bottlenecks are provider RPM/TPM and downstream MCP pools, amplified by retries and correlated alerts.
