# MCP current-state audit — what exists vs. what's needed for workspace-connected MCP apps

**Date:** 2026-07-22
**Method:** direct code reading + repo-wide grep + running the existing test suite. Every claim below is file:line-cited or backed by a command run in this session. Where a prior doc (`docs/tasks/mcp-apps-deep-dive.md`, 2026-06-30) made a claim that turned out to be stale or wrong, that's called out explicitly — this doc supersedes it on those points.

Companion doc: `docs/design/mcp-masters-research.md` (2026-07-22) — how other agent products solve the same problem. This doc is the "where we actually are" half.

---

## 1. Does an MCP client exist today?

**Yes — a real, complete MCP client engine exists:** `server_modules/mcp_registry_service.py` (1,518 lines).

- Uses the official Anthropic `mcp` Python SDK (`from mcp import ClientSession`, `from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client` — lines 23-28). This is a real pip dependency: `requirements.txt:23` pins `mcp[cli]==1.26.0`, and it's installed (`pip show mcp` → 1.26.0, confirmed in this session).
- Transport is hardcoded to `streamable_http` only — `McpTransport = Literal["streamable_http"]` (line 32), and `_normalize_transport()` (lines 115-117) coerces any input to `"streamable_http"` regardless of what's passed. **No stdio, no plain SSE.** This means only remote HTTPS MCP servers are reachable; there is no path for a locally-running MCP process.
- SSRF-hardened endpoint validation (`_validate_mcp_endpoint`, lines 58-95): blocks localhost/127.0.0.1/::1/0.0.0.0, `.local`/`.internal` TLDs, loopback/link-local/multicast/private/reserved IPs, requires HTTPS in prod (HTTP only with `EMPYRALIS_DEV_ALLOW_HTTP_MCP=1`), and additionally calls `assert_safe_outbound_url()`.
- Per-workspace registry, file-backed (`MCP_SERVER_REGISTRY_FILE`, default `~/.empyralis/state/runtime/mcp_servers.json`), writes gated through the Rust runtime kernel (`_enforce_mcp_registry_state_decision`, lines 191-221).
- Tool discovery: `discover_mcp_server_tools` / `_async` (lines 533-574) call `session.list_tools()` over streamable HTTP with retry on transient errors (lines 411-490).
- Tool invocation: `invoke_workspace_mcp_skill` / `_async` (lines 1196-1517) — resolves the server + tool, enforces approval (`_assert_tool_approved_for_execution`), parses/validates arguments against the tool's JSON `input_schema` (`_parse_goal_arguments` + `_validate_mcp_arguments`, lines 1004-1127), resolves the credential from the vault with OAuth refresh (`_resolve_mcp_credential`, lines 341-361), calls `session.call_tool()`, and fully records the call through `agent_action_metering_service` (start/complete/fail/block events).
- Newly-discovered tools default to **unapproved** (line 705-711: `"tools discovered but not auto-approved. Use approve_mcp_tool()."`) — there's a real per-tool approve/deny workflow (`approve_mcp_tool` / `deny_mcp_tool`, lines 788-851), but as documented in §6 nothing in the frontend calls it.
- Skill-id addressing format: `mcp:{server_id}:{tool_name}` (`mcp_skill_id`, lines 929-930).

**Direction (b) — Empyralis itself as an MCP server** also exists and is live: `mcp_server.py` (root, 267 lines per `docs/PLATFORM-MAP.md:340`), mounted into the real FastAPI app at **`server.py:338`** (`mount_empyralist_mcp(app)`), with per-workspace bearer-token auth (`server_modules/mcp_server_auth.py`) and an OAuth provider variant (`server_modules/mcp_oauth_provider.py`). This exposes 9 tools (`list_spaces`, `get_space_status`, `get_recent_alerts`, `ask_space`, plus 4 write tools gated behind `EMPYRALIS_MCP_WRITE_ENABLED=true`, plus chat/memory tools) to external MCP clients (Claude Code, Claude Desktop, ChatGPT) per `docs/MCP_CLIENT_SETUP.md`. Verified: `python3 -m pytest server_modules/tests/test_mcp_oauth_provider.py server_modules/tests/test_mcp_server.py -q` → **58 passed**, no failures. This direction is unrelated to the "workspace connects an MCP app" question — it's the reverse traffic direction — but it proves the MCP protocol layer itself (session, auth, tool schemas) is solid in this codebase.

