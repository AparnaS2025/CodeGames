# Capacity Intelligence Agentic MVP

This repository contains the spec-first, agent-assisted MVP for CoW capacity intelligence.

Current status:
- specs written first
- executable Python MVP in place
- FastAPI API surface exposed for local execution
- sample-backed source connectors available for Datadog, CloudWatch, and Sumo Logic

Review order:
1. `specs/000-product-overview.md`
2. `specs/001-mvp-scope.md`
3. `specs/002-architecture.md`
4. `specs/003-domain-model.md`
5. `specs/004-ingestion-and-normalization.md`
6. `specs/005-analysis-and-recommendation.md`
7. `specs/006-agentic-workflow.md`
8. `specs/007-api-and-ui.md`
9. `specs/008-security-and-operations.md`
10. `specs/009-testing-and-edge-cases.md`

Project-local skills live under `skills/`.

## Run Locally

1. Start the API:
   - `.\run.ps1`
2. Open the dashboard:
   - `http://127.0.0.1:8000/dashboard`
3. Open FastAPI docs:
   - `http://127.0.0.1:8000/docs`

To regenerate the scored report from the previously fetched 30-day Sumo database before starting the server:
- `.\run.ps1 -GenerateReport`

To use a different database or port:
- `.\run.ps1 -DbPath "C:\capacity-intelligence-agentic\data\sumo_live_scored_report_30d.db" -Port 8001`

## Common Flow

1. `POST /api/ingestion/run`
2. `POST /api/analysis/run`
3. `GET /api/recommendations`
4. `GET /api/reports/latest`
