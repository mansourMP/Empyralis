# Audit: can an outside agent drive Empyralis via MCP today?

**Date:** 2026-07-22
**Method:** direct code reading (file:line cited throughout), `git log`/`git show` for history, running the real test suite, and — where the prior audit stopped at reading code — actually booting `mcp_server.py`'s mounted FastAPI app on `127.0.0.1` with a real `uvicorn` server and driving it with the real `mcp` Python SDK client (the same client library Claude Code uses), over the real streamable-HTTP transport, against the real local dev Postgres. Nothing here touches prod (`empyralis.ai`); the probe script and its output are reproduced below so every claim is checkable.

**This doc corrects two of the prior audit's claims.** `docs/design/mcp-current-state.md` §1 (2026-07-22, same day) asserts the reverse-direction MCP server "exposes 9 tools (`list_spaces`, `get_space_status`, `get_recent_alerts`, `ask_space`, plus 4 write tools ..., plus chat/memory tools)" and cites `docs/MCP_CLIENT_SETUP.md` for that list, and separately says the file is "267 lines per `docs/PLATFORM-MAP.md:340`." Neither is true of current `main`: the vision tools (`list_spaces` etc.) and the memory tools were **deleted** in commits `e03ce2a7d` ("cleanup: remove dead vision-monitor tools") and the "Fix 6" pass respectively (see §1 below, and `mcp_server.py:387-392`'s own comment explaining the memory-tool removal), and the file is 667 lines, not 267. `docs/MCP_CLIENT_SETUP.md` (the doc the prior audit trusted) is itself stale on this point. The "58/58 tests pass" claim is real and reproduced below — but its scope is much narrower than it sounds (see §2.3).

---

## 1. What does it actually expose?

`mcp_server.py` (repo root, 667 lines) is Empyralis acting as an MCP **server** — the reverse of the "Empyralis connects out to 72 SaaS MCP endpoints" direction (`server_modules/mcp_registry_service.py`, covered by the prior audit, unaffected by this one). It's built with the official `mcp` SDK's `FastMCP` (`mcp==1.26.0`, confirmed installed) and mounted into the real app at **`server.py:338`**: `mount_empyralist_mcp(app)`.

### 1.1 Live tool inventory (verified two ways: static read of `mcp_server.py:71-87`, and a live `tools/list` call against the actual mounted app — see §2.2)

13 tools, all prefixed `empyralis_*`. None of the four "vision" tools or three "memory" tools the prior audit and `docs/MCP_CLIENT_SETUP.md` describe still exist.

