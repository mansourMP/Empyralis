# Empyralis Connectors Architecture Audit
## 2026-06-28 — Deep Architecture Audit

---

# PART A — WHAT EXISTS TODAY

---

## A.1 — APPS TAB (Pre-built Connectors)

### A.1.1 — Connector Catalog

The connector catalog is defined in `runtime_config.py:785`. It declares **48 connector cards**:

| Connector | Auth Fields | Parent |
|-----------|-------------|--------|
| `google_workspace` | access_token | — |
| `gmail` | access_token | google_workspace |
| `google_calendar` | access_token | google_workspace |
| `google_drive` | access_token | google_workspace |
| `microsoft_365` | access_token | — |
| `outlook` | access_token | microsoft_365 |
| `outlook_calendar` | access_token | microsoft_365 |
| `smtp` | host, port, username, password, use_tls | — |
| `telegram_bot` | bot_token, chat_id | — |
| `wechat_work` | webhook_url | — |
| `whatsapp_twilio` | account_sid, auth_token, from_number, to_number | — |
| `apple_messages_business` | msp_provider, business_account_id, api_key, webhook_secret | — |
| `discord_bot` | bot_token, channel_id, guild_id, application_id, public_key, application_public_key | — |
| `slack` | bot_token, user_token, team_id, team_name | — |
| `github` | personal_access_token, app_id, installation_id, private_key_pem | — |
| `dropbox` | access_token | — |
| `figma` | access_token | — |
| `todoist` | access_token | — |
| `airtable` | access_token | — |
| `canva` | access_token | — |
| `asana` | access_token | — |
| `hubspot` | access_token | — |
| `zoom` | access_token | — |
| `calendly` | access_token | — |
| `clickup` | access_token | — |
| `jira` | access_token | — |
| `stripe` | access_token | — |
| `salesforce` | access_token | — |
| `webflow` | access_token | — |
| `monday` | access_token | — |
| `box` | access_token | — |
| `gitlab` | access_token | — |
| `bitbucket` | access_token | — |
| `confluence` | access_token | — |
| `miro` | access_token | — |
| `mailchimp` | access_token | — |
| `pipedrive` | access_token | — |
| `intercom` | access_token | — |
| `docusign` | access_token | — |
| `square` | access_token | — |
| `typeform` | access_token | — |
| `quickbooks` | access_token | — |
| `xero` | access_token | — |
| `freshbooks` | access_token | — |
| `vercel` | access_token | — |
| `s3` | aws_access_key_id, aws_secret_access_key, region | — |
| `notion` | integration_token, access_token | — |
| `linear` | api_key, access_token | — |
| `instagram_business` | access_token, instagram_account_id, page_id | — |
| `irc` | server, port, nick, channel, password, use_tls | — |

Also in the contract overrides (`connectors_actions.py:126`): `browser_automation`, `remote_browser`, `openai_image`, `runway_video`.

**Total: 52 connector definitions.**

### A.1.2 — How Connection Works End-to-End

1. **User enters credentials** (API key / token / OAuth) into the Connectors pane in the frontend
2. **POST /connectors/vault** → `routes_connectors.py:528` → `connectors_actions.py:1568` (`create_connector_vault`)
3. The connector type is matched against a giant if/elif chain (lines 1577-1701) with 40+ branches
4. Each branch calls a `validate_*` function (e.g., `validate_google_workspace_connector`, `validate_github_connector`)
5. These validators make a real API call to verify credentials (e.g., Google API call to `/oauth2/v3/userinfo`, GitHub API call to `/user`)
6. Credentials are JSON-serialized, encrypted with Fernet (PBKDF2-SHA256), and stored in a vault JSON file
7. The vault JSON file lives at `~/.empyralis/state/runtime/credential_vault.json`
8. Tokens are encrypted at rest with `_openssl_encrypt()` → Fernet + PBKDF2

**The validation is custom per service.** Each of the 40+ services has its own `validate_*_connector()` function in the codebase. There is no shared protocol — each one makes its own API calls with its own logic.

### A.1.3 — OAuth Flows

There IS an OAuth service: `connection_oauth_service.py`. It has configurations for:
- **Google Workspace** (`google_workspace`): Full OAuth2 with `offline_access`, scopes for Gmail, Calendar
- **GitHub**: OAuth2 with `repo, read:user` scopes
- **Microsoft 365**: OAuth2 with Mail.ReadWrite, Calendars.ReadWrite, Files.ReadWrite.All
- **Slack**: OAuth2 V2 with bot scopes via `slack_connector.oauth_authorize_url()`
- **Notion**: OAuth2
- **+ many more** configured

**BUT these OAuth flows are only PARTIALLY wired to the frontend.** Only Slack has a working OAuth callback route at `POST /connectors/slack/oauth/callback`. The Google OAuth route is for platform auth (login), not connector OAuth. Most connectors expect users to manually paste access tokens obtained elsewhere.

