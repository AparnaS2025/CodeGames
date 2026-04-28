# Security And Operations

## Security Requirements
- source credentials must be read-only
- secrets must not be hard-coded
- secrets loaded from env vars or secret manager
- no raw log bodies persisted from Sumo Logic unless explicitly whitelisted
- Sumo Logic connector stores only aggregate query outputs, not raw message bodies, in normal MVP operation
- all stored timestamps in UTC
- review actions must be auditable

## Operational Requirements
- jobs must be idempotent
- concurrent duplicate analysis runs should be deduplicated or serialized
- partial source failure must not corrupt existing data
- ingestion and analysis logs must carry correlation ids
- stale data must be visible in API and UI
- FastAPI docs and health endpoints should remain available even when no recommendation data exists

## Observability For This App
The MVP app itself should expose:
- job success/failure counts
- source latency
- source failure count by connector
- analysis duration
- recommendation count by type
- agent failure count

## Data Retention For MVP
- normalized metric points retained at least 45 days
- recommendations retained indefinitely in MVP
- reports retained indefinitely in MVP
- source error logs retained per default app logging policy

## Recovery Expectations
- failed job can be retried safely
- failed agent step does not require DB rollback of completed deterministic analysis
- source cursor only advances after successful persistence

## Edge Cases
- expired credentials
- revoked API access
- DB unavailable during analysis persistence
- operator retries same review action
- resource deleted after analysis but before review

## Acceptance Criteria
- recommendation detail can always show whether source data was stale
- app can start and run in read-only mode against source systems