| Tool | Kind | Backing call | Annotations |
|---|---|---|---|
| `empyralis_list_projects` | read | `projects_repository.list_projects` | readOnly, non-destructive |
| `empyralis_list_agents` | read | `fleet_list_agents` + channel/connector enrichment | readOnly, non-destructive |
| `empyralis_get_agent_activity` | read | `fleet_get_agent_activity` | readOnly, non-destructive |
| `empyralis_get_agent_conversations` | read | `deployed_agent_service.list_deployed_agent_conversations` | readOnly, non-destructive |
| `empyralis_chat` | chat | full turn via `direct_chat_operator_binding_service` | non-destructive |
| `empyralis_create_project` | write | `projects_repository.create_project` | non-destructive |
| `empyralis_create_agent` | write | `fleet_create_agent` + optional project assignment | non-destructive |
| `empyralis_configure_agent` | write | `fleet_configure_agent` | **destructive** |
| `empyralis_message_agent` | write | `fleet_message_agent` (drops into agent's fleet inbox) | non-destructive |
| `empyralis_assign_channel_bot` | write | binds a Telegram/Discord bot token to an agent | **destructive** |
| `empyralis_release_channel_bot` | write | unbinds a channel bot | **destructive** |
| `empyralis_connect_connector` | write | `connection_oauth_service.start_oauth` → returns an authorization URL | non-destructive |
| `empyralis_trigger_test_turn` | write | `deployed_agent_test_turn_service.execute_test_turn` | non-destructive |

Source: `mcp_server.py:295-610` (each `@empyralist_mcp.tool(...)` definition), cross-checked against `EMPYRALIST_MCP_TOOLS` at `mcp_server.py:71-87` and the live `tools/list` response captured in §2.2.

**Is it the whole platform?** No — it's a real but partial slice. It covers projects, the agent fleet CRUD surface, channel-bot binding, connector OAuth kickoff, one chat entry point, and a test-turn trigger. It does **not** expose: memory read/write (explicitly removed, `mcp_server.py:387-392`, because the real `agent_memory_tools` signature is agent-scoped and the old code passed workspace-only args and crashed every call — the tools were deleted rather than shipped broken), hardware/gateway management, billing, workspace settings, or any of the "vision"/space-monitoring tools the old docs still describe.

### 1.2 Auth: how a key maps to workspace scope

Two paths, both resolved through `_resolve_workspace()` (`mcp_server.py:118-178`):

- **Legacy per-workspace bearer key** (the one that actually works today — see §2). Created via the dashboard's workspace Settings page → "MCP API keys" section (`frontend/app/(account)/w/[workspaceId]/settings/page.tsx:216-260`, a real, wired UI — "Allow writes" checkbox at line 246, key shown once at line 224-232) → `POST /api/connections/mcp-keys` (`server_modules/routes_connections.py:864-891`), gated `_workspace_scope(current_user, workspace_id, minimum_role="owner")` at line 883 — **only a workspace owner can mint a key**. The key (`empyralis_mcp_<32B urlsafe>`) is SHA-256-hashed and stored in a flat JSON file (`server_modules/mcp_server_auth.py:29-32`, default `~/.empyralis/state/runtime/mcp_api_keys.json`, overridable via `EMPYRALIS_MCP_KEYS_FILE`) — this is a bespoke store, not the same credential vault every other connector uses, but functionally fine for a single-backend-instance deployment. `resolve_workspace_from_api_key()` (`mcp_server_auth.py:189-212`) hashes the presented bearer token and looks up `{workspace_id, writes_enabled}`; a revoked or unknown key returns `None`.
- **OAuth 2.1** (`server_modules/mcp_oauth_provider.py`, opt-in via `EMPYRALIS_MCP_OAUTH_ENABLED=true`, off by default — `mcp_server.py:93-98`). Implements RFC 7591 (DCR), 8414/9728 (metadata), PKCE. `EmpyralisOAuthProvider.load_access_token` tries the OAuth token tables first, then falls back to the same legacy-key resolver, so both paths converge on the same `{workspace_id, writes_enabled}` shape.

**Workspace is always resolved from the key/token, never from a tool argument** — no tool in §1.1 accepts a `workspace_id` parameter. Cross-workspace isolation is unit-tested (`test_mcp_server.py::MCPCrossWorkspaceIsolationTests`) and was empirically re-verified in §2.2 below.

Write tools require **two independent gates**, both must be true: the per-key `writes_enabled` flag (owner sets it at key-creation time) AND the server-wide `EMPYRALIS_MCP_WRITE_ENABLED=true` env var (`mcp_server.py:89-91, 275-286`) — an emergency global off-switch that overrides every key. Verified live in §2.2.

---

## 2. Would it actually work from Claude Code / Claude / Cursor today?

**Verdict: no — not at the documented URL, and not reliably even at the URL that actually answers.** This was proven empirically, not just read from code.

### 2.1 The bug: `/mcp` is not `/mcp`

`mount_empyralist_mcp()` (`mcp_server.py:616-658`) does, in the default (non-OAuth) configuration that ships today:

```python
sub_app = empyralist_mcp.streamable_http_app()   # mcp_server.py:619
if oauth_provider is None:
    app.mount(EMPYRALIST_MCP_PATH, sub_app)       # mcp_server.py:622 — EMPYRALIST_MCP_PATH = "/mcp" (line 68)
    return
```

`empyralist_mcp.streamable_http_app()` is the SDK's own method (`FastMCP.streamable_http_app`, `.venv-v2/…/mcp/server/fastmcp/server.py:950-1044`). It builds its **own** Starlette app with a route registered at `self.settings.streamable_http_path`, whose default — never overridden by `_build_mcp_server()` at `mcp_server.py:211-255` — is also `"/mcp"` (SDK default, `mcp/server/fastmcp/settings.py:166`).

So the real path structure is: an outer `Mount("/mcp", app=sub_app)`, and *inside* `sub_app`, a `Route("/mcp", …)`. The two `"/mcp"` strings are coincidentally identical but independently sourced (one's a module constant in `mcp_server.py`, the other's an SDK default), and nobody composes them — the effective reachable path is **`/mcp/mcp`**, not `/mcp`. This is the same double-mount shape in the OAuth branch too (`mcp_server.py:643-658`: `protocol_app = Starlette(routes=protocol_routes, …)` is mounted at `EMPYRALIST_MCP_PATH` = `/mcp` again, while `protocol_routes` were filtered by `r.path == protocol_path` = `/mcp`).

**Empirically confirmed** with a local, read-only probe (`uvicorn` serving the exact, unmodified `mount_empyralist_mcp(app)` call on `127.0.0.1`, no code changes):

```
outer route: /mcp
   inner route: /mcp

POST '/mcp'      -> 307  location=http://127.0.0.1:PORT/mcp/
POST '/mcp/'     -> 404  body='Not Found'
POST '/mcp/mcp'  -> 200  body='event: message\r\ndata: {"jsonrpc":"2.0", ...}'
POST '/mcp/mcp/' -> 307  location=http://127.0.0.1:PORT/mcp/mcp
```

**This is not new.** `git show df30aed76:mcp_server.py` — the very first commit that mounted an MCP server in this repo — already has `EMPYRALIST_MCP_PATH = "/mcp"` and the identical one-line `app.mount(EMPYRALIST_MCP_PATH, empyralist_mcp.streamable_http_app())`. It has been broken this way through every subsequent commit (Phase 3D, the OAuth build, the "Fix 6 cheap-correctness bundle"), because **no test in the repo ever mounts the app and sends it a real HTTP request** — see §2.3.

**And even the working path is unreachable from the internet as configured.** `docs/MCP_CLIENT_SETUP.md:3,36,62` and `deploy/nginx-empyralis.conf:111` both point clients at `https://<server>/mcp`. The nginx config uses an **exact-match** location block, `location = /mcp` (`deploy/nginx-empyralis.conf:111-120`) — it matches only the literal path `/mcp`, not `/mcp/mcp`. There is no other location block that would catch `/mcp/mcp`; it falls through to the catch-all `location /` (line 158), which proxies to the **Next.js frontend on port 3000**, not the FastAPI backend on 8001. So: the documented path 404s at the backend (§2.1 above), and the actual working path isn't proxied to the backend at all. Net effect as currently committed: **`/mcp` is not reachable from `empyralis.ai` by any client, full stop.** (Caveat, honestly stated: I did not and was told not to touch prod, so I can't confirm this exact nginx file is the one installed on the live box right now — but it's the one in-repo, dated 2026-07-16, with its own header comment describing a reconciliation pass against the live VPS on 2026-07-10, so it's the best available evidence of intended/likely-live routing.)