**Unrelated "mcp" substrings**, for completeness: none of significance found — the grep noise was almost entirely genuine MCP-client/server code, docs, or the DCR ("Dynamic Client Registration") OAuth term which is a different, correctly-used acronym in `connection_oauth_service.py`, `mcp_oauth_provider.py`, `connectors_actions.py`, `connector_validators.py`.

---

## 2. How tools are registered/discovered/surfaced to the model (the live, primary loop)

**This is the important finding: the MCP client above is NOT wired into this system.**

`server_modules/tool_registry_service.py` implements exactly the 2-tier design the task describes:

- `ALWAYS_ON_TOOL_NAMES` (lines 26-39) — 12 tools injected every turn (`task_complete`, `update_plan`, memory tools, `web__search`, `web__fetch`, `hardware__action`, `query_tool_registry`).
- `build_registry_entries()` (lines 171-209) builds the searchable "everything else" registry from three sources only:
  1. `skills_service._builtin_tool_descriptors()`
  2. `skills_service._local_tool_descriptors()`
  3. `skills_service.build_direct_chat_tools(tool_capabilities)` (connected-app tools)
  **MCP is not one of the three sources.** Grep-verified: zero occurrences of "mcp" anywhere in `tool_registry_service.py`, `direct_chat_tool_catalog_service.py` (the facade that wraps it — see `direct_chat_tool_catalog_service.py:7-8` importing `tool_registry_service`), or `skills_service.py`'s `ToolDescriptor`/ `_builtin_tool_descriptors`/`_local_tool_descriptors`/`build_direct_chat_tools`.
- `search_tool_registry()` (line 214) is the lazy-load keyword search called when the model invokes `query_tool_registry`; `get_query_tool_registry_definition()` (lines 371-408) is the tool schema for that meta-tool.
- `ToolDescriptor` (the descriptor dataclass) lives in `server_modules/skills_service.py:37`; `_tool_payload_from_descriptor` at `skills_service.py:310`.

So: the always-on + lazy-load tool-discovery mechanism is real and live, but it only knows about built-in tools, local tools, and the **bespoke per-app connector tools** (see §3) — never MCP-registered tools. An MCP tool a workspace has connected has no OpenAI-style function schema anywhere in this pipeline, so the model can never see it via `query_tool_registry`.

### Where MCP tools DO get a "tool" abstraction
`server_modules/skill_registry.py` bridges MCP tools into a **separate**, older abstraction called a "skill" (`SkillDefinition`, goal-string based, not JSON-schema function-calling):

- `mcp_registry_service.list_workspace_mcp_skill_entries()` (`mcp_registry_service.py:945-986`) turns each approved+enabled MCP tool into a skill-shaped dict (`execution_adapter: "mcp_tool"`, `id: mcp:{server_id}:{tool_name}`).
- `skill_registry._definition_from_mcp_skill_entry()` (`skill_registry.py:1091-1112`) converts that into a `SkillDefinition`.
- `skill_registry._skill_registry_map()` (`skill_registry.py:1115-1137`) merges these into the workspace's overall skill map, alongside built-in and installed skills.

This is a real, working data pipeline — it's just a parallel system to the one the model's structured tool-calling actually reads from.

---

## 3. How tool calls get dispatched (the live loop) — and where MCP is absent

Traced `direct_chat_generation_service.py` → `direct_tool_execution_service.py` → `skills_service.py`, which is the loop the task asked about:

1. `direct_chat_generation_service.stream_provider_backed_direct_chat()` — the model emits a tool call. `query_tool_registry` is special-cased inline (`direct_chat_generation_service.py:1616-1666`, calling `direct_chat_tool_catalog_service.search_tool_registry`, itself a thin re-export of `tool_registry_service.search_tool_registry`). All other tool calls go to `direct_tool_execution_service` (imported at line 15).
2. `direct_tool_execution_service.execute_single_direct_tool_call()` (`direct_tool_execution_service.py:798`) does policy/broker/audit work, then falls through to `skills_service.execute_single_direct_tool_call()` (sync, line 1120) as the final generic handler. **Zero "mcp" references anywhere in `direct_tool_execution_service.py`** (grep-verified).
3. `skills_service.execute_single_direct_tool_call_async()` (`skills_service.py:3476`) is the real dispatch table for the async loop:
   - Built-in connectors (`memory`, `web`, `browser`, `http`, `llm`, `hardware`, `file`, `shell`, `screenshot`, `computer`, `sage_service`, `image`, `messaging` — the set `_BUILTIN_DIRECT_TOOL_IDS`, lines 3311-3334) dispatch to ~24 hardcoded `if connector_id == "..." and action_id == "...":` branches in the sync sibling function.
   - Everything else — i.e. any third-party app connector like Notion, Linear, GitHub, Dropbox, Slack — is routed to **`_execute_custom_connector_tool_call_sync()`** (`skills_service.py:3337-3440`), whose own docstring says: *"Custom OAuth connectors (GitHub, Notion, Linear, Dropbox, Google Workspace, etc.) ... dispatches to `runs_execution._workflow_execute_connector_action`"*.
   - `runs_execution._workflow_execute_connector_action()` (`runs_execution.py:2498`) is the real per-connector executor, and it imports the **bespoke, hand-written Python API clients** directly: `from server_modules.connectors.slack_connector import (...)` (`runs_execution.py:14`), `.github_connector` (35), `.dropbox_connector` (49), `.linear_connector` (58), `.notion_connector` (67).

**MCP is never in this call chain.** Grep-verified zero "mcp" references in `direct_chat_generation_service.py`, `direct_tool_execution_service.py`, and (separately) zero of the 57 files in `server_modules/connectors/` reference `mcp_registry_service` or `streamable_http` — confirmed by `grep -rl "mcp_registry_service\|streamable_http" server_modules/connectors/` returning nothing.

> **Correction to `docs/tasks/mcp-apps-deep-dive.md:31`**, which claims *"58 files in `connectors/` directory — most are MCP bridges now, a few are legacy custom connectors."* This is false as of current `main`: **all 57** files are bespoke per-app implementations (`notion_connector.py`, `linear_connector.py`, `github_connector.py`, `dropbox_connector.py`, `slack_connector.py`, plus Telegram/WhatsApp/Discord channel adapters and autopilot plumbing). None of them call the MCP client. The doc's "Before MCP vs Now" framing (§2 of that doc) does not match the code.

### What an MCP-backed tool would need to plug into this seam
A new branch in `skills_service.execute_single_direct_tool_call_async()` (parallel to `_execute_custom_connector_tool_call_sync`) that recognizes an MCP-shaped `tool_name` and calls `mcp_registry_service.invoke_workspace_mcp_skill_async()` — plus a matching entry point in `tool_registry_service.build_registry_entries()` so the tool has a real JSON-schema function definition the model can see via `query_tool_registry` in the first place. Today neither exists.

---

## 4. The Sage-runtime MCP bridge — wired, but its trigger mechanism is dead code

`server_modules/sage_agent_runtime_service.py` has a **second, separate** MCP invocation path, independent of the loop in §3, built for the Sage operator specifically:

- `_matching_mcp_skill()` (`sage_agent_runtime_service.py:2470-2510+`) — a **keyword/substring matcher**, not model tool-calling: it checks the raw user message text against each MCP skill's id/label/`trigger_terms`, then falls back to description-keyword overlap.
- `_build_mcp_tool_inventory()` (`sage_agent_runtime_service.py:1510-1536`) injects a system-prompt block: *"## Available MCP Tools ... To use an MCP tool, request its described function. Requests are automatically routed to the correct tool."*