The OAuth provider config is comprehensive (`OAUTH_PROVIDER_CONFIGS` with 20+ providers), but the **frontend wizard that walks through "Click to authorize" → redirect → callback → store is only implemented for Slack**.

### A.1.4 — How Connectors Are Exposed to the Agent During Inference

**This is the critical architectural issue.** Connectors are NOT exposed as MCP tools. Here's the actual flow:

1. `skills_service.py` (3948 lines) builds a tool catalog from `_CONNECTOR_CONTRACT_OVERRIDES` and `CONNECTOR_CATALOG`
2. Each connector declares `capability_patterns` (e.g., `browse_drive`, `create_document`)
3. These patterns are translated into "tool descriptors" via `tool_name_for_action(connector_id, action_id)` → produces a tool name like `google_workspace__browse_drive`
4. At inference time, the agent sees these as function-call tools in the LLM request
5. When the agent calls a tool, `skills_service.py:_execute_custom_connector_tool_call_sync()` dispatches:
   - Resolves the encrypted credential from the vault
   - Decrypts it
   - Makes a direct API call using service-specific logic (e.g., `google_workspace_list_drive_children()`)
   - Returns the result

**The connector execution pipeline is:**
```
Agent calls tool → skills_service dispatches → resolve_credential from vault 
→ decrypt → custom API call (NOT MCP) → return result
```

**There is ZERO MCP protocol usage for connectors/apps.** The MCP client code in `mcp_registry_service.py` (which uses the official `mcp` Python SDK, Streamable HTTP transport) is ONLY used for the Advanced tab's manually-registered MCP servers.

### A.1.5 — Credential Storage

- **Vault file**: `~/.empyralis/state/runtime/credential_vault.json` (or configurable via `EMPYRALIS_STATE_HOME`)
- **Encryption**: Fernet symmetric encryption with PBKDF2-SHA256 key derivation (120,000–3,000,000 iterations)
- **Key**: Stored in `~/.empyralis/state/runtime/.vault_key` or `CREDENTIAL_VAULT_KEY` env var
- **Backup**: Encrypted blob synced to cloud control plane via `vault_store.py:_backup_vault_to_cloud()`
- **Each credential entry** has: `id`, `label`, `provider`, `workspace_id`, `mode`, `metadata`, `created_at`, `updated_at`, `encrypted_secret`
- **No credential vault pattern**: Credentials are stored PER WORKSPACE but there's no `vault_id` that agents reference at session time. The entire vault is loaded/decrypted for the workspace.

### A.1.6 — What Is Broken / Incomplete / Hardcoded

1. **45+ connectors have stub validators with default test result**: `create_connector_vault()` at line 1574 defaults to `test: {"ok": True, "status": "healthy", "message": "OAuth token stored."}` for connectors in the generic bucket (figma, todoist, airtable, canva, asana, hubspot, zoom, calendly, clickup, jira, stripe, salesforce, webflow, monday, box, gitlab, bitbucket, confluence, miro, mailchimp, pipedrive, intercom, docusign, square, typeform, quickbooks, xero, freshbooks, vercel). These connectors **accept any token and say "healthy" without actually validating**.

2. **No unified MCP pipeline**: Apps and Advanced are two completely separate code paths. Apps go through custom API logic; Advanced goes through MCP protocol.

3. **OAuth wizard is only wired for Slack**: The OAuth service has configs for 20+ providers but only Slack has a working end-to-end OAuth flow with callback.

4. **No token refresh**: OAuth tokens are stored once and never refreshed. The `access_type: offline` param is in the Google config, but there's no refresh token handling.

5. **Mixed concerns in connector catalog**: Channel connectors (telegram_bot, whatsapp_twilio, discord_bot, slack) are in the same catalog as App connectors (google_workspace, notion, linear). These should be architecturally separate.

6. **Channel-as-connector confusion**: `telegram_bot`, `whatsapp_twilio`, `discord_bot`, `slack` are defined as connectors with `surface_role: CONNECTOR_SURFACE_CHANNEL_SHELL` — they blur the line between Apps and Channels since they have both messaging transport AND app-like capabilities.

7. **Browser connectors have no MCP bridge**: `browser_automation` and `remote_browser` are connector classes but have no credential storage or MCP bridge. They're declared but not functional as connectors.

8. **No MCP server auto-registration for Apps**: When a user connects Google Workspace, no MCP server is registered. The agent gets custom tools (`google_workspace__browse_drive`) instead of MCP tools.

---

## A.2 — CHANNELS TAB

### A.2.1 — Channel Architecture

Channels are implemented across multiple layers:

