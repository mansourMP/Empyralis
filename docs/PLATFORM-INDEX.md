# Empyralis — Platform Index

At-a-glance inventory of what exists on the platform today. Static facts
only — no history, no changelog.

Tags used below: `(built, not wired)` = code exists but nothing live calls
it or a UI to reach it is missing. `(planned)` = not built yet.

**Terminology:** the platform has exactly three kinds of agent — owner-facing,
customer-facing (serving the owner), and AskAI. "Sage" is a legacy code
prefix (`sage_*` filenames) and a still-live UI string in places, never a
product concept.

---

## Agents

- Owner-facing agent — one per workspace; full unfiltered workspace tool registry plus operator-only fleet tools; personal channels only, never a business-channel persona
- Customer-facing agents (Fleet specialists) — serve the owner; restricted to their own explicitly-bound connectors; the one live agent class (`workspace_agent_installs`)
- AskAI — the third agent kind; live today as the "Ask Sage" launcher UI; rename to "Ask AI" `(planned)`
- Deployed/Studio agents — a frozen, dormant reference implementation (marketplace, quotas, cost caps); not a live creation path `(built, not wired)`

## Multiplayer / Workspace

- Workspace invites — link-only; the platform has no outbound email sender at all, so the owner copies the returned link and shares it manually; invite bound to one email, role capped at the inviter's own, re-verified against the live DB row (not the token alone) on accept
- `list_workspace_members` — real endpoint, viewer-role gated
- Agent display-name uniqueness — DB-enforced unique index (guarded, self-healing dedupe) on top of the existing app-layer collision check on both create and rename
- Numeric backstops: max wake-ups/task/day = 24; delegation depth cap = 30 (was an effectively-uncapped `999999` literal); the delegation retry path is now gated the same way the two creation paths already were

## Channels — Personal (require Agent Computer Gateway)

- Telegram personal — GramJS via Gateway WSS
- WhatsApp personal — Baileys via Gateway WSS
- Discord personal — bot token via Gateway; produces a workspace-wide credential, not a per-agent one
- Signal personal — signal-cli HTTP bridge via Gateway
- iMessage personal — BlueBubbles HTTP bridge via Gateway (Mac only)
- WeChat personal — no bridge exists anywhere `(planned)`
- Cloud-relayed Telegram/WhatsApp (Cloud Session Manager, no local hardware) — session-creation backend exists, no self-serve UI `(built, not wired)`

## Channels — Business (cloud-only, no hardware)

- Telegram bot — Bot API, hosted or BYO token, per-agent binding
- Discord bot — HTTP Interactions, runs in-process in the Python backend; per-agent binder exists but has zero frontend callers `(built, not wired)`
- Slack — OAuth + Events API; per-agent channel binding wired, unlocks once a Slack OAuth app is configured for this deployment
- Gmail / SMTP email — inbound+outbound, partial coverage
- WhatsApp Business (Twilio) — blocked for general-purpose assistants by Meta policy; task-specific bots remain allowed
- Apple Messages Business, web chat widget, Microsoft Teams, Matrix — `(planned)`

## Channels — Work-system connectors (cloud-only)

- GitHub, Linear, Notion, Dropbox, S3, SMTP, WeChat Work, Instagram Business — proven
- Microsoft 365 — partial

## Channels — Ingress Gates

- One shared mention resolver (`mention_gating_service`) replaces four duplicated inline gates that used to live separately in WhatsApp, Telegram, local-bridge, and cloud code paths
- `group_policy` (open / allowlist / disabled), `requireMention` defaults OFF — see-and-decide is the product default, not a hard mention gate; owner-configurable per agent per channel
- Triage input-blocking gate removed entirely — every inbound message reaches the reasoning model, unconditionally; the model's own `[SILENT]` choice is unaffected

## Brains / Providers

- Four payment modes on every agent's `model_config`: `platform_credits`, `byok_api`, `cli_subscription`, `local`
- `platform_credits` — DeepSeek only, Empyralis pays, debited from the workspace credit ledger
- `byok_api` — 13 providers, key stored in a Fernet-encrypted per-workspace vault, customer pays the provider directly
- `cli_subscription` — Claude Code or OpenAI Codex, run via the Gateway under the customer's own login; no in-product model picker (CLI's own default)
- `local` — Ollama on customer hardware via the Gateway, zero platform cost
- 17-provider catalog total across all modes
- Owner-facing agent's own Model tab accepts and saves `cli_subscription`/`local`, but turn-time resolution never checks it `(built, not wired)`
- A specialist's own BYOK provider swap can outrun its stored credentials (stale key threaded through unchanged)