**These are dead in production.** There are two copies of the Sage action loop:
- `_run_sage_action_loop_v3()` (`sage_agent_runtime_service.py:2755`) — the **live** one. Its only two callers, `handle_sage_chat()` at lines **4574** and **4653**, are themselves the unified entry point for every Sage turn (web chat and every channel — see `direct_chat_service.py:333-335`: *"UNIFIED ENTRY: route web chat through the SAME handle_sage_chat() that channels use"*). Inside v3, `mcp_skill = _matching_mcp_skill(...)` is computed once at **line 2904** and then **never read again** (grep-verified: exactly one occurrence of `mcp_skill` in the v3 function body, lines 2755-3184). The code right after it even says explicitly: *"All messages go through the LLM with query_tool_registry for tool discovery. No keyword-based MCP routing — the LLM decides which tools to use."* (lines 2922-2923) — but per §2/§3, the LLM has **no schema** to call an MCP tool through `query_tool_registry` either. Net effect: an MCP tool cannot be invoked through ordinary conversation with Sage today.
- `_run_sage_action_loop_v2()` (`sage_agent_runtime_service.py:3184`), aliased as `_run_sage_action_loop_v1` (line 3419) — this is where `_matching_mcp_skill()` is actually *used* (line 3261) and dispatched (`if mcp_skill is not None:` → `skill_registry.execute_skill(...)`, lines 3338-3357). **Zero callers anywhere in the codebase** — confirmed by `grep -rn "_run_sage_action_loop_v1\b|_run_sage_action_loop_v2\b" server_modules/*.py`, which returns only the `def` line and the `v1 = v2` alias line. This function is unreachable.

**Empirical confirmation** — ran the dedicated test suite for this feature:
```
python3 -m pytest server_modules/tests/test_sage_mcp_bridge_v1.py -v
```
3 of 9 tests **fail against current `main`**, specifically the ones that assert an MCP tool actually executes:
- `TestApprovedMCPToolExecutes::test_approved_mcp_tool_executes`
- `TestMCPFailureReturnsControlledError::test_mcp_failure_returns_controlled_error`
- `TestMCPToolResultIncludedInFinalResponse::test_mcp_tool_result_included_in_sage_final_response`

Concrete failure for the first: `AssertionError: 'text_only' != 'tools_executed'` — the test mocks `skill_registry.execute_skill` and expects it to be called; it never is, because `handle_sage_chat` runs `_run_sage_action_loop_v3` (which drops the MCP match on the floor), not `_run_sage_action_loop_v2` (which the test was written against). The other 6 tests in that file pass because they test pure functions (`_definition_from_mcp_skill_entry`, transparency-event formatting) that don't depend on the dead loop.

