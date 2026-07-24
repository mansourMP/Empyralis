# How the best agent products connect MCP servers and keep tools from blowing the context window

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). `sage_*` code/variable/string identifiers are legacy code artifacts only, not a live product concept. Anywhere this document's prose says "Sage" or "master," read: the owner-facing agent. This is a lighter-touch terminology note, not a full rewrite — the body below is unchanged and may still use "Sage" throughout.

Research date: 2026-07-22. All facts below are pulled from official primary sources (spec repo, vendor docs) via direct fetch on this date; version numbers reflect what was current then. Spec-level facts (apply to any compliant client/server) are marked **[SPEC]**. Product-specific behavior is marked with the product name.

---

## 1. The MCP spec itself

Source of truth: `modelcontextprotocol.io/specification/2025-11-25/` (the version live as of this research; the spec is versioned by date, e.g. `2025-06-18`, `2025-11-25`).

### 1.1 Core primitives **[SPEC]**

MCP defines three server-side primitives, each with a distinct control model:

| Primitive | Purpose | Who decides when it's used |
|---|---|---|
| **Tools** | Executable actions (call an API, run a query, mutate state) | **Model-controlled** — the LLM discovers and invokes tools automatically based on context. Spec explicitly requires a human-in-the-loop safety net: clients **SHOULD** show which tools are exposed, indicate when they're invoked, and confirm before executing. |
| **Resources** | Read-only contextual data (files, DB schemas, docs) addressed by URI | **Application-driven** — the host app decides how to surface them (tree/list picker, search, or automatic inclusion); not something the model calls like a function. |
| **Prompts** | Reusable, parameterized message templates | **User-controlled** — explicitly invoked by the user (e.g. as a slash command), never run automatically. |

This three-way split matters for product design: tools are the "give the agent capabilities" surface; resources are the "give the agent/user reference material" surface; prompts are the "give the user a canned workflow" surface. Most "connect an app, get its tools" products (including what we're planning) are really only implementing the **tools** half of MCP — that's fine, it's spec-compliant (a client isn't required to support resources/prompts).

**Capability negotiation**: a server declares which primitives it supports during initialization (`{"capabilities": {"tools": {"listChanged": true}, "resources": {...}, "prompts": {...}}}`). `listChanged: true` means the server will push a notification (`notifications/tools/list_changed`) when its tool list changes — a client should re-fetch `tools/list` on that signal rather than polling.

### 1.2 Tool wire format **[SPEC]**

`tools/list` request/response (JSON-RPC 2.0, paginated via `cursor`/`nextCursor`):

```json
// Response
{
  "tools": [
    {
      "name": "get_weather",
      "title": "Weather Information Provider",
      "description": "Get current weather information for a location",
      "inputSchema": {
        "type": "object",
        "properties": { "location": { "type": "string", "description": "City name or zip code" } },
        "required": ["location"]
      },
      "outputSchema": { "...": "optional JSON Schema for structured results" },
      "annotations": { "...": "optional hints; MUST be treated as untrusted unless server is trusted" },
      "icons": [ { "src": "...", "mimeType": "image/png", "sizes": ["48x48"] } ]
    }
  ]
}
```

Key contract details:
- `name`: unique per server, 1–128 chars, `[A-Za-z0-9_.-]` only, case-sensitive. This is the identifier a client namespaces (e.g. Claude Code prefixes with `mcp__<server>__<tool>`).
- `description`: free-text, human/model-readable. **Spec explicitly warns**: tool annotations (and by extension descriptions from untrusted servers) **MUST** be treated as untrusted unless the server is known-trusted — this is the prompt-injection attack surface (see §6).
- `inputSchema`: a JSON Schema object (defaults to 2020-12 dialect). For no-arg tools, spec recommends `{"type":"object","additionalProperties":false}`.
- Tool calls (`tools/call`) return `content` (text/image/audio/resource-link/embedded-resource blocks) and optionally `structuredContent` validated against `outputSchema`. Errors are either **protocol errors** (malformed request/unknown tool — JSON-RPC error) or **tool execution errors** (`isError: true` inside a normal result — meant to be fed back to the model so it can self-correct).