## Memory

- `MEMORY.md` — one markdown file per agent, injected into every owner-facing-agent turn
- `memory_read` / `memory_write` / `memory_list` — path-traversal-hardened LLM tools
- Per-file caps (200 lines / 25KB) and per-agent file-count caps (40 hardware-backed / 20 cloud-only), enforced atomically
- Self-maintaining `MEMORY.md` index — every write/delete upserts its own index line; index can never list a file that doesn't exist
- Provenance + trust tiers (owner / non_owner_sender / unverified / agent_inferred), computed not stored; a non-owner or unverified write requires an `attribution_reason` or is rejected
- `memory_search`/`memory_read` return a retrieval-honesty envelope (`status`/`files_searched`/`errors`) — a failed or incomplete search can never look like a confirmed-empty one
- Secret redaction wired into all four native write seams (`memory_write_file`, `update_memory_context_file`, `memory_append_daily_note`, `apply_memory_consolidation_staging`) — previously wired to only one
- No SOUL/IDENTITY/USER/GOALS/AGENTS/TOOLS root-file taxonomy — removed entirely, not hidden; `MEMORY.md` is the only official root memory file (durable profile facts live in an indexed `memory/files/profile.md` topic file instead)
- Semantic/topic retrieval layer (SQLite `memory_entries`, optional embeddings) — gated, fires only when a message-heuristic says a query needs it
- Cross-session continuity — old turns summarized and carried into the next session's metadata
- Memory tab — per-agent file-tree browser, editable, live "starter scaffold" banner for untouched templates
- Separate `/api/sage-memory` CRUD API (export/wipe/pin) — no confirmed live UI consumer `(built, not wired)`

## Isolation (per-agent, infrastructure level)

- Every agent's memory database is a physically separate SQLite file: `memory/<workspace_id>/agents/<agent_install_id>/`
- Every agent's MEMORY.md and topic files live in their own directory: `context/<workspace_id>/agents/<agent_install_id>/`
- Agents address memory by logical NAME only; the server resolves the path from the agent's verified identity — a model can never supply a path, so it can never reach another agent's store
- On-box (gateway) file and shell access is scoped per agent: mount `agent-<agent_install_id>__<bucket>`; several agents on one VPS cannot share a directory
- Identity always comes from the verified session, never from the tool call's own arguments — not model-forgeable
- An empty agent id means the owner-facing agent's own turn (server-controlled signal, not a missing value)
- Connector credentials are per-agent bound (`agent_connector_bindings`) on top of the project-scoped vault
- MCP server-credential registry refuses a cross-agent overwrite instead of silently swapping whose account a provider resolves to (contained, not yet the full account-aware schema rework)
- A subscribed (non-connecting) agent's connector reuse resolves its own enabled binding, not an unscoped "most recently connected" credential

## Skills

- One unified skill catalog (`skill_registry.list_skill_definitions`) backs both the Tools tab and the model's own capability manifest
- `skill_invoke` — owner-only Level-2 dispatch tool, live in the turn loop
- `skill_write` — agent self-authors a skill; lands disabled pending owner review
- Six bundled skills on disk: memory-manager, code-runner, file-manager, telegram-bot, vision-monitor, business-skill-template
- Marketplace skill install/publish pipeline — real backend (JSON-file storage), no frontend surface anywhere `(built, not wired)`
- Capability-manifest skill slice — fixed at 6-of-16 reserved prompt slots, not scaling with context window

## Tools & Connectors

- Tool broker — single dispatch point for every MCP / Gateway / VPS tool call
- Connect/disconnect round trip — real for both OAuth and manual-credential paths, both directions
- 46-entry connector catalog; Notion and GitHub confirmed to execute real third-party API calls (search/create/update, issues/PRs)
- `vault_credentials` (Postgres, project-scoped) + `agent_connector_bindings` (per-agent enable/disable)
- Legacy `/api/connectors/vault` credential API — superseded, kept alive only by one internal fallback `(built, not wired)`
- 10 OAuth providers still need a manually registered dev-console app + env-var client_id/secret before
  any agent can connect (only a secret-less `GOOGLE_OAUTH_CLIENT_ID` exists today); 16 others
  self-register via RFC 7591 DCR, zero setup — step-by-step registration checklist per provider in
  `docs/OAUTH-PROVIDER-SETUP.md`

