# Local LangGraph Agent

## Purpose
The local LangGraph agent is an in-app orchestration adapter for environments where FAB Studio is unavailable, slow, or not yet approved for production use.

It does not replace the deterministic analysis engine. It routes user intent, calls existing service tools, and summarizes persisted state.

## Endpoint

```text
POST /api/agent/ask
```

Request:

```json
{
  "query": "Run the full capacity cycle",
  "run_label": "optional-stable-label"
}
```

Response:

```json
{
  "answer": "...",
  "intent": "execute_cycle",
  "run_label": "...",
  "tool_calls": [],
  "source_of_truth": "capacity_langgraph_agent"
}
```

## Graph Shape
The graph is intentionally small:

1. `route`: classify the request as execution, run-status lookup, or review.
2. `execute_cycle`: run ingestion, run analysis, inspect run status, fetch latest report, list recommendations.
3. `run_status`: fetch one persisted run by id.
4. `review`: summarize latest report/recommendations or a specific recommendation.

## Tool Boundary
The agent uses `CapacityAgentTools`, which calls `CapacityIntelligenceService` directly. This avoids an internal HTTP hop while keeping the same tool boundary used by MCP/FAB:

- `run_ingestion`
- `run_analysis`
- `get_latest_report`
- `list_recommendations`
- `get_recommendation`
- `list_runs`
- `get_run_status`

## Safety
- The agent always uses idempotency keys for ingestion and analysis.
- The deterministic service remains the source of truth.
- The agent must not claim that capacity changes were applied.
- The existing MCP/FAB adapter remains valid and can coexist with this LangGraph runtime.

## Future LLM Node
The first implementation uses deterministic routing and service tool execution inside LangGraph so it can run without model credentials.

When `AGENT_ENABLE_LLM=true` and Azure OpenAI settings are present, the graph asks the configured chat model to produce the final engineer-facing answer from bounded, trusted facts. Tool sequencing remains guarded by the graph; the model does not directly execute ingestion or analysis.

Local settings:

```powershell
$env:AGENT_ENABLE_LLM="true"
$env:AZURE_OPENAI_ENDPOINT="https://<resource>.openai.azure.com/"
$env:AZURE_OPENAI_API_KEY="<rotated-key>"
$env:AZURE_OPENAI_DEPLOYMENT="<deployment-name>"
$env:AZURE_OPENAI_API_VERSION="2024-10-21"
$env:AGENT_MAX_TOOL_ITERATIONS="6"
```

Use `AGENT_ENABLE_LLM=false` to force deterministic fallback.

The LLM path:
- receives trusted tool outputs and deterministic fallback answer
- may improve explanation quality and ambiguity handling
- must not invent metrics or actions
- must preserve advisory-only language

A later version can add an LLM planning node for read-only flows using the same graph and tools, as long as deterministic services continue to own recommendation truth and policy validation.