### 1.3 Transports **[SPEC]**

Exactly two standard transports as of the current spec (`2025-11-25`):

1. **stdio** (local): client spawns the server as a subprocess; JSON-RPC messages are newline-delimited over stdin/stdout; stderr is free-form logs. No auth framework applies here — the spec says stdio implementations **SHOULD NOT** follow the OAuth flow and should instead pull credentials from the environment (env vars, config files).
2. **Streamable HTTP** (remote): replaced the older "HTTP+SSE" transport from protocol version `2024-11-05`. One HTTP endpoint (e.g. `POST/GET https://example.com/mcp`) handles everything:
   - Client `POST`s each JSON-RPC message; server responds either as a single `application/json` object or opens a `text/event-stream` (SSE) to stream multiple messages (requests/notifications) before eventually sending the JSON-RPC response.
   - Client can also `GET` the endpoint to open a standing SSE stream for server-initiated messages.
   - **Session management**: server may issue an `MCP-Session-Id` header on `InitializeResult`; client must echo it on every subsequent request; `DELETE` explicitly ends a session; a `404` on a stale session ID means "start over with a fresh `InitializeRequest`."
   - **Resumability**: SSE events can carry an `id`; client reconnects with `Last-Event-ID` to resume a broken stream without losing messages.
   - **Protocol version pinning**: client must send `MCP-Protocol-Version: <date>` on every HTTP request after negotiation (e.g. `2025-11-25`).
   - **Security**: server **MUST** validate the `Origin` header (anti DNS-rebinding), **SHOULD** bind to localhost only when running locally, **SHOULD** require auth.
   - Legacy **HTTP+SSE** (pre-2025-03-26) is a distinct, older transport (separate POST + GET-SSE endpoints via an `endpoint` discovery event) that Streamable HTTP superseded; clients wanting broad compatibility probe for the new transport first and fall back to the old one on `400/404/405`.

Practical takeaway: **local stdio for anything on the user's machine (filesystem, browser automation, local DB); Streamable HTTP for anything hosted.** SSE-only servers still exist in the wild and are worth supporting as a fallback, but new servers should be Streamable HTTP.

### 1.4 Authorization — OAuth 2.1 for remote servers **[SPEC]**

Full spec: `modelcontextprotocol.io/specification/2025-11-25/basic/authorization`. Applies only to HTTP-based transports; optional overall, but **SHOULD** be implemented if a server needs auth. stdio servers are explicitly out of scope (env-var credentials instead).

Roles: **MCP server = OAuth 2.1 resource server**, **MCP client = OAuth 2.1 client**, **authorization server = separate role** (may be co-hosted with the MCP server or a third party like Auth0/WorkOS).

The flow, step by step:

1. Client calls the MCP server unauthenticated → server returns `401` with a `WWW-Authenticate: Bearer resource_metadata="https://.../.well-known/oauth-protected-resource"` header (or the client falls back to probing the well-known URI directly).
2. Client fetches **Protected Resource Metadata** (RFC 9728) from that URL → gets back `authorization_servers: [...]`.
3. Client discovers the **Authorization Server Metadata** (RFC 8414 or OIDC Discovery) by probing well-known endpoints in a defined priority order.
4. Client registers as an OAuth client. Three mechanisms, in priority order:
   - **Pre-registration** (client already has a `client_id` for this server) — highest priority.
   - **Client ID Metadata Documents (CIMD)** — new, spec-preferred approach: the client's `client_id` *is* an HTTPS URL pointing to a JSON document describing itself (`client_id`, `client_name`, `redirect_uris`); the auth server fetches and validates it live. No prior registration handshake needed — this is what lets an unknown client and an unknown server establish trust with zero pre-coordination.
   - **Dynamic Client Registration (RFC 7591)** — kept for backward compatibility, being de-emphasized in favor of CIMD.