| Channel | Transport | Webhook? | Gateway? | Inbound Flow | Outbound Flow |
|---------|-----------|----------|----------|--------------|---------------|
| **Telegram Bot** | Webhook / Polling | Yes | No | `routes_connectors.py:192` → `telegram_webhook_canonical()` → `agent_channel_router` | Telegram Bot API via `telegram_send_message` |
| **WhatsApp (Twilio)** | Webhook | Yes | No | `routes_connectors.py:183` → `whatsapp_twilio_webhook()` → Twilio handler | Twilio API via `whatsapp_transport_service` |
| **Discord (Bot)** | Webhook / Gateway DM | Yes | Optional | `routes_connectors.py:201` → `discord_webhook()` / `routes_gateway.py` for DMs | Discord REST API |
| **Slack** | Events API | Yes | No | `routes_connectors.py:219` → `slack_events_webhook()` → `agent_channel_router` | Slack Web API |
| **GitHub** | Webhook | Yes | No | `routes_connectors.py:210` → `github_events_webhook()` → `agent_channel_router` | N/A (event-triggered) |
| **Telegram Personal** | Gateway WS | No | **Required** | Gateway → `agent_channel_router` → Sage | Gateway → Telegram |
| **WhatsApp Personal** | Gateway WS | No | **Required** | Gateway → `agent_channel_router` → Sage | Gateway → WhatsApp |
| **iMessage** | BlueBubbles bridge | No | **Required** | Gateway → `routes_imessage.py` | Gateway → BlueBubbles |
| **Signal** | Local bridge | No | **Required** | Gateway → `agent_channel_router` | Gateway → signald |
| **WeChat** | Local bridge | No | **Required** | Gateway → `routes_wechat.py` | Gateway → WeChat |

### A.2.2 — Inbound Message Flow (Webhook Channels)

For cloud-hosted channels (Telegram Bot, WhatsApp Twilio, Slack, Discord Guild, GitHub):

```
External platform webhook → Public FastAPI route → Signature verification 
→ Parse inbound event → Legacy append channel event → Check should_trigger_agent_run 
→ Build run goal → agent_channel_router.route_inbound_channel_message() 
→ Sage agent runtime → Response → Send via platform API
```

### A.2.3 — Inbound Message Flow (Personal/Gateway Channels)

For personal channels requiring a local Gateway:

```
User's device (phone/computer) → Empyralis Gateway (local process) 
→ WebSocket to cloud → gateway_protocol_service.py → agent_channel_router 
→ Sage agent runtime → Response → Gateway WS → Local device → Platform API/SDK
```

### A.2.4 — What "Gateway" Means

The **Gateway** is a **local process** (`empyralis-gateway/`) running on the user's machine. It:
1. Maintains a persistent WebSocket connection to the cloud
2. Bridges local channels that the cloud can't reach: **iMessage** (macOS only), **Signal** (local signald), **WeChat** (local client), **WhatsApp Personal** (browser automation / QR pairing), **Telegram Personal** (browser automation / QR pairing)
3. Also provides **browser automation** (Playwright), **shell access**, and **file access** for the agent
4. Uses a JSON-RPC protocol over WebSocket (protocol version `v1alpha2`)

The Gateway is why some channels require hardware and some don't. Cloud-only channels use publicly reachable webhooks. Gateway channels need the local process to interact with local apps/APIs.

### A.2.5 — Channel Routing (agent_channel_router.py)

The channel router (`agent_channel_router.py`, ~2205 lines) is the central dispatch for ALL inbound channel messages. It:
1. Receives a normalized `ChannelRoutingContext` dataclass
2. Resolves the workspace/deployed agent mapping
3. Checks blocking policies, quotas, kill-switches
4. Dispatches to Sage via `execute_sage_turn()`
5. Handles response routing back through the channel

### A.2.6 — What Is Broken / Incomplete / Missing

1. **Channel pairing is fragile**: Channel-to-workspace mapping is stored in JSON files on disk (`sage_telegram_hosted_pairs.json`). No database table.

2. **WeChat and iMessage require specific hardware**: WeChat needs a Windows/Mac with WeChat installed; iMessage needs a Mac with BlueBubbles.

3. **No Signal implementation beyond bridge declaration**: `signal_personal` is declared in `agent_channel_router.py:63` as a `signal_local_bridge` but there's no Signal-specific connector code.

4. **Discord DM path is fragmented**: Three paths exist — webhook (Path A), gateway DM (Path C), and a forward-compatible webhook DM block (Path B). The comments in code acknowledge this is messy.

5. **Channel state is split between vault and flat files**: Channel pairing state (Telegram chat_id ↔ workspace) is in flat JSON files, not the encrypted vault. Channel credentials (bot tokens) are in the vault. These drift independently.

6. **No WeChat Work implementation**: `wechat_work` is declared in the connector catalog but has no webhook route or transport implementation beyond a validator.

7. **"Cloud only" vs "Gateway required" labels exist in frontend only**: The `ChannelRouteMode` type (`'cloud' | 'hardware'`) in the frontend determines the label, but the backend doesn't enforce routing constraints based on hardware availability.

---

## A.3 — ADVANCED TAB (MCP Servers)

### A.3.1 — What Exists

