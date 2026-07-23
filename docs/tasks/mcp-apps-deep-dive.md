# MCP / Apps Deep Dive — How Apps Connect, Cloud vs Hardware, What Works

> **OUTDATED (2026-07-23):** this reference doc is from 2026-06-30 and is
> explicitly superseded on several points by `docs/design/mcp-current-state.md`
> (2026-07-22), which calls out stale/wrong claims here directly. It also
> predates the 2026-07-23 founder ruling that "Sage" is dead product
> terminology (the platform has only agents — owner-facing, customer-facing
> serving the owner, and AskAI); this document uses "Sage" throughout as a
> live concept. Kept for history; do not build from this.

**Created:** 2026-06-30
**Source:** Code audit + `mcp_registry_service.py` + `app_bridge_service.py` + connectors pane
**Status:** reference doc

---

## 1. What MCP is in Empyralis

MCP (Model Context Protocol) is the standard for connecting external apps and tools to the agent. Empyralis doesn't build custom connectors for each app — it uses MCP as the universal bridge.

**How it works:**
1. User connects an app (Gmail, GitHub, Notion, Linear, etc.) via OAuth in the dashboard
2. Empyralis stores the credential in the vault (`CredentialUpsertRequest`, mode=`byok` or `managed`)
3. The app's official MCP server endpoint is registered in the MCP registry
4. When the agent needs a tool, it calls the MCP server via `streamable_http` transport
5. The MCP server handles the actual API call and returns results

**No hardware required.** MCP is purely cloud-to-cloud. The agent in Empyralis cloud calls the app's MCP server (e.g. `https://mcp.notion.com/mcp`) over HTTPS. No Gateway, no local proxy, no hardware.

---

## 2. The two eras: Before MCP vs Now

### Before MCP (legacy connector system)

Emypyralis had a custom connector system where each app needed a custom integration:
- Custom Python code per app in `server_modules/connectors/`
- OAuth flows, credential storage, and API calls all handled manually
- 58 files in `connectors/` directory — most are MCP bridges now, a few are legacy custom connectors

### Now (MCP-first)

Apps declare an `mcpEndpoint` — a URL to their official MCP server. Empyralis:
1. Performs OAuth (gets access token)
2. Stores token in credential vault
3. Registers the MCP server endpoint
4. Agent calls tools via MCP protocol

The 5 remaining custom connectors (not MCP) are channels:
- Telegram, Discord, WhatsApp, Slack, VPS — these are **channels**, not apps. Channels are transport; MCP is for tools/data.

---

## 3. 20 apps with MCP endpoints (wired)

| App | MCP Endpoint | Status |
|-----|-------------|--------|
| **Gmail** | `gmailmcp.googleapis.com/mcp/v1` | Official Google MCP |
| **Google Calendar** | `calendarmcp.googleapis.com/mcp/v1` | Official Google MCP |
| **GitHub** | `api.githubcopilot.com/mcp/` | Official GitHub MCP |
| **Notion** | `mcp.notion.com/mcp` | Official Notion MCP |
| **Linear** | `mcp.linear.app/mcp` | Official Linear MCP |
| **Slack** | `mcp.slack.com/mcp` | Official Slack MCP |
| **Figma** | `mcp.figma.com/mcp` | Official Figma MCP |
| **Dropbox** | `mcp.dropbox.com/mcp` | Official Dropbox MCP |
| **Calendly** | `mcp.calendly.com` | Official Calendly MCP |
| **ClickUp** | `mcp.clickup.com/mcp` | Official |
| **Webflow** | `mcp.webflow.com/mcp` | Official |
| **Monday.com** | `mcp.monday.com/mcp` | Official |
| **Box** | `mcp.box.com` | Official |
| **Atlassian (Jira)** | `mcp.atlassian.com/v1/mcp/authv2` | Official |
| **Miro** | `mcp.miro.com/` | Official |
| **Intercom** | `mcp.intercom.com/mcp` | Official |
| **DocuSign** | `mcp-d.docusign.com/mcp` | Official |
| **Square** | `mcp.squareup.com/sse` | Official (SSE transport) |
| **Typeform** | `api.typeform.com/mcp` | Official |
| **Vercel** | `mcp.vercel.com` | Official |