### 2.2 Once you actually reach it, the rest of the stack genuinely works

To separate "is the mount path wrong" from "does everything downstream of the mount also work," the probe was re-run against the real working path (`/mcp/mcp`) with the real `mcp` SDK `ClientSession`, a real per-workspace key minted through the real `create_workspace_mcp_api_key()`, and a real local Postgres (the same dev DB the app normally uses; nothing touched prod):

```
initialize() OK — server: empyralist 1.26.0, protocolVersion=2025-11-25
tools/list OK — 13 tools exposed: [... exact list matching §1.1 ...]
matches mcp_server.EMPYRALIST_MCP_TOOLS (13 declared)? True

Calling empyralis_list_projects (read tool, real local Postgres, fresh workspace)...
  isError=False
  content: {"ok": true, "projects": []}

Calling empyralis_create_project (WRITE tool), writes_enabled=False, EMPYRALIS_MCP_WRITE_ENABLED unset...
  isError=True
  content: Error executing tool empyralis_create_project: MCP write tools are globally disabled.
```

And auth was independently re-verified at the tool-call layer (not just via the unit tests) by calling `empyralis_list_projects` with no header and with a bogus token against the working path:

```
NO Authorization header      -> isError=True: "Missing MCP API key. Add an Authorization header: ..."
BOGUS bearer token            -> isError=True: "Invalid or revoked MCP API key. Create a new key at ..."
```

So: protocol handshake, tool schema advertisement, tool dispatch to real backend services, workspace-scoped auth, and the write-tool double-gate all function correctly **once a client is pointed at the right URL**. The defect is entirely in the mount path (§2.1) and the nginx routing (§2.1) — not in the auth/dispatch logic itself.

One auth nuance worth flagging under safety (§3): `initialize` and `tools/list` succeed with **no** Authorization header at all — only an actual `tools/call` enforces the key. This is because `_resolve_workspace()` is called manually inside each tool's function body (`mcp_server.py:301` etc.), not wired as SDK-level auth middleware (that only happens when OAuth is enabled — `mcp_server.py:216-253`, `auth_kwargs["auth_server_provider"]`). Net effect on the legacy (default) path: anyone who reaches the endpoint can enumerate the full tool surface (names, descriptions, JSON schemas) without a key; they just can't successfully call anything without one.

### 2.3 Why "58/58 tests pass" didn't catch this

