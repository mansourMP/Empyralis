# MCP applications — the build plan

**The punchline:** we are not building an MCP client. We already have one — complete,
SSRF-hardened, tested, metered, and it even auto-registers on OAuth connect for dozens of
real remote-MCP providers. The engine simply **isn't plugged into the live tool-calling
loop**, and it has **no UI**. "MCP applications" is a *wiring + UI + context-hygiene* job,
not a from-scratch build. That's the highest-leverage kind of work there is.

Source research (both verified file:line before this plan was written):
- `docs/design/mcp-current-state.md` — our codebase state, file:line-cited
- `docs/design/mcp-masters-research.md` — MCP spec + how Claude Code / Cursor / OpenAI do it

---

## What already exists (verified)

| Piece | Where | State |
|---|---|---|
| Real MCP **client** (official `mcp` SDK, discovery, invoke, approval, vault creds, metering, SSRF guard) | `server_modules/mcp_registry_service.py` (`invoke_workspace_mcp_skill_async` :1359) | **Built, solid** |
| **Auto-registration on OAuth connect** for remote MCP providers (Notion, Linear, Figma, Canva, Asana, ClickUp, Atlassian, Stripe, Webflow, Monday, …) | `connection_oauth_service.py` `registration_endpoint=…` + `routes_connections.py:619` | **Live** |
| Empyralis **as an MCP server** (reverse direction) | `mcp_server.py`, mounted `server.py:338` | **Live, 58/58 tests** |
| REST API for MCP server CRUD (`/agent-registry/mcp/servers…`, refresh, tool-approve) | routes + `mcp_registry_service` | **Built** |
| Frontend MCP CRUD **client** | `frontend/lib/workspace/workstation-client.ts` (:610, :1184, :2546) | **Built** |

## What's dead / broken (verified)

- **The live tool loop has zero MCP awareness.** The primary path
  (`direct_chat_generation_service` → `tool_registry_service.build_registry_entries` :171 →
  `skills_service.execute_single_direct_tool_call_async` :3476 → `runs_execution`) dispatches
  third-party calls to **55 bespoke per-connector Python files**, never MCP.
- **The only NL→MCP routing is dead code.** It lives in `_run_sage_action_loop_v2`
  (aliased to `_v1`, **no live caller**). The live loop `_run_sage_action_loop_v3` *computes*
  a skill match at `sage_agent_runtime_service.py:2904` and then **ignores it**.
- **No UI.** The frontend MCP client has **zero React callers** — nothing to add/list/approve
  an MCP server. The Connectors-catalog MCP tile hardcodes health to `"available"`.
- **A false promise in the prompt.** The system prompt tells the model MCP tools are
  "automatically routed" — untrue in the live loop.
- **No local/stdio MCP.** The gateway has zero MCP code; transport is hardcoded to
  `streamable_http`, so on-box/local MCP isn't reachable yet.

## The seam (why this is clean)

`execute_single_direct_tool_call_async` already begins with
`connector_id, action_id = callbacks.parse_tool_name(tool_call["name"])` and an
authority-mandate/approval gate. Namespace MCP tools as **`mcp__<connector>__<tool>`** (the
convention every serious product converged on) and `parse_tool_name` splits them for free —
so an MCP branch is a *localized* addition, not a rewrite. Same for the registry: MCP tools
become one more **source** in `build_registry_entries`.

---

## The build

### Phase A — wire the engine into the live loop (backend)
1. **Registry source.** Add MCP tools as a source in `tool_registry_service.build_registry_entries()`,
   **deferred by default**: only name + one-line description enter context; full JSON schema
   loads on demand via `query_tool_registry` (we already have this 2-tier mechanism — MCP
   tools just opt into the lazy tier). Namespace `mcp__<connector>__<tool>`.
2. **Dispatch branch.** In `execute_single_direct_tool_call_async`, after `parse_tool_name`,
   route MCP-namespaced calls to `mcp_registry_service.invoke_workspace_mcp_skill_async(...)`,
   **keeping** the authority-mandate + approval gate that's already there.
3. **Kill the dead path + fix the promise.** Retire the `_v2` NL-match; correct the
   "automatically routed" system-prompt claim so it matches reality.

### Phase B — the Connect-an-MCP-app UI (frontend)
Build against the already-complete REST API + `workstation-client.ts`:
- Add a **remote** MCP server (paste URL → OAuth 2.1 consent; the spec flow already exists in
  the client engine).
- List connected servers + their discovered tools; **approve** tools (approval workflow
  exists); **per-tool allow/deny** so a workspace can expose `create_issue` without
  `delete_repo`; live health (replace the hardcoded `"available"`).
- Reuse the Inbox two-pane north-star; verify on phone + browser.

### Phase C — context-hygiene hardening (the part to copy closely)
Every serious product converged on this; if we call the Anthropic API directly it maps 1:1
onto `tool_search_tool_regex/bm25` + `defer_loading: true` (per-tool or per-server via
`mcp_toolset.default_config`) — don't reinvent it. Encode the thresholds:
- Defer past **~10 tools or >10k tokens** of definitions.
- Treat **30–50 simultaneously-loaded tools** as a hard accuracy ceiling regardless of budget.
- **Cache `tools/list`** discovery across turns, don't refetch every turn.
- New connections default to **curated/deferred**, not "expose everything."

### Phase D — auth durability (unattended agents)
Auto-refresh OAuth tokens silently; on refresh failure, **alert the workspace owner**
(not the agent, which runs unattended for hours/days) that re-consent is needed.

### Phase E — local/stdio MCP via the gateway (later)
Only meaningful once agents run on dedicated boxes (stdio needs client+server co-located).
Add stdio transport support in the gateway then.

---

## Pitfalls to design against (from the spec + the masters)
1. **Tool-name collisions** across connectors/instances → namespace by connector *instance*
   from day one (`mcp__<connector>__<tool>`); retrofitting after call history exists is painful.
2. **Connect ≠ expose-everything** → curated/deferred default + per-tool allow/deny.
3. **Silent auth breakage** on long-running agents → surface failed refresh to the human.
4. **Prompt injection via tool descriptions/results** → the spec flags tool annotations as
   untrusted unless the server is vetted. Mark unreviewed custom connectors as such; never let
   a connector-sourced description silently expand permissions or skip the human gate for
   sensitive ops.

---

## Recommended start
**Phase A + B together** — the backend wiring is small and localized (two known seams), the
UI is pure assembly over a finished API, and together they turn "72 auto-registered MCP
providers that do nothing" into "connect an app and your agent can use it." Phase C is folded
into A (deferred-by-default from the first line). Then D. E is a later, hardware-gated add.