### Apps without MCP endpoints (credential vault only, no tool discovery)

| App | Status |
|-----|--------|
| **Todoist** | No MCP endpoint — credential only |
| **Canva** | No MCP endpoint — credential only |
| **Stripe** | No MCP endpoint — credential only |
| **Airtable** | No MCP endpoint — credential only |
| **Jira** (via Atlassian) | Has MCP via Atlassian umbrella |
| **WhatsApp** | No MCP endpoint — channel transport, not app |
| **Telegram Bot** | No MCP endpoint — channel transport |
| **iMessage** | No MCP endpoint — channel transport |
| **Signal** | No MCP endpoint — channel transport |
| **WeChat** | No MCP endpoint — channel transport |

---

## 4. How connection works (the OAuth → MCP flow)

From `workstation-sage-connectors-pane.tsx:1032-1035`:
> When `mcpEndpoint` is set, the Connect button triggers OAuth → credential vault → MCP server registration.
> When undefined, the app uses credential vault only (no MCP tool discovery).

```
User clicks "Connect" on Gmail
    │
    ▼
OAuth flow → Google consent screen → access token
    │
    ▼
Credential stored in vault (runtime_models.py CredentialUpsertRequest, mode="byok")
    │
    ▼
MCP server registered: { endpoint: "https://gmailmcp.googleapis.com/mcp/v1", transport: "streamable_http" }
    │
    ▼
Agent can now call: gmail.search, gmail.read, gmail.send, gmail.draft, etc.
```

**Key insight:** The user's credentials (OAuth tokens) stay in Empyralis cloud. The MCP calls go cloud-to-cloud: Empyralis → MCP server → actual API. The user's data never touches user hardware. This is the difference from channels — channels need Gateway because they involve the user's phone number/identity. Apps just need OAuth.

---

## 5. MCP transport: streamable_http only

From `mcp_registry_service.py:30`:
```python
McpTransport = Literal["streamable_http"]
```

MCP supports `streamable_http` transport only. No stdio, no SSE for external servers. This means:
- MCP servers must be remote HTTP endpoints (not local processes)
- All MCP calls are cloud-to-cloud
- No local MCP servers (like a locally running Ollama) are supported — those would need the Gateway

---

## 6. MCP safety

`mcp_registry_service.py:47-89` enforces strict endpoint validation:
- **Blocked hosts:** localhost, 127.0.0.1, ::1, 0.0.0.0
- **Blocked TLDs:** .local, .internal
- **Blocked IPs:** loopback, link-local, multicast, private, unspecified, reserved
- **HTTPS required** in production (HTTP only with `EMPYRALIS_DEV_ALLOW_HTTP_MCP=1`)
- **Rust kernel enforces** state decisions via `rust_runtime_kernel_client`

This means an attacker can't register `http://localhost:8080/mcp` and trick the agent into calling internal services. Every MCP endpoint must be a public, remote, HTTPS URL.

---

## 7. App bridge service — the legacy layer

`app_bridge_service.py` is a separate system that predates MCP. It bridges "apps" (not MCP servers) to the agent. Key concepts:

- **`app_bridge` contract** — metadata field that declares what an app can do
- **Forbidden bridge actions:** `screenshot`, `computer`, `shell`, `mcp.invoke`, `skill.execute`, `codex_cli`, `claude_code_cli` — these are blocked from app bridges
- **Forbidden metadata keys:** `captain_context`, `sage_memory`, `specialist_mode`, `private_context` — prevents apps from injecting context into the agent brain

The app bridge is the pre-MCP system. It's still wired but being replaced by MCP. The forbidden fields show the security boundary: apps can't access hardware, can't inject into agent memory, can't call the CLI.

---

## 8. Empyralis's own MCP server

`mcp_server.py` (root) — Empyralis exposes its own MCP server at `/mcp` for external agents to call INTO Empyralis:

```python
EMPYRALIST_MCP_ENDPOINT = "http://127.0.0.1:8001/mcp"
EMPYRALIST_MCP_TOOLS = ["list_spaces", "get_space_status", "get_recent_alerts", "ask_space"]
```

This is the reverse direction — external agents connect to Empyralis via MCP. It exposes space monitoring and alert tools.

---

## 9. All MCP/app files

### Core MCP
| File | Role |
|------|------|
| `mcp_registry_service.py` | MCP server registration, validation, tool discovery, Rust gate |
| `mcp_server.py` | Empyralis's own MCP server (exposes tools TO external agents) |
| `mcp.json` (root) | MCP configuration |
| `tool_registry_service.py` | Tool registry (new, `??` in git status — uncommitted) |

### App connection
| File | Role |
|------|------|
| `app_bridge_service.py` | Legacy app bridge (pre-MCP) — connects apps to agent context |
| `app_registry_api.py` | App registry REST API |
| `connection_oauth_service.py` | OAuth flows for MCP app connections |
| `connection_catalog_service.py` | Available connection/connector catalog |
| `routes_connectors.py` | Connector/app REST endpoints |
| `routes_connections.py` | Connection CRUD endpoints |
| `connector_validators.py` | Validates connector configurations |
| `connectors_actions.py` | Executes connector/MCP actions |

### Runtime integration
| File | Role |
|------|------|
| `rust_runtime_kernel_client.py` | Rust kernel enforces MCP state decisions |
| `sage_turn_adapter.py` | Adapts Sage turns for MCP tool calls |
| `sage_agent_runtime_service.py` | Sage uses MCP tools during execution |
| `command_registry.py:428-438` | `/mcp` slash command (list servers) |

### Frontend
| File | Role |
|------|------|
| `workstation-sage-connectors-pane.tsx` (7,800+ lines) | App/connector catalog, MCP endpoint definitions, connect flow |
| `workstation-sage-tools-pane.tsx` | Tool discovery and management |
| `workstation-client.ts` | API client for MCP/app operations |

### Connectors directory (58 files)
Most are MCP bridges now. The 5 custom connectors (not MCP) are channels:
`telegram_connector.py`, `discord_connector.py`, `whatsapp_connector.py`, `slack_connector.py`, plus VPS/SSH connectors.

---

## 10. What works vs what doesn't

### Works (proven)
- **MCP registry** — CRUD for MCP servers, endpoint validation, Rust gate enforcement
- **OAuth flows** — Google, GitHub, Notion, Linear, Slack, Figma OAuth wired
- **Credential vault** — token storage with `byok`/`managed` modes
- **Tool discovery** — agent can list tools from registered MCP servers
- **Cloud-to-cloud** — MCP calls go Empyralis → MCP server → API, no hardware needed

### Wired but unverified
- **End-to-end tool execution** — the full pipeline from "agent decides to use MCP tool" → "tool returns result" → "agent incorporates result" is wired but not battle-tested
- **20 MCP endpoints** — listed in CONNECTOR_DETAIL_MAP, but how many actually respond to tool discovery?
- **App bridge → MCP migration** — the legacy app bridge still exists alongside MCP; unclear which path is canonical

### Not wired
- **Rate limiting per MCP server** — no per-app rate limit enforcement
- **MCP cost tracking** — `agent_action_metering_service.py` exists but MCP-specific metering isn't wired
- **Local MCP servers** — no support for Gateway-hosted MCP servers (e.g. local Ollama)

---

## 11. Answer to your questions

**Does MCP require hardware?** No. MCP is cloud-to-cloud. The agent calls the app's MCP server over HTTPS. No Gateway, no local process, no hardware needed.

**Is it just tokens?** Yes. OAuth access token → store in vault → MCP server uses it. The user's credentials never leave Empyralis cloud.

**Before MCP, what was there?** Custom connectors — each app needed a Python integration file in `connectors/`. MCP replaces that with a standard protocol. The old connector files still exist but are being migrated to MCP bridges.

**Does it work?** The registry, OAuth, and endpoint validation work. The 20 MCP endpoints are declared. But end-to-end "agent calls MCP tool and gets a result" hasn't been verified in production.
