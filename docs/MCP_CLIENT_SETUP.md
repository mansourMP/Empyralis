# Connecting AI Clients to Empyralis via MCP

Empyralis exposes itself as a remote MCP server at `https://<your-server>/mcp`.
Any MCP-compatible client (Claude Code, Claude Desktop, ChatGPT, etc.) can
call Empyralis tools directly.

## 1. Create an API Key

First, create an MCP API key for your workspace:

```
POST /api/connections/mcp-keys
{"workspace_id": "ws-1", "label": "Claude Code"}
```

The response includes the plaintext key **once** — save it:

```json
{
  "ok": true,
  "key": "empyralis_mcp_abc123...",
  "key_id": "mcp_key_...",
  "workspace_id": "ws-1",
  "label": "Claude Code"
}
```

Manage keys:
- **List:** `GET /api/connections/mcp-keys?workspace_id=ws-1`
- **Revoke:** `DELETE /api/connections/mcp-keys/{key_id}`

## 2. Connect Claude Code

```bash
claude mcp add --transport http --scope user empyralis \
  https://your-server.example.com/mcp \
  -H "Authorization: Bearer empyralis_mcp_YOUR_KEY_HERE"
```

Verify it's connected:
```bash
claude mcp list
```

Then in Claude Code, use:
- `empyralis_list_agents` — see all agents in your workspace
- `empyralis_chat` — send a message through the full Empyralis turn
- `empyralis_memory_read` / `empyralis_memory_list` — access workspace memory

## 3. Connect Claude Desktop

Claude Desktop does not natively support custom headers on remote MCP servers.
Use the `mcp-remote` bridge:

```json
{
  "mcpServers": {
    "empyralis": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote",
        "https://your-server.example.com/mcp",
        "--header", "Authorization: Bearer empyralis_mcp_YOUR_KEY_HERE"
      ]
    }
  }
}
```

Add this to `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%/Claude/claude_desktop_config.json` (Windows).

## 4. Connect ChatGPT

ChatGPT supports remote MCP servers via OAuth 2.0.  Create an API key as
described above, then add the server URL in ChatGPT's Settings > Apps &
Connectors.  Note: ChatGPT currently documents SSE transport — check
whether streamable HTTP is now supported.

## 5. Available Tools

### Read + Chat (always live)

| Tool | Description |
|------|-------------|
| `empyralis_list_agents` | List all agents in your workspace |
| `empyralis_get_agent_activity` | Get recent ledger events for an agent |
| `empyralis_memory_read` | Read a memory entry by key |
| `empyralis_memory_list` | List all memory entries |
| `empyralis_chat` | Full turn through Sage (triage → reasoning → reply) |

### Write (requires `EMPYRALIS_MCP_WRITE_ENABLED=true` on server)

| Tool | Description |
|------|-------------|
| `empyralis_create_agent` | Create a new specialist agent |
| `empyralis_configure_agent` | Configure agent settings |
| `empyralis_message_agent` | Send message to agent's fleet inbox |
| `empyralis_memory_write` | Write a memory entry |

### Vision (hardware monitoring)

| Tool | Description |
|------|-------------|
| `list_spaces` | List monitored physical spaces |
| `get_space_status` | Get current state for a space |
| `get_recent_alerts` | Recent unresolved alerts |
| `ask_space` | Natural-language question about a space |

## 6. Security Notes

- **API keys are workspace-scoped.** A key for `ws-alpha` cannot access `ws-beta`.
- **All MCP calls are ledgered** with `event_class: "mcp_inbound"` and
  `actor: "external_mcp_client"`.
- **The workspace is resolved from the key, never from tool arguments.**
  You cannot pass a different `workspace_id` to impersonate another workspace.
- **Write tools are gated** behind `EMPYRALIS_MCP_WRITE_ENABLED=true`.
  Until enabled, only read and chat tools are available.
- **Operator role rules apply.** The MCP caller acts as the workspace's
  Sage identity (operator).  Specialist-only tools are not accessible
  to external MCP clients.

## 7. Troubleshooting

**"Missing MCP API key" error:**
Create a key via `POST /api/connections/mcp-keys` and include it as a
`Bearer` token in the `Authorization` header.

**"Invalid or revoked MCP API key" error:**
The key has been revoked or is invalid. Create a new key.

**"MCP write tools are not enabled" error:**
The server administrator must set `EMPYRALIS_MCP_WRITE_ENABLED=true`.

**Claude Desktop not connecting:**
Claude Desktop's native OAuth for remote MCP servers is unreliable.
Use the `mcp-remote` bridge with a bearer token (see section 3).