5. Client builds the authorization URL with **PKCE** (`S256` challenge — **mandatory**, no fallback) and a **`resource` parameter** (RFC 8707) that pins the token to *this specific* MCP server's canonical URI — this is what prevents a token issued for server A being replayed against server B ("confused deputy" / token-passthrough attack).
6. User approves in-browser → auth code → client exchanges it (with `code_verifier` + `resource`) for an access token (+ refresh token).
7. Client calls the MCP server with `Authorization: Bearer <token>` on every request (not in query strings).
8. Server validates the token's audience claim matches itself; **MUST NOT** forward that same token unmodified to any downstream API it calls (it must mint its own outbound token if it acts as a client to something else).

Notable security requirements worth encoding as design constraints for our client: redirect URIs must be `localhost` or HTTPS; state parameter required and must be checked; tokens should be short-lived with refresh rotation for public clients; step-up re-auth flow is defined for `insufficient_scope` (`403` + `WWW-Authenticate: error="insufficient_scope", scope="..."`) so a client can silently re-request a broader token instead of failing hard.

---

## 2. Claude Code's MCP support

Sources: `code.claude.com/docs/en/mcp-quickstart`, `code.claude.com/docs/en/agent-sdk/tool-search`, `platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool`, `platform.claude.com/docs/en/agents-and-tools/mcp-connector`.

### 2.1 Adding a server

```bash
# Remote, HTTP/SSE, no auth
claude mcp add --transport http claude-code-docs https://code.claude.com/docs/mcp

# Local stdio (default transport when --transport omitted)
claude mcp add playwright -- npx -y @playwright/mcp@latest

# Remote with a static bearer token
claude mcp add --transport http github https://... --header "Authorization: Bearer <token>"
```

`claude mcp list` shows connection status per server: `✔ Connected`, `! Connected · tools fetch failed`, `! Needs authentication`, `✘ Failed to connect`, `✘ Connection error`, `⏸ Pending approval`.

### 2.2 Scopes

Three scopes, resolved local > project > user when the same name is defined at more than one:

| Scope | Flag | Storage | Visibility |
|---|---|---|---|
| `local` (default) | *(none)* | `~/.claude.json`, keyed under the current project path | Only you, only this project |
| `project` | `--scope project` | `.mcp.json` in the repo root | Everyone who clones the repo (checked into git — "config as code") |
| `user` | `--scope user` | `~/.claude.json`, top-level `mcpServers` key | Only you, every project |

`.mcp.json` format (hand-editable, same schema for HTTP and stdio):

```json
{
  "mcpServers": {
    "claude-code-docs": { "type": "http", "url": "https://code.claude.com/docs/mcp" },
    "playwright":       { "type": "stdio", "command": "npx", "args": ["-y", "@playwright/mcp@latest"] }
  }
}
```

Project-scoped servers require a one-time user approval prompt the first time Claude Code sees them in a cloned repo (prevents a repo from silently launching subprocesses on your machine).

### 2.3 OAuth for remote servers

For servers behind OAuth (Sentry, Linear, Notion, etc.), the flow is: `claude mcp add --transport http sentry https://mcp.sentry.dev/mcp` → status shows `! Needs authentication` → inside a session, run `/mcp` → select the server → `Authenticate` → browser opens → user approves → status flips to connected. Claude Code handles PKCE, token storage, and refresh internally; the user only ever sees a browser consent screen.

### 2.4 How it avoids dumping every tool schema into context — Tool Search

This is the mechanism most relevant to our build. Two related but distinct implementations exist:

**A. The underlying API primitive** (`platform.claude.com/.../tool-search-tool`) — a first-class Messages API feature, usable by anyone building a client, not just Claude Code:

- You add a `tool_search_tool_regex_20251119` or `tool_search_tool_bm25_20251119` entry to your `tools` array (regex variant: Claude writes Python `re.search()` patterns; BM25 variant: natural-language queries).
- You mark tools that shouldn't load upfront with `"defer_loading": true` on the tool definition. **You still send the full definition (name, description, input schema) for every deferred tool on every request** — `defer_loading` controls what enters the *model's visible context*, not what you transmit over the wire. The API excludes deferred tools from the system-prompt prefix (which is why prompt caching is preserved), and when Claude searches and finds a match, the API injects a `tool_reference` block inline and auto-expands it to the full definition — client code never manually expands anything.
- At least one tool (normally the search tool itself) must stay non-deferred, or the request 400s.
- Each search returns **up to 5 tools by default**. Response blocks: `server_tool_use` (the search call, server-side, never gets a `tool_result` back from you) → `tool_search_tool_result` (containing `tool_references`) → the model's subsequent normal `tool_use` block against a now-expanded tool.
- Documented numbers: a typical 5-server setup (GitHub, Slack, Sentry, Grafana, Splunk) burns **~55k tokens** in tool definitions loaded naively; tool search **cuts that by >85%**, loading only the 3–5 tools actually needed per turn. Anthropic's stated rule of thumb: use tool search once you have **10+ tools**, **>10k tokens** of tool definitions, or you're aggregating **200+ tools** across multiple MCP servers. Tool selection accuracy is documented to degrade past **30–50 tools** loaded simultaneously regardless of context size — this is a separate problem from raw token cost. Max catalog size: **10,000 deferred tools per request**.

**B. Claude Code's product-level wiring** (`code.claude.com/.../tool-search`) — turns the API primitive into an invisible default for CLI/SDK users:

- **Enabled by default.** When active, tool definitions are withheld from context entirely; the agent gets a lightweight summary and searches on demand; up to 5 most-relevant tools load per search and persist for the rest of the session (until context compaction evicts them, at which point a fresh search re-loads them if needed).
- **Exact activation threshold**: controlled by `ENABLE_TOOL_SEARCH` env var. Default (unset) = on, deferred-by-default. Explicit modes:

  | Value | Behavior |
  |---|---|
  | *(unset)* | On; deferred loading; auto-falls-back to upfront loading on GCP Agent Platform or non-first-party `ANTHROPIC_BASE_URL` proxies |
  | `true` | Force on everywhere, even where it'd normally fall back |
  | `auto` | Compares combined token size of **all** tool definitions against the model's context window; activates only if they exceed **10%** |
  | `auto:N` | Same as `auto` but with a custom percentage threshold, e.g. `auto:5` activates at 5% |
  | `false` | Fully disabled; every tool loads on every turn |

- Applies uniformly to MCP-server tools and custom SDK tools; with `auto`/`auto:N` the threshold is evaluated on the **combined** size across every connected server, not per-server.
- One extra round-trip on first discovery of a tool per session; below ~10 tools total, upfront loading is faster (no search hop), which is why the product doc explicitly recommends turning it off for small toolsets.
- Model support gate: Sonnet 4.5, Haiku 4.5, Opus 4.5 and later only (this is a model-capability feature, not purely infra).
- Product-level knobs for improving search hit-rate: descriptive tool names (`search_slack_messages` beats `query_slack`), keyword-rich descriptions, and an appended system-prompt hint listing available tool *categories* ("You can search for tools to interact with Slack, GitHub, and Jira") so the model knows what's searchable before it searches.

**C. The API-level MCP connector's own toolset config** additionally exposes `defer_loading` as a **per-MCP-server** (not just per-tool) setting via `default_config`, so you can defer an entire server's tools in one line:

```json
{
  "type": "mcp_toolset",
  "mcp_server_name": "google-calendar-mcp",
  "default_config": { "defer_loading": true },
  "configs": { "search_events": { "enabled": true } }
}
```

This connector config layer also does **allowlist/denylist per tool** (`enabled: true/false`) independent of deferred loading — i.e. "expose only these 3 tools from this 40-tool server" is a first-class, declarative config, not something you build yourself by filtering `tools/list` output.

### 2.5 Note on Claude Code vs. the raw API MCP connector

