# Connecting AI Clients to Empyralis via MCP

Empyralis exposes itself as a remote MCP server at `https://<your-server>/mcp`
(e.g. `https://empyralis.ai/mcp`). Any MCP-compatible client (Claude Code,
Claude Desktop, ChatGPT, etc.) can call Empyralis tools directly, scoped to
one workspace. Implementation: `mcp_server.py`.

## 1. Create an API Key

First, create an MCP API key for your workspace:

```
POST /api/connections/mcp-keys
{"workspace_id": "ws-1", "label": "Claude Code", "writes_enabled": false}
```

The response includes the plaintext key **once** — save it:

```json
{
  "ok": true,
  "key": "empyralis_mcp_abc123...",
  "key_id": "mcp_key_...",
  "workspace_id": "ws-1",
  "label": "Claude Code",
  "writes_enabled": false
}
```

Set `"writes_enabled": true` when creating the key if this client should be
able to call the write tools (section 5) — it's a per-key flag, not just the
server-wide switch (see section 6).

Manage keys:
- **List:** `GET /api/connections/mcp-keys?workspace_id=ws-1`
- **Revoke:** `DELETE /api/connections/mcp-keys/{key_id}`

## 2. Connect Claude Code

```bash
claude mcp add --transport http empyralis \
  https://empyralis.ai/mcp \
  --header "Authorization: Bearer empyralis_mcp_YOUR_KEY_HERE"
```

Verify it's connected:
```bash
claude mcp list
```

Then in Claude Code, use any of the tools in section 5 — e.g.:
- `empyralis_list_agents` — see all agents in your workspace
- `empyralis_chat` — send a message through the full Empyralis turn
- `empyralis_list_my_tasks` / `empyralis_get_task` — pull tasks off the
  shared project board

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
        "https://empyralis.ai/mcp",
        "--header", "Authorization: Bearer empyralis_mcp_YOUR_KEY_HERE"
      ]
    }
  }
}
```

Add this to `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%/Claude/claude_desktop_config.json` (Windows).

## 4. Connect ChatGPT / other OAuth clients

Empyralis also supports MCP OAuth 2.1 (PKCE + dynamic client registration)
as an opt-in alternative to the bearer key, gated behind
`EMPYRALIS_MCP_OAUTH_ENABLED=true` on the server (see `mcp_oauth_provider.py`).
When enabled, add the server URL in the client's Settings > Apps & Connectors
and complete the consent flow tied to your dashboard login — no manual key
needed. When disabled (the default), use the bearer-key path in sections 2–3
instead.

## 5. Available Tools

Full source of truth: the `EMPYRALIST_MCP_TOOLS` list and tool definitions in
`mcp_server.py`.

### Read + Chat (always live)

| Tool | Description |
|------|-------------|
| `empyralis_list_projects` | List projects (client/company groupings of agents) in your workspace |
| `empyralis_list_agents` | List agents in your workspace, with project/channel/connector info |
| `empyralis_get_agent_activity` | Recent ledger activity for one agent |
| `empyralis_get_agent_conversations` | A deployed agent's recent conversations with its end customers |
| `empyralis_chat` | Full turn through the owner-facing agent (triage → reasoning → reply) |

### Tasks (always live — bounded to tasks already visible through this key)

| Tool | Description |
|------|-------------|
| `empyralis_create_task` | Create a task on a project's shared board |
| `empyralis_list_my_tasks` | List tasks assigned to this key's external-agent identity, or unassigned/backlog |
| `empyralis_get_task` | Get one task by id |
| `empyralis_update_task_status` | Update a task's status (open / in_progress / blocked / awaiting_input / done) |
| `empyralis_comment_on_task` | Post a progress comment on a task |

These are not gated behind `EMPYRALIS_MCP_WRITE_ENABLED` — they're bounded to
tasks already visible through this same key, not a workspace-wide
configuration mutation. See the write-gate rationale in `mcp_server.py`'s
module docstring.

### Write (requires `EMPYRALIS_MCP_WRITE_ENABLED=true` on the server **and**
`writes_enabled=true` on the key)

| Tool | Description |
|------|-------------|
| `empyralis_create_project` | Create a project |
| `empyralis_create_agent` | Create a new specialist agent, optionally inside a project |
| `empyralis_configure_agent` | Configure agent settings |
| `empyralis_message_agent` | Not implemented — always returns `ok: false` (no delivery path yet); use tasks instead |
| `empyralis_assign_channel_bot` | Bind a BYO Telegram/Discord bot to an agent |
| `empyralis_release_channel_bot` | Release an agent's bot for a channel |
| `empyralis_connect_connector` | Start an OAuth connector grant (e.g. gmail, github, slack); returns an authorization URL for a human to open |
| `empyralis_trigger_test_turn` | Send a test message to a deployed agent without a real customer |

## 6. Security Notes

- **API keys are workspace-scoped.** A key for `ws-alpha` cannot access `ws-beta`.
- **All MCP calls are ledgered** with `event_class: "mcp_inbound"` and
  `actor: "external_mcp_client"`.
- **The workspace is resolved from the key, never from tool arguments.**
  You cannot pass a different `workspace_id` to impersonate another workspace.
- **Write tools need two gates open at once:** the server-wide
  `EMPYRALIS_MCP_WRITE_ENABLED=true` switch AND `writes_enabled=true` on the
  specific key. Either one being off blocks all write tools for that key.
- **Operator role rules apply.** The MCP caller acts as the workspace's
  owner-facing agent identity (operator). Specialist-only tools are not
  accessible to external MCP clients.
- **DNS-rebinding guard is on.** The server only answers requests whose
  `Host` header matches an explicit allowlist (the public domain + local
  dev/test forms) — an unrecognized `Host` gets rejected before any tool
  runs.

## 7. Troubleshooting

**"Missing MCP API key" error:**
Create a key via `POST /api/connections/mcp-keys` and include it as a
`Bearer` token in the `Authorization` header.

**"Invalid or revoked MCP API key" error:**
The key has been revoked or is invalid. Create a new key.

**"MCP write tools are globally disabled" error:**
The server administrator must set `EMPYRALIS_MCP_WRITE_ENABLED=true`.

**"This API key does not have write access" error:**
The server-wide switch is on, but this specific key was created with
`writes_enabled=false`. Create a new key with `writes_enabled: true`.

**Claude Desktop not connecting:**
Claude Desktop's native OAuth for remote MCP servers is unreliable.
Use the `mcp-remote` bridge with a bearer token (see section 3).
