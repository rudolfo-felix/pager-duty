# PagerDuty SRE Agent — design exercise

A minimal runnable Python sketch for Session 1 ("In the box"). The repository contains a deterministic incident investigation loop, mocked MCP-shaped read tools, SQLite-backed incident state, an approval-bound policy broker, and tests.

## Run

```bash
python3 demo.py --reset
python3 demo.py --approve --reset
python3 -m unittest discover -s tests -v
```

No external packages, API keys, or real PagerDuty services are required. The approval path is a local simulation; it does not perform a real deployment rollback.

## Design focus

1. **Safety and tenant boundary:** tenant identity is supplied by trusted server context. The mocked tool boundary rejects model-supplied tenant overrides. Writes are allowlisted and require a signed approval bound to tenant, incident, action arguments, revision, and expiry.
2. **State and idempotency:** SQLite WAL persists incident snapshots and append-only events. The mocked action and idempotency record are committed together.

## Explicit assumptions

The assignment references a region-outage burst but the supplied PDF does not define a numeric traffic profile. Any volume or multiplier discussed in the interview should be stated as a hypothetical assumption, not as a PagerDuty requirement.

## Left out and why

Real MCP transport, real LLM integration, authentication/approval UI, multi-responder coordination, distributed worker leases, multi-region state, model evaluations, production observability, and post-action SLI verification are intentionally omitted to keep this exercise executable without credentials or external services.

## Where it breaks first

At burst scale, provider RPM/TPM quotas and downstream tool connection pools are likely bottlenecks before the local orchestration loop. Retry storms amplify load. Production needs alert correlation, tenant-fair queues, bounded retries with jitter, backpressure, per-dependency bulkheads, circuit breakers, and severity-aware load shedding.

SQLite is local demonstration persistence, not a multi-host database. Local idempotency does not solve ambiguous external effects if a remote system applies an action but its response is lost; production needs downstream idempotency and reconciliation. Concurrent humans/workers require one authoritative incident timeline, revision-based approval invalidation, and destination-enforced fencing where available.

