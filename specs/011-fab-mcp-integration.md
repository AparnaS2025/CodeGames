# FAB MCP Integration

## Target FAB Workspace
Use the FAB workspace:

```text
capacity-advisor
```

Workspace URL:

```text
https://fab.wolterskluwer.ai/workspaces/capacity-advisor
```

## Phase 0 Contract
FAB agents orchestrate and explain. The FastAPI service remains the source of truth for ingestion, normalization, deterministic analysis, policy validation, recommendation persistence, reports, and review decisions.

Agents must not claim that capacity has been changed. The MVP remains advisory-only until a separate approved execution workflow exists.

## Tool Boundary
Execution agents may use:
- `run_ingestion`
- `run_analysis`
- `list_runs`
- `get_run_status`
- `list_recommendations`
- `get_latest_report`

Review agents should normally use only read/explanation tools:
- `list_resources`
- `get_resource`
- `list_recommendations`
- `get_recommendation`
- `get_latest_report`
- `ask_review_assistant`

Write-side review tools, approval tools, and any future scaling tools require a separate permissioning design.

## Phase 1 MCP Server
The MCP server wraps the existing FastAPI contract instead of duplicating business logic.

Environment:
- `COW_API_BASE_URL`: FastAPI base URL, default `http://127.0.0.1:8000`
- `COW_API_BEARER_TOKEN`: optional bearer token forwarded to FastAPI
- `COW_API_TIMEOUT_SECONDS`: FastAPI request timeout, default `30`
- `COW_MCP_BEARER_TOKEN`: optional bearer token required by the MCP HTTP endpoint
- `COW_MCP_HOST`: MCP bind host, default `127.0.0.1`
- `COW_MCP_PORT`: MCP port, default `8001`
- `COW_MCP_PATH`: MCP path, default `/mcp`

Local run:

```powershell
python -m app.mcp_server
```

FAB Studio should connect to:

```text
http://<host>:8001/mcp
```

When `COW_MCP_BEARER_TOKEN` is set, configure FAB with:

```json
{ "Authorization": "Bearer <token>" }
```

## Production Notes
The API still executes ingestion and analysis synchronously behind `202` responses, but every accepted run is now persisted in `capacity_runs`.

FAB agents should provide an `idempotency_key` whenever they call `run_ingestion` or `run_analysis`. If the same key is submitted again for the same run type, the API returns the existing run result instead of creating duplicate recommendations or reports.

Run status endpoints:
- `GET /api/runs`
- `GET /api/runs/{run_id}`

MCP tools:
- `list_runs`
- `get_run_status`

The next production hardening step is to move execution to true background jobs while keeping the same run-status contract.

## Azure Functions Deployment
The repo includes an Azure Functions v2 Python entrypoint in `function_app.py`. It adapts the FastMCP Streamable HTTP app to Azure Functions through `azure.functions.AsgiFunctionApp`.

Azure Functions files:
- `function_app.py`: ASGI wrapper for the MCP server
- `host.json`: removes the default `/api` route prefix so the MCP endpoint is `/mcp`
- `.funcignore`: excludes local DBs, tests, and workspace artifacts from publish
- `local.settings.sample.json`: local settings template
- `scripts/deploy_azure_mcp.ps1`: Azure CLI/Core Tools deployment helper

Required Azure app settings:
- `COW_API_BASE_URL`: deployed FastAPI API URL reachable from Azure
- `COW_MCP_BEARER_TOKEN`: shared bearer token configured in FAB
- `PYTHON_ENABLE_INIT_INDEXING`: `1`

Deploy with:

```powershell
.\scripts\deploy_azure_mcp.ps1 `
  -ResourceGroup "<resource-group>" `
  -FunctionAppName "<function-app-name>" `
  -StorageAccountName "<globally-unique-storage-name>" `
  -CowApiBaseUrl "https://<fastapi-host>" `
  -McpBearerToken "<long-random-secret>" `
  -Location "eastus"
```

FAB MCP URL:

```text
https://<function-app-name>.azurewebsites.net/mcp
```

FAB custom header:

```json
{ "Authorization": "Bearer <long-random-secret>" }
```

Azure Functions must support streaming HTTP for this MCP transport. If FAB discovery fails with streaming/SSE errors after deployment, deploy the same server as a long-running container on Azure Container Apps or App Service instead.