Reproduced: `python3 -m pytest server_modules/tests/test_mcp_oauth_provider.py server_modules/tests/test_mcp_server.py -q` → `58 passed in 1.91s`. Real and reproducible. But:

- `test_mcp_server.py` (9 tests) calls `server_modules.mcp_server_auth` functions directly — key create/resolve/revoke against the flat JSON file. It never imports `mcp_server.py`, never builds the FastMCP app, never mounts it, never sends an HTTP request.
- `test_mcp_oauth_provider.py` (49 tests) tests `EmpyralisOAuthProvider`'s PKCE/token/consent/rate-limit logic directly (DB-fake or SQLite), and the OAuth-branch `resolve_workspace_*` helper functions — again, no mounted app, no HTTP request.
- `test_fix6_cheap_correctness.py` and `test_single_app_root.py` (not part of the "58") test, respectively, that specific dead tools are absent and that route registration happens on a single fake `FastAPI`-shaped object — also no real HTTP call.

**Zero tests anywhere in the repo perform `initialize → tools/list → tools/call` against the actually-mounted app.** That's the exact gap this audit's local probe (§2.2) fills, and exactly why a routing bug present since the very first commit survived six-plus feature commits and a "58/58" green test run.

---

## 3. Safety

- **Workspace isolation:** every tool resolves `workspace_id` from the authenticated key/token, never from a tool argument (§1.2). Verified statically (no tool signature in `mcp_server.py:295-610` accepts `workspace_id`) and empirically (§2.2, cross-workspace unit tests in `test_mcp_server.py::MCPCrossWorkspaceIsolationTests` and `test_mcp_oauth_provider.py::test_cross_workspace_isolation*`).
- **Write tools default OFF, two independent gates:** per-key `writes_enabled` (owner-set at key creation, defaults `False` — `mcp_server_auth.py:91`) AND server-wide `EMPYRALIS_MCP_WRITE_ENABLED` (defaults unset/false — `mcp_server.py:89-91`). Both must be true; verified live in §2.2. Key *creation itself* is owner-gated (`routes_connections.py:883`, `minimum_role="owner"`), so a non-owner workspace member can't even mint a writes-capable key.
- **Destructive tools are correctly annotated** per MCP convention (`ToolAnnotations(destructiveHint=True)`): `empyralis_configure_agent`, `empyralis_assign_channel_bot`, `empyralis_release_channel_bot` (`mcp_server.py:472,504,534`). This is the spec-correct way to signal risk to a client — actual confirmation-before-call UX is a client responsibility (Claude Code does prompt on tool calls by default), not something the MCP server itself can force.
- **No approval-broker involvement:** MCP write tools call `fleet_configure_agent` / `fleet_create_agent` / `fleet_message_agent` etc. directly (`mcp_server.py:441-610`) — the same functions the internal Fleet REST routes call — not through `direct_tool_execution_service`'s in-chat approval/policy broker that governs an agent's own autonomous tool calls mid-conversation. That's a different, and arguably appropriate, model for this surface (a standing, owner-granted, revocable key rather than a per-call approval), but it means there is no per-call human-in-the-loop confirmation on the server side for a granted write key — the gate is entirely "did the owner choose to mint this key with writes on."
- **Minor gap — unauthenticated schema disclosure:** as noted in §2.2, `initialize`/`tools/list` succeed with no credentials at all on the legacy (default) path; only `tools/call` enforces the bearer key. Low severity (no workspace data is returned, only tool names/descriptions/JSON schemas) but worth closing, especially since the endpoint is meant to be reachable from the public internet.
- **All calls are ledgered** — `_ledger_mcp_call()` (`mcp_server.py:181-205`) writes an `event_class="mcp_inbound"` activity event per call, actor `external_mcp_client`; confirmed by `test_fix6_cheap_correctness.py::McpLedgerRestoredTests` and re-derivable from the probe output (every tool call in §2.2 completed without the previously-existing `TypeError` this test's own name references).
- **Revoke is cross-tenant-IDOR-safe:** `revoke_mcp_api_key` scopes the revoke to the key's owning workspace (`routes_connections.py:906-911`; `test_fix6_cheap_correctness.py::McpRevokeIdorTests`).

---

## 4. Fresh spec-currency check

Checked against the live spec site and blog (2026-07-22), not from training-data memory:

- **Current stable spec revision: `2025-11-25`** ([modelcontextprotocol.io/specification/2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)). The installed SDK (`mcp==1.26.0`) reports exactly this in a live `initialize()` response (§2.2: `protocolVersion=2025-11-25`) — **the transport/protocol layer is current, not stale.**
- Per the current spec's [Authorization page](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization): authorization is **optional** for MCP implementations, so a bearer-key-only server isn't a spec violation by omission. But where a server *does* implement request-level auth over HTTP, the spec's Error Handling section says servers **MUST** return HTTP 401 for "authorization required or token invalid" and, for OAuth-style servers, **MUST** implement OAuth 2.0 Protected Resource Metadata (RFC 9728) with a `WWW-Authenticate: Bearer resource_metadata="…"` challenge so a spec-aware client can self-discover how to authenticate. Empyralis's default (legacy bearer) path does neither: unauthenticated calls get a normal `200 OK` MCP-protocol-level tool error (§2.2), not an HTTP 401 with a `WWW-Authenticate` challenge — so a generic OAuth-aware MCP client (e.g. a "click to connect" flow) has no way to discover it needs a key at all; it would just see every tool call fail with an opaque text string.
- The **OAuth 2.1 path** (`server_modules/mcp_oauth_provider.py`) is genuinely built to the current spec's letter — PKCE (S256), RFC 7591 dynamic client registration, RFC 8414/9728 metadata, 49 passing tests covering the token/consent/PKCE lifecycle (§2.3) — but it's **opt-in and off by default** (`EMPYRALIS_MCP_OAUTH_ENABLED`, unset in the code as shipped), and it shares the exact same mount-path double-nesting defect as the legacy path (§2.1), so it isn't reachable either as currently wired.
- **A new spec release candidate, `2026-07-28`, is six days out from today** ([blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/), roadmap: [blog.modelcontextprotocol.io/posts/2026-mcp-roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/)). It's a breaking release relevant to this exact codebase: it **removes the `initialize`/`initialized` handshake and the `Mcp-Session-Id` header** in favor of a stateless core (Empyralis's `StreamableHTTPSessionManager` usage is session-based today), adds required `Mcp-Method`/`Mcp-Name` transport headers, changes the missing-resource error code (`-32002` → `-32602`), and further hardens authorization (RFC 9207 `iss` validation, OIDC `application_type` declaration, credential-to-issuer binding). Not a current defect — it's an RC, nothing breaks on July 28 for existing implementers per the MCP team's own post — but worth a deliberate look once it lands, given how directly it touches the session/transport code this server relies on.