The MCP registry is a **well-implemented subsystem** in `mcp_registry_service.py` (~1170 lines). It has:

1. **Full MCP client**: Uses the official `mcp` Python SDK (`mcp.ClientSession`, `mcp.client.streamable_http.streamable_http_client`)
2. **Transport**: Streamable HTTP only (`_normalize_transport()` always returns `"streamable_http"`)
3. **Server registry**: JSON file at `~/.empyralis/state/runtime/mcp_servers.json`
4. **Tool discovery**: `discover_mcp_server_tools()` → connects to MCP server → calls `session.list_tools()` → normalizes results
5. **Tool approval**: Discovered tools are NOT auto-approved. Must be explicitly approved via `approve_mcp_tool()`
6. **Tool execution**: `invoke_workspace_mcp_skill_async()` → connects to MCP server → calls `session.call_tool()` → returns result
7. **Integration with skill registry**: `skill_registry.py:779` loads MCP skill entries into the agent's available tools at inference time
8. **Endpoint validation**: Blocks localhost, private IPs, reserved TLDs (`.local`, `.internal`), loopback, link-local, multicast addresses
9. **Rust kernel policy gate**: All registry mutations go through `rust_runtime_kernel_client.runtime_state_store_decision()`

### A.3.2 — How "Save and Discover Tools" Works

1. User pastes MCP server URL into the Advanced tab
2. `POST` to upsert route → `upsert_workspace_mcp_server()` with `discover_tools=True`
3. Backend connects to the MCP server endpoint via Streamable HTTP
4. Calls `session.initialize()` then `session.list_tools()`
5. Normalizes discovered tools (adds risk levels, action classes, permission manifests)
6. Stores tools in the MCP server registry JSON file
7. **Tools are NOT auto-approved** — a warning is logged: "MCP server %s: %d tools discovered but not auto-approved. Use approve_mcp_tool()."
8. The discovered tools become available in the skill registry as entries with `execution_adapter: "mcp_tool"`
9. At inference time, the agent sees these as callable tools
10. When the agent calls one, `skill_registry.py:904` routes to `mcp_registry_service.invoke_workspace_mcp_skill_async()`

### A.3.3 — What Is Broken / Incomplete / Missing

1. **No credential injection**: MCP servers that require authentication (OAuth Bearer tokens, API keys) have no mechanism to inject credentials. The MCP client connects with no auth headers.

2. **Tool approval is a manual step**: Discovered tools are blocked by default. There's no workflow for the user to review and approve discovered tools in the UI. The `approve_mcp_tool()` function exists but isn't exposed via a route.

3. **No MCP server URL for Apps**: When a user connects Google Workspace in the Apps tab, it does NOT register an MCP server. The Apps pipeline is completely separate from the MCP pipeline.

4. **No Empyralis-hosted MCP servers**: There's no infrastructure for Empyralis to host MCP servers for popular services (Gmail, Google Calendar, Slack, etc.). Users must find and paste MCP server URLs themselves.

5. **No OAuth → MCP bridge**: The OAuth service (`connection_oauth_service.py`) can obtain tokens, but there's no path from OAuth token → MCP server registration with credential injection.

6. **The Empyralis MCP server (`mcp_server.py`)** is a standalone FastMCP server that exposes `list_spaces`, `get_space_status`, `get_recent_alerts`, `ask_space` — it's for the vision monitor skill, not for the platform's own tools.

---

# PART B — INDUSTRY STANDARD GAP ANALYSIS