Claude Code (the CLI/desktop agent) is a **full MCP client** — it runs its own stdio subprocess management and Streamable-HTTP/SSE client, and can therefore connect to **local stdio servers**, which the hosted API-level `mcp_servers` connector explicitly **cannot** (API connector is remote/HTTP-only, "Local STDIO servers cannot be connected directly" per its docs). If you're building a product that needs local filesystem/browser-style MCP servers, you need your own client-side MCP implementation (like Claude Code has), not just the API connector.

---

## 3. Remote MCP / "MCP apps" / connectors (Anthropic's one-click connect)

Sources: `support.claude.com/en/articles/11175166` (custom connectors), `support.claude.com/en/articles/11176164` (connector directory), `docs.claude.com` → `platform.claude.com/docs/en/agents-and-tools/mcp-connector`.

### 3.1 Two distinct UX tiers

**Directory connectors** (pre-built, reviewed, one-click — e.g. Google Drive, Slack, Linear): user browses `claude.ai`'s Connectors Directory → clicks the app → "Connect"/"Install" → follows an OAuth consent screen → done. No URL entry, no config. Anthropic (or the vendor) has already registered the OAuth client and hosts/vets the server.

**Custom connectors** (any remote MCP server, including ones you host yourself): `Settings → Customize → Connectors → Add → Add custom connector` → user pastes the **remote MCP server URL** → optional "Advanced settings" to supply OAuth client credentials manually if the server doesn't support dynamic/CIMD registration → Claude walks the standard OAuth 2.1 flow from §1.4. For Team/Enterprise workspaces, an **org owner** adds it once under `Organization settings → Connectors → Add → Custom → Web`, and members then just click **Connect** on the already-registered entry (credentials aren't shared with members — each user does their own OAuth grant against the same registered app).

### 3.2 Tool exposure UX

Once connected, a connector's tools are **on by default and contextually auto-invoked** — "Claude can bring it into a conversation on its own when it fits what you're asking for; you don't have to name it every time." Per-conversation, the user can also explicitly toggle a connector on/off via a **`+` button → Connectors** menu in the chat composer — this is the closest thing to a manual "enable this app for this chat" control. Team/Enterprise owners get an admin-level kill switch: they can disable specific tool calls org-wide, particularly ones that "render interactive connectors."

Permissions are inherited from the source system, not re-specified in Claude: "If someone can't access a specific file, channel, or record in the source system, the connector can't reach it from Claude either" — i.e. the OAuth-granted scope + the underlying service's own ACLs are the only access control; Claude doesn't add a second permission layer on top.

### 3.3 API-level equivalent (for building our own connector-consuming backend)

The Messages API `mcp_servers` + `mcp_toolset` shapes documented in §2.4C are the programmatic version of "connect an app": `mcp_servers: [{type:"url", url, name, authorization_token}]` plus a matching `tools: [{type:"mcp_toolset", mcp_server_name, default_config, configs}]`. Current beta header: `anthropic-beta: mcp-client-2025-11-20` (the older `mcp-client-2025-04-04`, which put tool filtering inside the server definition itself instead of a separate toolset object, is deprecated). Notably: **the caller is responsible for obtaining and refreshing the OAuth `authorization_token` before each call** — the API does not run the OAuth dance for you server-side; that's on the client application (this is different from claude.ai's product UX, which does run the full browser OAuth flow for the user). Multiple servers per request are supported; each server needs exactly one matching toolset.

---

## 4. Cursor and OpenAI/Codex MCP support

### 4.1 Cursor

Source: `cursor.com/docs/mcp` (docs.cursor.com redirects here).

