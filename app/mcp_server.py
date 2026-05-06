from __future__ import annotations

import os
import secrets
from typing import Any

from app.cow_api_client import CapacityApiClient, CapacityApiConfig
from app.cow_mcp_tools import CapacityMcpTools


def _settings_from_env() -> CapacityApiConfig:
    return CapacityApiConfig(
        base_url=os.getenv("COW_API_BASE_URL", "http://127.0.0.1:8000"),
        bearer_token=os.getenv("COW_API_BEARER_TOKEN") or None,
        timeout_seconds=float(os.getenv("COW_API_TIMEOUT_SECONDS", "30")),
    )


def _build_static_auth_provider() -> Any | None:
    expected_token = os.getenv("COW_MCP_BEARER_TOKEN")
    if not expected_token:
        return None

    from fastmcp.server.auth.auth import AccessToken, TokenVerifier

    class StaticBearerTokenVerifier(TokenVerifier):
        async def verify_token(self, token: str) -> AccessToken | None:
            if not secrets.compare_digest(token, expected_token):
                return None
            return AccessToken(
                token=token,
                client_id="fab-agent",
                scopes=["cow:read", "cow:execute"],
                expires_at=None,
            )

    return StaticBearerTokenVerifier(required_scopes=["cow:read"])


def create_mcp_server():
    from fastmcp import FastMCP

    tools = CapacityMcpTools(CapacityApiClient(_settings_from_env()))
    mcp = FastMCP("CoW Capacity Intelligence", auth=_build_static_auth_provider())

    mcp.tool()(tools.run_ingestion)
    mcp.tool()(tools.run_analysis)
    mcp.tool()(tools.list_runs)
    mcp.tool()(tools.get_run_status)
    mcp.tool()(tools.list_resources)
    mcp.tool()(tools.get_resource)
    mcp.tool()(tools.list_recommendations)
    mcp.tool()(tools.get_recommendation)
    mcp.tool()(tools.get_latest_report)
    mcp.tool()(tools.ask_review_assistant)

    return mcp


mcp = create_mcp_server()


def run() -> None:
    host = os.getenv("COW_MCP_HOST", "127.0.0.1")
    port = int(os.getenv("COW_MCP_PORT", "8001"))
    path = os.getenv("COW_MCP_PATH", "/mcp")
    mcp.run(transport="http", host=host, port=port, path=path)


if __name__ == "__main__":
    run()
