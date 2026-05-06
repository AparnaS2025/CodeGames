from __future__ import annotations

import azure.functions as func

from app.mcp_server import create_mcp_server


mcp = create_mcp_server()
mcp_asgi_app = mcp.http_app(path="/mcp")

app = func.AsgiFunctionApp(
    app=mcp_asgi_app,
    http_auth_level=func.AuthLevel.ANONYMOUS,
)