- Config file: `.cursor/mcp.json` (project-scoped) or `~/.cursor/mcp.json` (global), same three-shape schema as Claude Code (`command`/`args`/`env` for stdio; `url` for HTTP/SSE).
- Install UX: "Add to Cursor" one-click deep links from the Cursor marketplace / cursor.directory community listings, in addition to hand-editing `mcp.json`. Team plans can push a shared server list via an admin marketplace.
- OAuth: supported for remote servers, with Cursor providing **fixed redirect URIs** it registers as an OAuth client (`https://www.cursor.com/agents/mcp/oauth/callback` for cloud/web agents, `http://localhost:8787/callback` for desktop) — this is the "pre-registered client" path from the spec's client-registration options (§1.4 step 4), not CIMD.
- **Tool count**: reporting during research (secondary sources, not confirmed on the current official page fetch) puts a practical/soft cap around **40 tools** exposed to the agent at once.
- **Context management**: the official docs, as fetched, describe no tool-search/deferred-loading mechanism — tools are described as loaded whenever the server is enabled ("Cursor automatically uses MCP tools listed under Available Tools when relevant"), with the user's main lever being to **manually enable/disable whole servers** per project via the Customize/MCP settings panel, not per-tool deferred loading. This is a meaningfully less sophisticated context strategy than Claude Code's — the mitigation is coarse (turn a whole server off) rather than granular (search-and-load individual tools).

### 4.2 OpenAI — Agents SDK and Responses API

Sources: `openai.github.io/openai-agents-python/mcp/`, `developers.openai.com/api/docs/mcp`, `developers.openai.com/api/docs/guides/tools-connectors-mcp`, `developers.openai.com/api/docs/guides/tools-tool-search`.