*(Separately, `test_mcp_registry_service.py`, `test_skill_registry.py`, and `test_tool_broker.py` show additional failures in this sandbox — `McpRegistryRustGateError: unexpected_next_action`. That's a test-infra gap in `server_modules/tests/conftest.py`'s Rust-kernel mock (it doesn't set `next_action` in the mocked decision dict), not evidence about the MCP code path itself — flagged here so it isn't confused with the dead-code finding above, but worth fixing since it currently masks whatever those tests are meant to guard.)*

### The one reachable way to invoke an MCP tool today
`server_modules/command_registry.py:997-1013` — the `/skills <skill_id>` slash command handler (`_handle_skills`) calls `skill_registry.execute_skill()` with whatever `skill_id` text the user types. If a user or script sends `/skills mcp:notion:search ...`, it will genuinely execute via `mcp_registry_service.invoke_workspace_mcp_skill_async`. This is reachable but requires knowing the internal `mcp:{server_id}:{tool_name}` id format — there's no discovery UI for it (§6). A read-only `/mcp` command also exists (`command_registry.py:489`, handler `_handle_mcp` at lines 1086-1110) that lists registered servers and tool counts — it's the only thing that currently surfaces `mcp_registry_service` state to an end user, and it's text-only inside chat.

---

## 5. The connector system — how a user connects a third-party app today, and whether it's MCP-shaped

**Yes, this already reuses the OAuth/vault/DCR machinery — this part is well-built.**

`server_modules/connection_oauth_service.py`:
- `APP_MCP_SERVER_MAP` (lines 2948-3092+) maps **72 providers** to their official MCP server endpoint(s) (verified by parsing the dict in this session) — Gmail, Calendar, Drive, GitHub, Slack, Notion, Linear, Dropbox, Figma, Jira/Confluence, HubSpot, Todoist, Calendly, ClickUp, Webflow, Monday, Box, Miro, Intercom, Typeform, Vercel, DocuSign, Square, Stripe, Salesforce, Airtable, Canva, Asana, Zoom, GitLab, Higgsfield, Zapier, PayPal, Sentry, Attio, Cloudflare, Gusto, Deel, Remote.com, Ashby, Klaviyo, Customer.io, Netlify, Supabase, PlanetScale, Neon, Railway, Heroku, Sourcegraph, Replit, Postman, Buildkite, Socket, Whimsical, Ramp, Brex, Mercury, Robinhood, Amplitude, Mixpanel, PostHog, Meta Ads, SEMrush, Ahrefs, Close CRM, Apollo.io, Outreach, Salesloft, Clay, Fireflies, Fathom, Coda. Only `microsoft_365` has `endpoint: None` (no unified public MCP endpoint yet, per the inline comment). This is a much larger, more current list than `docs/tasks/mcp-apps-deep-dive.md`'s "20 apps" (written 2026-06-30) — supersede that number with 72.
- `complete_oauth_callback()` (`connection_oauth_service.py:3634`) is the **real, PKCE-correct** OAuth completion path, called from **`routes_connections.py:619`** — a live REST route the frontend hits when a user finishes an OAuth consent flow.
- It calls `_register_mcp_servers_for_provider()` (`connection_oauth_service.py:3746-3789`, invoked at line 3726) which, for any provider with an `APP_MCP_SERVER_MAP` entry, calls `mcp_registry_service.upsert_workspace_mcp_server_async(..., credential_id=credential_id, discover_tools=True)` — i.e. **connecting Notion/Linear/Slack/etc. via the existing "Connectors" OAuth UI silently and automatically registers the MCP server and discovers its tools**, best-effort (failures are logged, never block the OAuth flow — see the docstring at lines 3752-3758).
- There's a second, older bridge function, `connect_app_via_oauth_to_mcp()` (`connection_oauth_service.py:3348`), whose own comment (lines 3374-3384) documents that it's broken for any PKCE provider (which is most of them now — Airtable, Canva, Asana, Higgsfield, Stripe, Linear, Notion, ClickUp) because it has no `code_verifier`/`state` round-trip. It's superseded by `complete_oauth_callback`; not the live path.
- Credentials: stored in the same vault as every other connector, via `connectors_actions.create_connector_vault()` (`connectors_actions.py:1800`) / `vault_store.py`. `mcp_registry_service._resolve_mcp_credential()` (`mcp_registry_service.py:341-361`) reads from that same vault by `credential_id` and calls `connection_oauth_service.refresh_oauth_token_if_needed()` before use. **MCP does not have its own credential system — it correctly reuses the existing vault.** DCR (dynamic client registration) support already exists in `connection_oauth_service.py`, `connectors_actions.py`, `connector_validators.py`, and `mcp_oauth_provider.py` for the newer MCP servers that require it (Fireflies, Fathom, Coda, Airtable, Canva, Asana, Higgsfield per inline comments).
- REST CRUD for MCP servers themselves: `agent_registry_api.py` — `GET /agent-registry/mcp/servers` (874), `GET .../{server_id}` (888), `PUT .../{server_id}` (913, save+discover), `POST .../{server_id}/refresh` (952), `GET .../{server_id}/tools` (983), `DELETE .../{server_id}` (1004). All call straight into `mcp_registry_service`. Real, live, workspace-scoped (`member_dependency` auth).

### Is the Connectors/Capabilities catalog MCP-aware?
No. `server_modules/connection_catalog_service.py` (2,789 lines, the source for the "Connectors"/"Capabilities" pane) is a static per-app metadata catalog (`_CATALOG` tuple, `_item()` calls) with **one single generic entry** for MCP as a whole:
```
connection_catalog_service.py:2087-2105
connection_id="mcp", display_name="MCP", lane=LANE_MCP_PLUGIN,
setup_kind="mcp_server", description="MCP servers and plugin packages.",
health_check="mcp_server_health", ...
```
Its "health" is hardcoded, not derived from real state: `connection_catalog_service.py:2556-2557`:
```python
elif lane == LANE_MCP_PLUGIN:
    health_status = "available"
```
This never calls `mcp_registry_service.list_workspace_mcp_servers()` — so the catalog always reports "available" for MCP regardless of whether any server is actually registered for that workspace, and it never lists any of the 72 individually-mapped providers as MCP-connected (they show up, if at all, through their existing bespoke-connector catalog entries — grep-verified zero other "mcp" occurrences in this file).

---

## 6. Frontend — is there any UI to add/see an MCP server?

**No.** `frontend/lib/workspace/workstation-client.ts` has a fully-formed API client wired to the real backend routes from §5:
- `listMcpServers`, `saveMcpServer`, `refreshMcpServer`, `approveMcpTool`, `deleteMcpServer` (lines 821-831, paths at 1184-1190, implementations 2544-2834ish), plus `completeAppOAuthForMcp` (line 909, wired to the OAuth-bridge endpoint).

But: `grep -rln "listMcpServers\|saveMcpServer\|approveMcpTool\|deleteMcpServer\|refreshMcpServer\|completeAppOAuthForMcp" frontend --include="*.tsx" --include="*.ts"` returns **only `workstation-client.ts` itself** — no React component anywhere in `frontend/` calls any of these methods. There is no "Add MCP server" form, no server list, no tool-approval UI. A workspace admin cannot self-serve add a custom/arbitrary MCP endpoint through the product today; the only way an MCP server gets registered is transparently, as a side-effect of the existing per-app OAuth "Connect" button (§5), or manually via the raw REST API / `/skills` `/mcp` slash commands.

Also worth noting: `docs/tasks/mcp-apps-deep-dive.md` cites a file `workstation-sage-connectors-pane.tsx` (7,800+ lines) as the connector catalog UI — **this file does not exist in the current repo** (`find frontend -iname "*connectors-pane*"` → no results). Either it was renamed/refactored away since 2026-06-30, or the doc's frontend claims were never accurate. Treat that doc's frontend section as stale.

`frontend/lib/workspace/fleet/tabs/WorkTab.tsx:251` does have rendering support for MCP activity: it recognizes an activity-feed entry whose name starts with `mcp:` and renders it as "Used a skill" — but this only fires if an MCP tool call ever actually reaches the ledger, which per §3/§4 doesn't happen in the live loop today.

---

## 7. The gateway's role

**None.** `grep -rniE "\bmcp\b" empyralis-gateway/src --include="*.ts"` → **0 matches**. The gateway (`empyralis-gateway/src/`) has no MCP code at all. Given `mcp_registry_service.py` only supports `streamable_http` (remote HTTPS) transport (§1), all MCP traffic today is cloud-backend-to-remote-MCP-server over plain `httpx`/the `mcp` SDK — there is no path today for an MCP server running locally on a user's Agent Computer (e.g. a filesystem or local-app MCP server) to be reached at all. If that's ever wanted, it would need: (a) the gateway to run/proxy a local MCP server, and (b) either a `stdio`-over-websocket bridge or an on-box HTTP listener the gateway exposes back to the backend — none of which exists. For now, "connect an MCP app" = "connect a remote SaaS MCP endpoint"; that's consistent with how the 72-provider map in §5 is entirely public HTTPS endpoints (`mcp.notion.com`, `mcp.linear.app`, etc.), so it covers the common case, just not local/on-box MCP servers.

---

## 8. Verdict

| Layer | State | Evidence |
|---|---|---|
| MCP client engine (discovery, invocation, approval, credential resolution, metering) | **Built, real, solid** | `mcp_registry_service.py`, real `mcp` SDK dep, per-function detail in §1 |
| MCP server-registration REST API | **Built, live, tested** | `agent_registry_api.py:874-1023` |
| Auto-register MCP server on OAuth connect (72 providers) | **Built, live, wired to the real OAuth callback route** | `connection_oauth_service.py:3634,3726,3746`; `routes_connections.py:619` |
| Credential reuse (vault, OAuth refresh, DCR) | **Built, correctly reused, not a parallel system** | `mcp_registry_service.py:341-361`; `connection_oauth_service.py` DCR support |
| Empyralis-as-MCP-server (reverse direction) | **Built, live, mounted, tested (58/58 passing)** | `server.py:338`, `mcp_server.py`, `mcp_server_auth.py`, `docs/MCP_CLIENT_SETUP.md` |
| MCP tools exposed to the model as callable function schemas (primary loop) | **Missing** | zero MCP refs in `tool_registry_service.py`, `direct_chat_generation_service.py`, `direct_tool_execution_service.py` |
| MCP tool auto-invocation from natural conversation (Sage loop) | **Dead code** | `_matching_mcp_skill`/dispatch lives only in unreachable `_run_sage_action_loop_v2` (0 callers); live `_run_sage_action_loop_v3` computes but discards the match (`sage_agent_runtime_service.py:2904`); 3/9 dedicated tests fail against `main` |
| System prompt telling the model MCP tools "are automatically routed" | **False promise in the live path** | `_build_mcp_tool_inventory`, `sage_agent_runtime_service.py:1510-1536`, injected at line 3993 inside live `handle_sage_chat` |
| Explicit invocation via `/skills mcp:server:tool` | **Reachable but undiscoverable** | `command_registry.py:997-1013`; no UI surfaces the id format |
| Connectors/Capabilities catalog reflecting real per-workspace MCP state | **Stubbed** | `connection_catalog_service.py:2087-2105,2556-2557` — one generic tile, hardcoded "available" |
| Frontend UI to add/list/approve/remove an MCP server | **Missing entirely** | API client exists (`workstation-client.ts`) with zero callers anywhere in `frontend/` |
| Gateway involvement / local MCP servers | **None; not supported** | zero MCP refs in `empyralis-gateway/src`; transport hardcoded to `streamable_http` only |
| Legacy per-app connector code (`connectors/`) | **Still 100% bespoke, not MCP bridges** | corrects `docs/tasks/mcp-apps-deep-dive.md:31`; 0/57 files reference `mcp_registry_service` |

### The cleanest seam to build on

Don't build a new `mcp_service.py` — the backend engine (`mcp_registry_service.py`) is already the right shape and is reused correctly for credentials. The gap is entirely in **wiring**, not in the client itself. In priority order:

1. **Surface MCP tools to the model** — add MCP-registered tools as a fourth source in `tool_registry_service.build_registry_entries()` (`tool_registry_service.py:171-209`), so `query_tool_registry` can actually return them with real JSON-schema function definitions (the `input_schema` is already stored per tool on the MCP server record — `mcp_registry_service.py:290,873`).
2. **Add the dispatch branch** — in `skills_service.execute_single_direct_tool_call_async()` (`skills_service.py:3476`), add a branch parallel to `_execute_custom_connector_tool_call_sync` (line 3337) that recognizes an MCP-shaped tool name and calls `mcp_registry_service.invoke_workspace_mcp_skill_async()` directly — this is the real "plug MCP into the primary loop" seam described in §3.
3. **Build the frontend** — a settings panel calling the already-complete `listMcpServers`/`saveMcpServer`/`approveMcpTool`/`deleteMcpServer` client methods in `workstation-client.ts` against the already-complete, tested REST API in `agent_registry_api.py`. This alone would let a workspace connect an arbitrary/custom MCP endpoint (not just the 72 pre-mapped OAuth providers), which today is impossible from the product.
4. **Decide the fate of the Sage keyword-matching bridge** (`_matching_mcp_skill`, `skill_registry.execute_skill`, `_run_sage_action_loop_v2`) — either delete it and the now-false system-prompt promise (`_build_mcp_tool_inventory`), or re-wire its dispatch into the live `_run_sage_action_loop_v3` as a fallback for cases outside the structured tool-calling loop.
5. Wire `connection_catalog_service.py`'s `LANE_MCP_PLUGIN` health check to real `mcp_registry_service.list_workspace_mcp_servers()` state instead of the hardcoded `"available"`.
6. (Larger, optional) gateway-mediated local MCP support, if on-box MCP servers become a product requirement — currently out of scope entirely.

Per-workspace connection records are already correct as designed (the JSON registry keyed by `workspace_id` in `mcp_registry_service.py`, mirroring how every other per-workspace connector state is stored) — no schema change needed there.
