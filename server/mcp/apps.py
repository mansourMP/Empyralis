"""MCP app registry — maps provider keys to MCP server endpoints.

5 apps for MVP (Week 2): Google Workspace (3 sub-servers), Slack, Notion.
Same endpoints verified production-ready in MAN-14."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class McpApp:
    server_id: str
    label: str
    endpoint: str
    transport: str = "streamable_http"
    provider: str = ""


# The canonical 5-app registry
APPS: dict[str, list[McpApp]] = {
    "google_workspace": [
        McpApp(server_id="gmail", label="Gmail (MCP)",
               endpoint="https://gmailmcp.googleapis.com/mcp/v1",
               provider="google_workspace"),
        McpApp(server_id="google_calendar", label="Google Calendar (MCP)",
               endpoint="https://calendarmcp.googleapis.com/mcp/v1",
               provider="google_workspace"),
        McpApp(server_id="google_drive", label="Google Drive (MCP)",
               endpoint="https://drivemcp.googleapis.com/mcp/v1",
               provider="google_workspace"),
    ],
    "slack": [
        McpApp(server_id="slack", label="Slack (MCP)",
               endpoint="https://mcp.slack.com/mcp",
               provider="slack"),
    ],
    "notion": [
        McpApp(server_id="notion", label="Notion (MCP)",
               endpoint="https://mcp.notion.com/mcp",
               provider="notion"),
    ],
}

# Flattened: server_id → McpApp
APP_BY_SERVER: dict[str, McpApp] = {
    app.server_id: app for apps in APPS.values() for app in apps
}


def get_app(server_id: str) -> McpApp:
    app = APP_BY_SERVER.get(server_id.strip())
    if app is None:
        raise ValueError(f"Unknown MCP server: {server_id}")
    return app


def list_apps() -> list[McpApp]:
    return list(APP_BY_SERVER.values())