## Governance & Safety

- Authority Mandate — owner/audience/system tiers; two fail-closed execution gates shared across the direct-chat and durable-run paths; unstamped tier fails closed to audience
- `mandate.audience_tools` — owner-declared per-agent allowlist of tools an audience-tier sender may trigger; no frontend surface, PATCH-only `(built, not wired)`
- Kill switch — 5 scopes (global, workspace, agent, gateway, channel); only workspace and agent are fully wired with UI; gateway has routes but no UI; global and channel scopes have zero write callers `(built, not wired)`
- Tool-honesty guard — two independent runtime pipelines (owner-facing-agent chat, specialist direct chat), each with proactive prompt rules plus a post-hoc consistency check; on by default
- Capability presets (knowledge / standard / operator) — three real, different starting toolsets; the wizard hardcodes `standard` for every new agent `(built, not wired)`
- Admin-only tool contracts — an ops kill-switch layer behind a platform key, not a workspace-owner control (by design)
- `/tools` chat command — a hand-maintained hardcoded tool list, can drift from the live per-agent catalog

## MCP — Client + Server

- Empyralis AS CLIENT — OAuth → credential vault → MCP server auto-registration → tool discovery, streamable_http transport only
- 31 providers in the connections catalog (30 with a live MCP endpoint); 8 fully wired with an auto-registering bridge (Gmail, Calendar, GitHub, Notion, Linear, Slack, Figma, Dropbox); ~13 more wired via a frontend+backend bridge (ClickUp, Webflow, Monday.com, Box, Confluence, Miro, Intercom, DocuSign, Square, Typeform, Vercel, Calendly, Higgsfield); remainder OAuth-only at varying tool-completeness
- Microsoft 365 — OAuth-only, no public MCP endpoint yet
- Discord — has an OAuth config but no MCP-server entry by design; feeds the bot-token connector instead
- Empyralis AS SERVER — 17 tools at `/mcp`, Bearer `empyralis_mcp_...` (SHA-256 hashed); 5 read + 4 task tools always live, 8 write tools gated behind `EMPYRALIS_MCP_WRITE_ENABLED`
- The 4 task tools (list/get/update-status/comment on tasks) are deliberately NOT behind the write flag — gating self-status-reporting behind the same switch that unlocks agent creation would trade more privilege than the action needs
- External agents (Codex/Claude Code sessions outside the platform) get a platform-minted identity at MCP-key mint, auto-named and collision-checked against platform agent labels too; `list_unified_roster` exposes one closed `{platform|external}` list for a future mention resolver

## Hardware & Gateway

- Agent Computer Gateway (Node.js) — outbound WSS tunnel from user hardware to cloud, no inbound holes, survives NAT/firewalls
- Self-hosted VPS node — HTTP-poll worker, shell + file tools only
- Cloud tier — no hardware required; bot APIs, webhooks, OAuth apps
- Gateway capability router — 9 executors (browser, external_agent_proxy, personal_channel, shell_sandbox, llm, cli_setup, self_update, doctor, restart); no desktop-control executor
- Desktop/computer control (Rust Supervisor) — archived by owner decision; code preserved under `_archive/supervisor/`, not live; "agents do not control user desktops"
- Hardware tab — per-agent access picker (none / gateway / vps / all), a binding fully independent of the CLI-subscription "brain" binding
- Placement resolver — a `cli_subscription` agent's brain dispatch reads `gateway_binding` exclusively, never falls back to `hardware_access` (verified)
- "Agents running here" list on a machine's detail page undercounts — it filters on the tool-access binding only, not the brain binding
- On-demand hardware-probe endpoint (`GET /gateway/hardware/capabilities`) — zero frontend callers, separate from the wired capabilities grid `(built, not wired)`

## Scheduling / Autonomy

- Per-agent wake-up requests — real "Schedule a wake-up" UI, real Postgres table, now live-executed via a cross-workspace scanner
- Legacy single-workspace `HeartbeatScheduler` — still starts with no workspace scope and can never advance a row on its own; superseded by the scanner above, not deleted
- Cron / weekly scheduler — separate system, live executor on by default, polls every 20s; state lives in a local JSON file, not Postgres; no frontend UI creates a schedule through it `(built, not wired)`
- No ledger event type exists for "a scheduled/autonomous run fired" — only directly observable via table/log inspection
- Sub-agent delegation (orchestrator → specialist child runs) — full backend, role model, depth cap, UI already renders its trace events; zero confirmed callers `(built, not wired)`
- Agent-to-agent mailbox (`fleet__message_agent`) — writes real rows into the target agent's inbox; nothing ever reads them back into a turn `(built, not wired)`

