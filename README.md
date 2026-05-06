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
11. `specs/011-fab-mcp-integration.md`
12. `specs/012-local-langgraph-agent.md`

Project-local skills live under `skills/`.

## Run Locally

`run.ps1` automatically loads local environment variables from `.env` before starting the app. Copy `.env.example` to `.env` if needed, then put your rotated Azure OpenAI key in `.env`.

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
5. `GET /api/runs/{run_id}`

## Local LangGraph Agent

The local agent is available through:

```text
POST /api/agent/ask
```

Example:

```json
{
  "query": "Run the full capacity cycle",
  "run_label": "manual-smoke-test"
}
```

It uses LangGraph as an in-app orchestration adapter over the same deterministic service tools used by MCP/FAB. It is advisory-only and uses idempotency keys for execution runs.

Optional Azure OpenAI-backed summaries can be enabled in `.env`:

```env
AGENT_ENABLE_LLM=true
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<rotated-key>
AZURE_OPENAI_DEPLOYMENT=<deployment-name>
AZURE_OPENAI_API_VERSION=2024-10-21
AGENT_MAX_TOOL_ITERATIONS=6
```

When these are absent or `AGENT_ENABLE_LLM=false`, the agent uses deterministic summaries.

## FAB MCP Server

Phase 1 exposes the existing FastAPI API as MCP tools for FAB agents.

Target FAB workspace:
- `capacity-advisor`
- `https://fab.wolterskluwer.ai/workspaces/capacity-advisor`

1. Start the API:
   - `.\run.ps1`
2. Start the MCP server:
   - `python -m app.mcp_server`
3. Connect FAB Studio to:
   - `http://127.0.0.1:8001/mcp`

Optional environment:
- `COW_API_BASE_URL` controls the wrapped FastAPI URL.
- `COW_MCP_BEARER_TOKEN` requires `Authorization: Bearer <token>` on the MCP endpoint.

For FAB-triggered ingestion or analysis, send an `idempotency_key` so retries return the existing run instead of duplicating recommendations or reports.

## Azure Functions MCP Deployment

The MCP server can be hosted as an Azure Functions Python v2 app through `function_app.py`.

Deploy from a machine with Azure CLI and Azure Functions Core Tools installed:

```powershell
.\scripts\deploy_azure_mcp.ps1 `
  -ResourceGroup "<resource-group>" `
  -FunctionAppName "<function-app-name>" `
  -StorageAccountName "<globally-unique-storage-name>" `
  -CowApiBaseUrl "https://<fastapi-host>" `
  -McpBearerToken "<long-random-secret>" `
  -Location "eastus"
```

Then configure FAB with:

```text
https://<function-app-name>.azurewebsites.net/mcp
```

and:

```json
{ "Authorization": "Bearer <long-random-secret>" }
```