---

## 5. Ordered fix list

1. **Fix the mount path.** Either pass `streamable_http_path="/"` (or `""`) when constructing `FastMCP(...)` in `_build_mcp_server()` (`mcp_server.py:255`) so the inner route lands at the mount root, or change `EMPYRALIST_MCP_PATH` to something that doesn't collide with the SDK's internal default and re-derive the nginx/docs URLs from whatever the real reachable path turns out to be. Add a regression test that actually boots the app (TestClient/ASGITransport, no live server needed) and asserts `POST <documented URL>` returns something other than a 404 — this is the single test that would have caught the bug on day one and is currently entirely absent (§2.3).
2. **Fix nginx to match whatever path #1 produces**, and re-verify against a real deploy (not just this repo's checked-in `nginx-empyralis.conf`) — confirm what's actually installed on the live box, since the file's own comments note it requires a manual install step separate from a code deploy.
3. **Update `docs/MCP_CLIENT_SETUP.md`** to match the current 13-tool surface (§1.1) — it still advertises `list_spaces`/`get_space_status`/`get_recent_alerts`/`ask_space` (deleted) and `empyralis_memory_read`/`_list`/`_write` (deleted, deliberately, per the code's own comment) as available tools. An external user following this doc today would configure a URL that 404s and, if they somehow got past that, would call tools that don't exist.
4. **Return HTTP 401 (with `WWW-Authenticate`) for missing/invalid credentials**, at least for the legacy bearer path, instead of letting `initialize`/`tools/list` succeed unauthenticated and only failing at `tools/call` with an in-band text error (§2.2, §4). This closes both the schema-disclosure gap and the spec-alignment gap in one change.
5. **Decide whether to keep OAuth opt-in or make it the default** for the public deployment, now that it shares the same underlying mount-path bug as the legacy path (#1 fixes both at once) — worth revisiting given the current spec's push toward OAuth as the expected pattern for internet-facing MCP servers, and given ChatGPT / Claude "one-click connector" flows generally assume it.
6. Track the `2026-07-28` spec RC (§4) for the session/handshake and error-code changes once it's final; not urgent today, but this server's session-manager-based design is directly in scope for the stateless-core change.