## Billing / Credits

- Workspace credit ledger — Stripe-backed, atomic Postgres debit, backs `platform_credits` mode
- DeepSeek is the confirmed default platform provider
- Every new specialist is seeded with `deepseek-reasoner` by default
- BYOK vault — Fernet encryption, PBKDF2-HMAC-SHA256-derived key, random per-secret salt
- No fallback to a cheaper model at zero credits — hard stop, by design

## Frontend Surfaces

- FleetShell — the only workspace UI surface (floating-panel chrome); the legacy workstation shell is fully removed
- Fleet Home — agent grid, status strip, new-agent entry point
- Agent detail — 9 visible tabs (Overview, Model, Work, Channels, Connectors, Tools, Capabilities, Hardware, Memory) plus one hidden `chat` tab reachable only via CTA or direct URL
- Create-agent wizard — 4 steps (Placement, Brain, Channels, Connections); agent is created on step 1, auto-named, later steps are optional PATCHes
- Primary rail — 5 nav items (Inbox, Conversations, Projects, Agents, Hardware); Billing and Settings live in the account-menu popover
- Single `data-theme` attribute — one theming source of truth across every surface
- Catch-all API proxy (`/api/*`) forwards every backend call from the frontend

## Runtime Kernel

- `empyralis-runtime-kernel` — Rust CLI, policy decision engine; reads JSON on stdin, writes `{ok, decision, reason}` on stdout
- ~50+ decision commands: policy/risk/approvals, execution planning + Docker sandbox config, gateway frame/heartbeat validation, run lifecycle, deployed-agent authorization
- 7 autonomy modes: yolo, cautious, read_only, safe_autopilot, trusted_workstation, ask_every_time, deny_all
- Session bounds: 20/80/200 turn caps, 4h/24h/168h age caps

## Storage

- Postgres — production control plane (tenants, workspaces, agents, installs)
- SQLite fallback — local dev when `DATABASE_URL` is unset; same schema, transparent to callers
- Per-agent `MEMORY.md` + `memory/files/**.md` — plain markdown on disk, not a database table
- `vault_credentials` (Postgres) — BYOK secrets, Fernet-encrypted
- Local JSON files (inconsistent with the Postgres-first pattern elsewhere): `mcp_servers.json` (MCP registry), `providers/profiles.json`, `automations/weekly_schedules.json`
- `graphify-out/graph.json` — AST knowledge-graph cache, a dev artifact, not runtime storage
- 18 of ~21 growing stores have no deletion path at all short of a full manual workspace wipe; a real, tested retention job exists and is never invoked in production; a full workspace wipe itself still misses 12 of the 21 stores

## Known Defects — under active fix, verify against code before relying on it

- Compaction (`compaction_service.py`, `sage_agent_runtime_service.py`) — the background auto-compaction job is a fire-and-forget `asyncio` task with no kept reference, wrapped in nested bare `except Exception: pass`, so it can silently never complete and never say why; 6 of 7 `compact_turns()` call sites never thread `previous_summary` through; `load_previous_summary` reads the OLDEST 10 turns in a thread, not the latest; a persisted compaction summary is filtered out by the very next reload (`role in {"user","assistant"}` excludes `compaction_summary`); the flat 16384-token reserve clamps `should_compact`'s threshold to 1 on small context windows (near-permanent compaction) while being disproportionately thin on 1M+ windows; `max_context_tokens=0` (documented as "use model default") silently becomes 128000 via a falsy-zero `or` bug
- Billing — one real LLM call in the hosted-direct-chat path writes 3 ledgers, not the 4 a first-pass estimate assumed (the 4th, `deployed_agent_monthly_cost_ledger`, is scoped to a mutually exclusive surface); the assistant-reply-persisted-twice bug is already fixed (`text_ref` pointer replaces the duplicated text)
- `normalize_model_tier()` — the Standard/Operator capability presets seed `model_tier: "standard"` and Knowledge seeds `"cheap"`; neither string is a real tier, so both silently resolve to the `"pro"` fallback with no error or log line