## B.1 — The Correct Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    AGENT INFERENCE                           │
│  Agent sees tools from: [Channels don't give tools]         │
│  - MCP servers (Apps tab) → managed OAuth + MCP             │
│  - MCP servers (Advanced) → manual URL + MCP                │
│  - Built-in skills (Skill.md, memory, commands)              │
│  - Computer use (browser, shell, files via Gateway)          │
├─────────────────────────────────────────────────────────────┤
│                    UNIFIED MCP CLIENT LAYER                  │
│  ┌──────────────────────┐  ┌───────────────────────────┐    │
│  │ Apps (Managed)       │  │ Advanced (Manual)         │    │
│  │ OAuth wizard → token │  │ Paste URL → discover      │    │
│  │ → vault → MCP server │  │ tools → approve → invoke  │    │
│  │ registration with    │  │                            │    │
│  │ credential injection │  │ SAME MCP CLIENT PIPELINE  │    │
│  └──────────────────────┘  └───────────────────────────┘    │
├─────────────────────────────────────────────────────────────┤
│                    CHANNELS (message routing only)           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │Telegram  │ │WhatsApp  │ │ Discord  │ │  Slack   │  ...  │
│  │ webhook  │ │ webhook  │ │ webhook  │ │ webhook  │       │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘       │
│       └─────────────┴────────────┴────────────┘             │
│                          │                                   │
│                    Normalize → Route → Agent → Reply        │
└─────────────────────────────────────────────────────────────┘
```

**Apps and Advanced share the same MCP client pipeline. The only difference is how the MCP server URL + credentials are registered (wizard vs. manual). Channels are pure message routing.**

## B.2 — Gap Analysis

### GAP-1: Apps and Advanced Are Separate Pipelines
- **Current**: Apps use custom API validators + vault storage + `skills_service.py` tool dispatch. Advanced uses MCP protocol + JSON registry + `mcp_registry_service.py`.
- **Correct**: Both should feed into the SAME MCP client pipeline.
- **Severity**: **P0** — This is the fundamental architectural defect.
- **Complexity**: **Large** — Requires re-architecting all 40+ App connectors to emit MCP server registrations.

### GAP-2: No Credential Vault Pattern
- **Current**: All credentials are stored in a single JSON vault file. MCP server registrations don't reference credentials.
- **Correct**: Credentials should be stored with a vault_id. MCP server registrations should reference vault_id. At session time, credentials are injected into the MCP client.
- **Severity**: **P0** — MCP servers that need auth tokens can't work without this.
- **Complexity**: **Medium** — Requires schema changes and injection logic.

### GAP-3: No OAuth → MCP Bridge
- **Current**: OAuth service can obtain tokens. MCP registry can register servers. But there's nothing connecting them.
- **Correct**: OAuth flow → token → vault → MCP server registration with credential injection.
- **Severity**: **P0** — The "Connect Gmail with one click" experience doesn't exist.
- **Complexity**: **Medium** — The OAuth service and MCP registry both exist; just need the bridge.

### GAP-4: No Empyralis-Hosted MCP Servers
- **Current**: Users must bring their own MCP server URLs. No Empyralis-hosted MCP servers exist.
- **Correct**: Empyralis should host/manage MCP servers for popular services that expose tools to the agent.
- **Severity**: **P1** — Without this, "Connect Gmail" is just a token storage box.
- **Complexity**: **Large** — Requires building or proxying MCP servers for 20+ services.

### GAP-5: Channel/App Confusion
- **Current**: `telegram_bot`, `whatsapp_twilio`, `discord_bot`, `slack` are in both the connector catalog AND have channel webhook routes.
- **Correct**: Channels should be pure message adapters with NO presence in the Apps/Connector catalog.
- **Severity**: **P1** — Architectural clarity issue.
- **Complexity**: **Medium** — Requires separating channel config from app config.

### GAP-6: Stub Validators for 30+ Connectors
- **Current**: figma, todoist, airtable, canva, asana, hubspot, zoom, calendly, clickup, jira, stripe, salesforce, webflow, monday, box, gitlab, bitbucket, confluence, miro, mailchimp, pipedrive, intercom, docusign, square, typeform, quickbooks, xero, freshbooks, vercel all have default "healthy" test results without actually validating.
- **Correct**: Every connector should validate credentials against the service's API.
- **Severity**: **P1** — Users get false green status.
- **Complexity**: **Medium** — Each needs a real API call.

### GAP-7: No MCP Credential Injection
- **Current**: MCP client connects with no auth headers.
- **Correct**: MCP client should inject OAuth bearer tokens or API keys from the credential vault.
- **Severity**: **P0** — Authenticated MCP servers can't work.
- **Complexity**: **Small** — The `mcp` SDK supports custom headers in transport.

### GAP-8: Tool Approval Has No UI
- **Current**: MCP tools are discovered but not auto-approved. `approve_mcp_tool()` exists but isn't exposed via a route.
- **Correct**: After discovery, show tools in the UI with approve/deny toggles.
- **Severity**: **P2** — Users can't use discovered MCP tools without manual intervention.
- **Complexity**: **Small** — Add routes + frontend toggle UI.

---

# PART C — IMPLEMENTATION PLAN

## Phase 1 — Unify Apps + Advanced into a Single MCP Tool Layer

### Step 1.1: Credential Vault (v2)

Create a proper credential vault that supports referencing credentials by ID at session time.

**New database schema:**

```python
# SQLite or Postgres table (migration required)
CREATE TABLE credential_vault (
    id TEXT PRIMARY KEY,           -- UUID
    workspace_id TEXT NOT NULL,
    provider TEXT NOT NULL,        -- "google_workspace", "github", etc.
    label TEXT,
    auth_mode TEXT,                -- "oauth2", "api_key", "bot_token"
    token_type TEXT,               -- "bearer", "basic", "api_key"
    access_token TEXT,             -- encrypted
    refresh_token TEXT,            -- encrypted (if OAuth2)
    expires_at TEXT,               -- ISO 8601
    scope TEXT,                    -- space-separated scopes
    metadata JSON,                 -- provider-specific metadata
    created_at TEXT,
    updated_at TEXT,
    INDEX idx_credential_workspace (workspace_id, provider)
);

CREATE TABLE mcp_server_registry (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    label TEXT,
    endpoint TEXT NOT NULL,        -- MCP server URL
    transport TEXT DEFAULT 'streamable_http',
    credential_id TEXT,            -- FK → credential_vault.id (nullable)
    enabled BOOLEAN DEFAULT 1,
    tools JSON,                    -- discovered tools
    last_synced_at TEXT,
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY (credential_id) REFERENCES credential_vault(id),
    INDEX idx_mcp_workspace (workspace_id)
);
```

**Why this schema**:
- `credential_id` on `mcp_server_registry` is the bridge. When NULL, the MCP server is public (no auth needed).
- `refresh_token` enables automatic token refresh.
- `expires_at` enables proactive refresh before expiry.
- This implements the Anthropic pattern: "Agent definition declares MCP server URLs → session creation references a vault_id → credentials are matched by URL"

### Step 1.2: MCP Client with Credential Injection

Modify `mcp_registry_service.py` to support credential injection:

```python
async def _list_tools_streamable_http_async(
    *,
    endpoint: str,
    credential: Optional[Dict[str, Any]] = None,  # NEW
    ...
) -> List[Dict[str, Any]]:
    # Build headers from credential
    headers = {}
    if credential:
        token = credential.get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    
    # Pass headers to transport
    async with streamable_http_client_fn(endpoint, headers=headers) as (read_stream, write_stream, _):
        ...
```

The `mcp` Python SDK's `streamable_http_client` supports custom headers. This is the only change needed for credential injection.

### Step 1.3: App → MCP Server Registration

When a user connects an App (e.g., Google Workspace):

1. **OAuth flow** → obtain access_token + refresh_token
2. **Store in credential_vault** → get credential_id
3. **Determine MCP server URL** for this service:
   - Google Workspace: `https://mcp.empyralis.com/google-workspace` (Empyralis-hosted) OR user-provided
   - GitHub: `https://mcp.empyralis.com/github` OR user-provided
4. **Register MCP server** with `credential_id` pointing to the stored credential
5. **Discover tools** from the MCP server → tools are available to agent

**What this replaces**: The entire custom `validate_*` + `_CONNECTOR_CONTRACT_OVERRIDES` + `skills_service.py` tool dispatch for Apps. All that becomes: OAuth → credential → MCP server → tools.

### Step 1.4: Migration Path for Existing Connectors

For the 40+ existing connectors with stub validators:

1. **Keep the vault file** for backward compatibility
2. **Add credential_vault table** alongside it
3. **Build Empyralis-hosted MCP servers** for the top 5-10 services first
4. **For services without an MCP server**: Keep the existing custom API integration as fallback, but register it through the MCP registry API (wrap the custom code as an MCP server internally)

### Step 1.5: Unify the Frontend

The `workstation-sage-connectors-pane.tsx` (300KB) currently renders both AI providers AND app connectors AND channels in one giant pane. This should be split:

- **Sage → Apps tab**: Shows MCP servers (both managed and manual). "Connect" button triggers OAuth wizard for managed apps, or URL input for manual.
- **Sage → Channels tab**: Shows message channels only. No MCP tools. No API keys. Just routing configuration.
- **Sage → Advanced tab**: Raw MCP server URL input + tool approval UI.

---

## Phase 2 — Channel Adapters (Clean Separation)

### Step 2.1: Extract Channels from Connector Catalog

Remove these from `CONNECTOR_CATALOG`:
- `telegram_bot`
- `whatsapp_twilio`
- `discord_bot`
- `slack`
- `wechat_work`
- `apple_messages_business`
- `irc`

Replace with a `CHANNEL_REGISTRY` that only deals with message routing:

```python
CHANNEL_REGISTRY = {
    "telegram_bot": {
        "label": "Telegram Bot",
        "transport": "webhook",
        "gateway_required": False,
        "webhook_route": "/channels/telegram/webhook/{connector_id}",
        "requires": ["bot_token"],
        "outbound": telegram_send_message,
    },
    "whatsapp_twilio": {
        "label": "WhatsApp (Twilio)",
        "transport": "webhook",
        "gateway_required": False,
        "webhook_route": "/channels/whatsapp/twilio/webhook",
        "requires": ["account_sid", "auth_token", "from_number"],
        "outbound": whatsapp_twilio_send_message,
    },
    # ... etc
}
```

### Step 2.2: Standardize Channel Adapter Interface

Every channel adapter must implement:

```python
class ChannelAdapter(Protocol):
    """Standard interface for channel adapters."""
    
    async def receive_event(self, request: Request) -> ChannelEvent:
        """Parse and normalize an inbound event from the platform."""
        ...
    
    async def send_message(self, channel_id: str, message: str, **kwargs) -> None:
        """Send a message back to the channel."""
        ...
    
    async def verify_signature(self, request: Request) -> bool:
        """Verify the platform's request signature."""
        ...
    
    def should_trigger_run(self, event: ChannelEvent) -> bool:
        """Whether this event should trigger an agent run."""
        ...
```

### Step 2.3: Gateway Bridge Architecture

For channels requiring a local Gateway:

```
┌─────────────────────────────────────────────────────┐
│                   CLOUD                              │
│  Cloud Agent ←→ Gateway Protocol Service             │
│                      ↕ WebSocket                      │
├─────────────────────────────────────────────────────┤
│                   LOCAL GATEWAY                       │
│  empyralis-gateway process                           │
│  ├── Telegram bridge (MTProto / browser automation)  │
│  ├── WhatsApp bridge (browser automation / QR)       │
│  ├── iMessage bridge (BlueBubbles API)               │
│  ├── Signal bridge (signald)                         │
│  ├── WeChat bridge (itchat / local client)           │
│  ├── Browser automation (Playwright)                 │
│  ├── Shell executor                                  │
│  └── File system access                              │
└─────────────────────────────────────────────────────┘
```

The Gateway should be:
- A single binary/process (Node.js, as exists today)
- Managed by the cloud → user installs once, cloud pairs with it
- Communicates via a single persistent WebSocket
- Channels are plugins within the Gateway, not separate processes

### Step 2.4: Channel Labels

| Label | Meaning | Implementation |
|-------|---------|---------------|
| **Cloud only** | Channel uses publicly-reachable webhooks/APIs | Webhook route in FastAPI |
| **Gateway required** | Channel needs local device access (iMessage, Signal, WeChat) | Gateway bridge only |
| **Gateway optional** | Channel can work cloud-only (webhook) but personal variant needs Gateway (Telegram personal, WhatsApp personal) | Both paths available |

---

## Phase 3 — First App Migration: Google Workspace

This is the simplest reference implementation to prove the pattern.

### Step 3.1: What Exists Today for Google Workspace

1. **OAuth config**: `connection_oauth_service.py:47` — Full OAuth2 config with Gmail + Calendar scopes, `offline_access`, `prompt: consent select_account`
2. **Validator**: `validate_google_workspace_connector()` — Makes real API calls to verify credentials
3. **Actions**: `browse_drive`, `create_document`, `create_spreadsheet` — Custom Google Drive/Docs API calls in `connectors_actions.py`
4. **Tool exposure**: Via `skills_service.py` tool descriptors (NOT MCP)

### Step 3.2: Target Architecture

```
User clicks "Connect Google Workspace"
  → OAuth flow (connection_oauth_service.py — ALREADY EXISTS)
  → Google returns access_token + refresh_token
  → Store in credential_vault → credential_id = "cred_abc123"
  → Determine MCP server URL: "https://mcp.empyralis.com/google-workspace"
     (or a Google-hosted MCP server URL)
  → Register MCP server:
       upsert_workspace_mcp_server(
         workspace_id=workspace_id,
         server_id="google-workspace",
         endpoint="https://mcp.empyralis.com/google-workspace",
         credential_id="cred_abc123",  # NEW FIELD
         discover_tools=True,
       )
  → MCP client connects with Authorization: Bearer <access_token>
  → Discovers tools: gmail.read, gmail.send, gmail.search,
                     calendar.list, calendar.create,
                     drive.list, drive.read, drive.create,
                     docs.create, sheets.create, etc.
  → Tools appear in agent's available tools
  → Agent can call them at inference time via MCP protocol
```

### Step 3.3: What Needs to Be Built

1. **Google Workspace MCP Server** — Either:
   - (a) Empyralis-hosted: A Python MCP server that wraps Google APIs, hosted on Empyralis infrastructure
   - (b) Google-hosted: If Google provides an MCP server URL for Workspace
   - (c) Proxy pattern: Empyralis acts as an MCP proxy that injects the user's token into Google API calls

   **Recommendation: (c) Proxy pattern.** Build one internal MCP server that:
   - Accepts standard MCP tool calls
   - Resolves the credential from vault using `credential_id`
   - Makes the actual Google API call
   - Returns the result

2. **OAuth → MCP bridge route**:
   ```python
   @router.post("/apps/google-workspace/connect")
   async def connect_google_workspace(
       code: str,
       redirect_uri: str,
       workspace_id: str,
   ):
       # Exchange code for tokens
       tokens = exchange_google_oauth_code(code, redirect_uri)
       
       # Store credential
       cred_id = store_credential(
           workspace_id=workspace_id,
           provider="google_workspace",
           access_token=tokens["access_token"],
           refresh_token=tokens.get("refresh_token"),
           expires_at=tokens.get("expires_at"),
       )
       
       # Register MCP server with credential
       server = await upsert_workspace_mcp_server_async(
           workspace_id=workspace_id,
           server_id="google-workspace",
           label="Google Workspace",
           endpoint=GOOGLE_WORKSPACE_MCP_ENDPOINT,  # Empyralis-hosted
           credential_id=cred_id,
           discover_tools=True,
       )
       
       return {
           "ok": True,
           "server": server,
           "tool_count": len(server.get("tools", [])),
       }
   ```

3. **Add `credential_id` to MCP server schema**:
   - Add field to `mcp_server_registry` JSON schema
   - Pass credential headers when connecting to MCP server
   - Auto-refresh token before expiry

4. **Remove custom Google Workspace code** (after migration verified):
   - Remove `validate_google_workspace_connector` (MCP server handles validation)
   - Remove `browse_google_connector_drive` route
   - Remove `create_google_connector_document` route
   - Remove `create_google_connector_spreadsheet` route
   - Remove Google Workspace from `_CONNECTOR_CONTRACT_OVERRIDES`

### Step 3.4: How the Agent Sees It After Migration

Before (current):
```
Agent sees tools:
  - google_workspace__browse_drive
  - google_workspace__create_document
  - google_workspace__create_spreadsheet
  (3 hand-coded tools with custom dispatch logic)
```

After (migrated):
```
Agent sees tools:
  - mcp:google-workspace:gmail.read
  - mcp:google-workspace:gmail.send
  - mcp:google-workspace:gmail.search
  - mcp:google-workspace:gmail.modify
  - mcp:google-workspace:calendar.list
  - mcp:google-workspace:calendar.create
  - mcp:google-workspace:calendar.update
  - mcp:google-workspace:calendar.delete
  - mcp:google-workspace:drive.list
  - mcp:google-workspace:drive.read
  - mcp:google-workspace:drive.create
  - mcp:google-workspace:docs.create
  - mcp:google-workspace:sheets.create
  (20+ tools, all through MCP protocol, auto-discovered)
```

---

# PART D — IMPLEMENTATION SEQUENCE

## Phase 1 (Week 1-2): Foundation

1. **Create `credential_vault` table** (migration)
2. **Add `credential_id` to MCP server registry** (schema change)
3. **Implement credential injection in MCP client** (`mcp_registry_service.py`)
4. **Add MCP tool approval routes** (expose `approve_mcp_tool` via API)
5. **Build frontend for MCP tool approval** (toggle UI in Advanced tab)

## Phase 2 (Week 3-4): First App Migration

6. **Build Google Workspace MCP proxy server** (internal)
7. **Wire OAuth → credential vault → MCP registration** (bridge route)
8. **Build "Connect Google Workspace" wizard in frontend**
9. **Test end-to-end**: OAuth → token → vault → MCP → tools → agent call
10. **Remove custom Google Workspace code** after verification

## Phase 3 (Week 5-6): Channel Separation

11. **Extract channels from CONNECTOR_CATALOG** into CHANNEL_REGISTRY
12. **Standardize channel adapter interface** (all existing adapters)
13. **Separate channel config from app config in frontend**
14. **Implement channel pairing in database** (replace JSON files)

## Phase 4 (Week 7-8): Scale

15. **Build MCP proxy servers for next 5 services**: GitHub, Slack, Notion, Linear, Microsoft 365
16. **Migrate remaining 35+ connectors** to the MCP pipeline (or remove stub ones)
17. **Add token refresh for OAuth credentials**
18. **Add health monitoring for MCP servers**

---

# PART E — FILE INDEX

Key files referenced in this audit:

| File | Lines | Role |
|------|-------|------|
| `server_modules/runtime_config.py:785` | 260 | CONNECTOR_CATALOG (48 connectors) |
| `server_modules/connectors_actions.py` | 2496 | Connector CRUD, webhooks, validators |
| `server_modules/connectors_core.py` | 750+ | Connector listing, vault operations |
| `server_modules/routes_connectors.py` | 538 | All connector + channel webhook routes |
| `server_modules/mcp_registry_service.py` | 1170 | MCP server registry + tool execution |
| `server_modules/vault_store.py` | 752 | Credential encryption, storage, backup |
| `server_modules/connection_oauth_service.py` | 1121+ | OAuth provider configs + flow helpers |
| `server_modules/skills_service.py` | 3948 | Tool catalog, tool dispatch for connectors |
| `server_modules/skill_registry.py` | 905+ | Skill definition registry, MCP integration |
| `server_modules/agent_channel_router.py` | 2205 | Channel routing, Gateway dispatch |
| `server_modules/gateway_protocol_service.py` | 100+ | Gateway WebSocket protocol |
| `server_modules/channel_routing_models.py` | 62 | ChannelRoutingContext dataclass |
| `server_modules/runtime_models.py` | 500+ | Request models, catalogs |
| `server_modules/connector_validators.py` | ? | Individual connector validators |
| `mcp_server.py` | 100+ | Empyralis own MCP server (vision) |
| `frontend/lib/workspace/workstation-sage-connectors-pane.tsx` | 300KB+ | Main connectors UI |
| `frontend/lib/workspace/workstation-sage-tools-pane.tsx` | 627 | Skills/tools pane |
| `frontend/lib/workspace/workstation-studio-integrations-pane.tsx` | 33 | Studio integrations pane |
| `frontend/lib/workspace/connector-setup-modal-shell.tsx` | 37 | Connector setup modal |
