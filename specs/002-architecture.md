# Architecture

## High-Level Components
- data ingestion layer
- normalization layer
- deterministic analysis engine
- agent orchestration layer
- recommendation policy validator
- reporting and review UI
- persistence layer

## High-Level Flow
1. scheduled ingestion job pulls source aggregates
2. source responses are mapped into raw metric payloads
3. normalization converts raw payloads into canonical 5-minute metric points
4. deterministic engine computes features, baselines, and pattern candidates
5. supervisor invokes agents for interpretation and drafting
6. recommendation validator applies hard rules and finalizes output
7. report agent writes resource summaries and weekly portfolio summary
8. operator reviews and records decision

## Component Responsibilities

### Ingestion Layer
- source authentication
- time-window querying
- retry and backoff
- source cursor management
- source health reporting
- connector-per-source modules for Datadog, CloudWatch, and Sumo Logic

### Normalization Layer
- canonical metric naming
- unit conversion
- resource identity mapping
- timestamp normalization to UTC
- provenance retention

### Deterministic Analysis Layer
- percentiles
- seasonal baselines
- anomaly flags
- sustained breach detection
- insufficient-data detection
- data freshness checks

### Agentic Layer
- interpret feature outputs
- explain ambiguous or conflicting signals
- draft recommendation narrative
- generate operator-facing reports
- answer review questions

### Policy Validator
- minimum safe size constraints
- stale-data rules
- database-specific stricter safety rules
- confidence downgrades under uncertainty
- final recommendation status generation

### UI / API Layer
- run ingestion
- run analysis
- list resources
- inspect recommendation detail
- review actions
- latest report view
- FastAPI application with OpenAPI docs and JSON-first contracts

## Persistence Strategy
Use a relational store for the MVP:
- PostgreSQL preferred
- SQLite allowed for local development

Store:
- resources
- source cursors
- normalized metrics
- analysis snapshots
- recommendations
- report snapshots
- review decisions

## Explicit MVP Design Choice
The app is not agent-led end to end.

The app is hybrid:
- code performs ingestion, normalization, statistics, safety checks
- agents explain, classify, summarize, and answer questions