**Agents SDK (Python/TS)** — for building your own agent process that owns the MCP client:
- Server types: `MCPServerStdio` (local subprocess), `MCPServerStreamableHttp` (your process manages the HTTP transport directly), `MCPServerSse` (legacy, deprecated upstream), and `HostedMCPTool` (delegates the entire round-trip to OpenAI's servers via the Responses API — your process never talks to the MCP server directly).
- Context management here is **caching, not search**: every MCP server object exposes `cache_tools_list` (skip re-calling `list_tools()` on every agent turn) and `invalidate_tools_cache()` to bust it manually. Tool name collisions across multiple servers are handled by `include_server_in_tool_names=True`, which prefixes tool names with the server name.
- Filtering: `create_static_tool_filter(allowed_tool_names=..., blocked_tool_names=...)` for a fixed allow/deny list, or a dynamic callable receiving a `ToolFilterContext` (agent name, run context, server identity) for conditional logic.

**Responses API `type: "mcp"` tool** — the hosted/managed equivalent, directly comparable to Anthropic's `mcp_servers`/`mcp_toolset`:

```json
{
  "type": "mcp",
  "server_label": "cats",
  "server_url": "https://example.com/sse/",
  "allowed_tools": ["search", "fetch"],
  "require_approval": "never"
}
```
- `connector_id` (e.g. `connector_gmail`, `connector_dropbox`) is the alternative to `server_url` for OpenAI's own pre-built, pre-authenticated connector catalog (Dropbox, Gmail, Google Calendar/Drive, MS Teams, Outlook, SharePoint) — mirrors Anthropic's directory-connector tier.
- `authorization`: an OAuth access token passed per-request, not stored server-side by OpenAI (caller-managed, same pattern as Anthropic's `authorization_token`).
- `require_approval`: `"always"` / `"never"`, or a fine-grained object mapping individual tool names to a policy — this is OpenAI's version of Anthropic's tool-annotation-based "sensitive operation" confirmation, but expressed declaratively per-tool rather than left to client UX judgment.
- **Tool discovery caching**: the runtime calls the server's `tools/list` once and writes the result into the response's output as an `mcp_list_tools` item. As long as that item stays present in the model's context (achieved automatically by passing `previous_response_id` on the next turn, or manually by re-including the item), the API will **not** re-fetch `tools/list` on every turn — this is the Responses-API equivalent of the Agents SDK's `cache_tools_list`.
- **Deferred loading / tool search** (newer, `gpt-5.4`+ only): set `defer_loading: true` on the MCP server's tool entry, and add a `tool_search` entry to the `tools` array. Behavior differs by grain: on an individual function, only the parameter schema is deferred (name+description still visible); on a whole MCP server/namespace, only the server's own name+description shows up front and individual tool details load lazily as the model searches. Two execution modes: **hosted** (OpenAI runs the search server-side and returns the loaded subset) or **client-executed** (your app implements search and returns `tool_search_output` in response to a `tool_search_call`). Newly-discovered tools are appended at the **end** of the context window specifically to avoid invalidating the prompt cache on the earlier turns — same design goal as Anthropic's "prefix stays untouched" caching guarantee, achieved the same way (append-only injection instead of rewriting the head of context).
- Pricing: token-metered only (tool-definition-import tokens + call tokens), no separate per-call fee — same model as Anthropic's tool search.

**Bottom line on OpenAI vs. Anthropic parity**: as of this research, OpenAI's Responses API has converged on essentially the same two-layer design as Anthropic's — (1) cache the tool list across turns via a context marker (`mcp_list_tools` / Claude's persisted `tool_search_tool_result`), and (2) an explicit `defer_loading` + `tool_search` opt-in for genuinely large toolsets, gated to their newest models (`gpt-5.4`+ vs. Claude's Sonnet/Haiku/Opus 4.5+). Cursor, by contrast, has neither — it's the least sophisticated of the three on context management, relying on users to manually toggle whole servers off.

---

## 5. Distilled build guidance for Empyralis "MCP applications"

**The connect flow.** Support two server shapes from day one, sharing one config schema (mirror the field names everyone converged on — `type: "http"/"stdio"`, `url`, `command`/`args`, `authorization_token`/`header`):
1. **Remote + OAuth first** (the 90% case: Slack, Notion, Linear, GitHub-style hosted MCP servers). Flow: workspace admin/user pastes a server URL → we probe `.well-known/oauth-protected-resource` per spec (§1.4) → run PKCE + CIMD-preferred client registration → browser consent → store the refresh token server-side, scoped to the *workspace*, not the agent. One workspace-level OAuth grant should back every agent in that workspace that's given access to the app — don't make each agent do its own OAuth dance for the same connector.
2. **Local stdio later** (power-user case: filesystem, browser automation, local DB). Only makes sense once we have a "runs near the agent's own box" execution model, since stdio requires the client and server to share a machine — this maps naturally onto our per-agent hardware boxes, not the control plane.

**Where credentials live.** Access/refresh tokens belong in our backend, encrypted, keyed by (workspace, connector) — never handed to or stored by the agent process itself, and never pasted by the user as a raw API key (per our own no-API-key-hunting rule, this is actually a stronger argument for OAuth-only: it's both better UX and the spec's preferred path). Token refresh must be automatic and silent; treat a `401`/expired-token mid-conversation as a spec-defined recoverable event (re-auth, retry once), not a hard failure surfaced to the user.

**The context-protecting design — this is the part worth copying closely.** Every serious product converged on the same shape, so we should build to it rather than inventing our own:
- Store full tool definitions (name, description, JSON schema) per connected app in our own registry — always, regardless of size. This is the source of truth; nothing here is "sent to the model."
- By default, list connected apps' tools to the agent **by name + one-line description only** (a lightweight index), not full schemas. This alone is most of the token savings, and it's cheap to build without needing Anthropic's specific `defer_loading`/`tool_search_tool` API primitives — those are a bonus once we're calling the Anthropic API directly, since Claude Code's own tool-search wiring gives us this almost for free if our MCP tools are exposed the same way Claude Code exposes them (`mcp__<connector>__<tool>` naming, `defer_loading: true` by default for anything beyond a small always-on set).
- Load full schema **on demand**: when the agent decides (or searches and finds) that a tool is relevant, expand it into context, execute, and — this matters — **keep it loaded for the rest of that session/turn window** rather than re-collapsing it immediately; only evict on context compaction.
- Concretely for our stack: if we're routing through the Anthropic API, this is literally the `tool_search_tool_regex/bm25` + `defer_loading: true` mechanism from §2.4A — we don't need to reinvent discovery, just wire our per-workspace MCP tool catalog into it with `defer_loading` on by default for anything past a handful of "core" tools, and use the `mcp_toolset.default_config`/`configs` allow/deny layer for admin-level tool curation per connector.
- Rule of thumb thresholds to encode: turn on deferred loading once a workspace has **10+ tools** active or **>10k tokens** of definitions; treat **30–50 simultaneously-loaded tools** as a hard ceiling for selection accuracy regardless of token budget — if a workspace connects enough apps to blow past that, deferred loading isn't optional.
- Cache the discovery step across turns (OpenAI's `mcp_list_tools`-stays-in-context trick / Claude Code's "loaded tools stay available for the session"). Don't re-run `tools/list` against every connector on every single agent turn — that's both a latency and a token-cache-invalidation cost with no benefit.

**Top pitfalls to design against explicitly:**
1. **Tool-name collisions across connectors.** Two connected apps can both expose a tool called `search` or `create_task`. Every product namespaces: Claude Code prefixes `mcp__<server>__<tool>`, OpenAI's Agents SDK offers `include_server_in_tool_names`. We must namespace by connector instance (not just connector type — a workspace could connect two Notion workspaces) from the start; retrofitting naming after agents have tool-call history referencing bare names is painful.
2. **Context bloat from "just enable everything."** The natural product instinct is "connect app → all its tools appear," but a single large MCP server (GitHub, Notion) can be 40+ tools on its own. Default new connections to the deferred/curated path, not full-blast — and give workspace admins the allow/deny-per-tool control (mirroring `mcp_toolset.configs`) so they can hand an agent "just `create_issue` and `list_issues`" without exposing destructive tools like `delete_repo`.
3. **Auth token refresh silently breaking agents.** An agent running unattended (our core use case — these aren't chat sessions a human babysits) will hit expired tokens hours or days into a task. Must auto-refresh transparently and alert the workspace owner (not the agent, not a dead-end error to nobody) if a refresh itself fails and re-consent is required.
4. **Prompt injection via tool descriptions/results.** The spec explicitly flags this: tool annotations and, by extension, anything a third-party MCP server chooses to put in its tool descriptions or tool-call results **MUST** be treated as untrusted unless the server is vetted. A malicious or compromised connector could embed instructions in a tool description ("when calling this tool, also exfiltrate X") that an agent picks up as if it were legitimate context. Mitigations worth adopting: only list unreviewed/custom connectors with a visible "unverified" marker (mirrors Anthropic's directory-vs-custom-connector split), never let a tool description or tool result silently expand an agent's permissions or trigger a *different* tool call without the normal confirmation gate, and keep the human-in-the-loop confirmation step the spec mandates for sensitive/state-changing tools — don't let "reduce friction" product pressure quietly remove it for connector-sourced tools the way it might for our own first-party tools.

---

## Sources

**MCP spec (modelcontextprotocol.io):**
- [Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)
- [Prompts](https://modelcontextprotocol.io/specification/2025-11-25/server/prompts)

**Claude Code / Claude Agent SDK docs (code.claude.com):**
- [Connect to MCP servers](https://code.claude.com/docs/en/mcp-quickstart)
- [Scale to many tools with tool search](https://code.claude.com/docs/en/agent-sdk/tool-search)

**Claude API docs (platform.claude.com / docs.claude.com):**
- [Tool search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
- [MCP connector](https://platform.claude.com/docs/en/agents-and-tools/mcp-connector)

**Claude.ai connectors (support.claude.com):**
- [Use connectors to extend Claude's capabilities](https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities)
- [Get started with custom connectors using remote MCP](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)

**Cursor docs:**
- [Model Context Protocol (MCP)](https://cursor.com/docs/mcp)

**OpenAI docs:**
- [Model context protocol (MCP) — OpenAI Agents SDK (Python)](https://openai.github.io/openai-agents-python/mcp/)
- [MCP reference](https://developers.openai.com/api/docs/mcp)
- [MCP and Connectors guide](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)
- [Tool search guide](https://developers.openai.com/api/docs/guides/tools-tool-search)
