# Architecture Concerns


> **HISTORICAL — the baseline this prompt reads no longer exists (2026-08-28).**
> `docs/PLATFORM-MAP.md` was deleted: a commit-pinned 2026-07-13 snapshot
> that had gone stale (122 references to 23 modules that no longer exist),
> per this repository's standing rule that snapshot documents are not kept.
> Every instruction below to read or update it, and every reference to its
> section numbers, is therefore unfollowable. Read this file as a record of
> how the map was produced, not as a work order to run.

Questions from the builder. Your job: read the provided `PLATFORM-MAP.md`, answer every concern against what those documents show, and report what's real vs what's missing vs what's broken. Work exclusively from the documents provided to you.

**What this platform is:** Agents as a Service — the easiest, most reliable way for a business owner to automate real work. Not a chat app. Not a demo. A business owner describes what they need, the platform creates and runs digital workers that do it. Those agents control computers, talk to customers, monitor systems, process data — actual work, reliably, every day. The business owner doesn't need to know how agents work. They just need their work done. That's the billion-dollar product.

Sage is the operator that creates and coordinates specialist agents. Sage is NOT the product — the managed fleet is. The UI you see today (`localhost:3000`) is a works-in-progress that currently shows Sage front and center. The final UI is a professional fleet console where business owners see their agents working, not a chat window.

**MCP-first path:** The platform is also controllable from external AI apps — Claude, ChatGPT, Gemini — via the MCP server at `/mcp`. A business owner can manage their entire fleet, create agents, read memory, and chat with Sage without ever opening the Empyralis UI. Both paths (UI and MCP) should have feature parity.

**What this platform is NOT:** Another ChatGPT clone. A chat app. A conversational AI demo. A Sage-only tool. The current UI might look like one — that's temporary. The architecture underneath is built for managed agents at scale.

---

## 1. Agent Fleet — Creation, Control, Isolation

**The vision:** Sage (operator agent) can create/edit specialist agents for specific businesses. Each specialist has its own credentials, its own accounts, its own enabled/disabled functions. One platform, many agents, each scoped to a different job.

- [ ] Can Sage create a new specialist agent today? Which tool does it use? (`fleet_create_agent`? `fleet_configure_agent`?)
- [ ] Can each specialist have its own AI provider credentials (BYOK per agent, not just per workspace)?
- [ ] Can each specialist have its own channel bindings (different Telegram bot per agent)?
- [ ] Can functions/tools be enabled/disabled per agent? Where is the gating?
- [ ] Is agent-to-agent isolation real? Can specialist A read specialist B's memory? Can specialist A use specialist B's credentials?
- [ ] What happens when a specialist is created — does it get its own session? Its own memory namespace? Its own tool catalog?
- [ ] Is there a UI for any of this? (Phase W = not built yet — confirm)

---

## 2. MCP Integration — Are the Tools Real?

**The vision:** 30+ MCP apps (Gmail, GitHub, Slack, Notion, etc.) connected through OAuth → credential vault → MCP server registration → tool discovery → agent invocation.

- [ ] Of the 30 providers in `APP_MCP_SERVER_MAP`, how many have been tested end-to-end? (OAuth → token → MCP server registered → tool called → result returned)
- [ ] The 8 "fully wired" providers (Gmail, Google Calendar, GitHub, Notion, Linear, Slack, Figma, Dropbox) — has any been tested with a real OAuth token and a real tool call?
- [ ] For providers marked "WIRED" but only "backend bridge live" (Calendly, ClickUp, Webflow, Monday, Box, Confluence, Miro, Intercom, DocuSign, Square, Typeform, Vercel) — are their MCP endpoints actually reachable? Do they return real tools?
- [ ] Are MCP tools correctly scoped per workspace? If workspace A connects Gmail and workspace B connects Gmail, do they see each other's emails?
- [ ] What happens when an MCP tool call fails? Timeout? Auth expired? Does the agent get a clear error or does it silently break?
- [ ] `connectors_actions.py` (2,521 lines) is marked DEPRECATED — is anything still calling it? Can it be deleted?

---

## 3. Memory — Structure, Scoping, Trees

**The vision:** Three-tier memory (config / outputs / private), plus unified memory across captain and specialists, plus 8-layer memory architecture.

- [ ] What does the memory tree actually look like? Is it a flat key-value store or a hierarchical tree?
- [ ] Can an agent read memory from another agent in the same workspace? Should it be able to?
- [ ] The `unified_memory_service.py` defines 8 layers (`profile_memory`, `episodic_memory`, `app_event_history`, `shared_operational_board`, `notes_documents_retrieval`, `specialist_scoped_memory`, `local_private_memory`, `cloud_synced_memory`) — are all 8 implemented or are some stubs?
- [ ] Where is memory physically stored? LanceDB? SQLite? JSON files? Postgres?
- [ ] Can the user see/edit agent memory from the UI? (`sage-memory-pane.tsx` exists — does it work?)
- [ ] Is there memory compaction/summarization? When context gets too long, what happens?
- [ ] How does memory survive a restart? Is it durable?

---

## 4. Reasoning Loops — Before Decision-Making

**The vision:** Before the agent answers or acts, it goes through a reasoning loop. This loop is customizable per agent. Tool calls happen within the loop. The loop continues until the agent decides it's done.

- [ ] What does the reasoning loop actually look like in code? Trace it: agent receives message → ? → ? → ? → reply.
- [ ] Where is the loop limit enforced? (`direct_tool_loop_guard_service.py` — 3 repeat calls → abort?) What's the actual limit?
- [ ] Can the loop be customized per agent? (Max iterations? Tool timeout? Thinking budget?)
- [ ] What prevents an infinite loop? Is there a token budget? A time budget? A turn budget?
- [ ] Does the agent see its own previous tool calls within the same turn? Can it course-correct?
- [ ] Where does triage fit? (`triage_service.py` — scope + identity check BEFORE the reasoning loop?)
- [ ] Is the loop different for direct-chat vs durable-run paths?

---

## 5. Security — Hijack Scenarios

**The vision:** If someone compromises the platform, blast radius is contained. Tenant isolation. No credential theft. Hard boundaries at kernel level.

- [ ] **Scenario: Attacker gets a workspace API key.** What can they access? Only their workspace? Can they read another workspace's agents? Another workspace's memory? Another workspace's MCP credentials?
- [ ] **Scenario: Attacker compromises an agent.** Can the agent access the host filesystem? Can it read `.env`? Can it access other agents' vault entries?
- [ ] **Scenario: Attacker exploits a channel webhook.** Telegram/Discord/Slack webhook is compromised. Can they send messages as the agent? Can they access the control plane?
- [ ] **Scenario: Attacker gets database access.** Postgres is compromised. Are credentials encrypted at rest? Are MCP tokens hashed?
- [ ] **Scenario: Attacker compromises the Gateway.** Gateway runs on user hardware. If it's compromised, can the attacker reach the cloud control plane? Can they impersonate other Gateways?
- [ ] Where is `enforce_workspace_access()` called? Is it on EVERY route? Any routes missing it?
- [ ] Are MCP API keys (`empyralis_mcp_...`) stored hashed? (SHA-256 per `mcp_server_auth.py` — verify)
- [ ] What does the kill switch actually block? (`kill_switch_gate.py` — GLOBAL_KILL_KEY, WORKSPACE_KILL_PREFIX, AGENT_KILL_PREFIX) Is it tested?
- [ ] Is the Rust Supervisor binary COMPILED and RUNNING? (F1 audit said NEVER COMPILED — is this still true?) Without it, desktop-boundary enforcement is Python-only — what's the actual blast radius?

---

## 6. Channels — Cloud vs Gateway, Pigeon Theory Enforcement

**The vision:** Channels are pure transport. Cloud channels (Telegram Bot, Discord Bot, Slack) require no hardware. Gateway channels (Telegram Personal, WhatsApp Personal, Signal, iMessage) require the Gateway on user hardware. All channels normalize into `AgentTurnRequest` — no routing logic, no policy, no session state in the channel layer.

- [ ] **Pigeon theory audit:** Pick 3 channel files (`routes_sage_telegram_hosted.py`, `connectors/slack_connector.py`, `connectors/discord_connector.py`). Does ANY channel file contain: routing logic? Quota checks? Session management? Agent selection? If yes — violation.
- [ ] **Cloud channels:** Telegram Bot, Discord Bot, Slack — are all 3 proven working end-to-end?
- [ ] **Gateway channels:** Telegram Personal (GramJS), WhatsApp Personal (Baileys) — both run on user's Gateway, zero API costs. Proven working? Signal, iMessage — wired but untested?
- [ ] **Should Discord and Slack have Gateway/personal variants?** Currently Discord has a personal variant but anomalously uses `cloud_connector` (bot token, not user account). Slack has no personal variant at all. If the user runs a Gateway, should they be able to route Discord DMs and Slack DMs through it — avoiding API costs the same way WhatsApp Personal avoids Twilio?
- [ ] **Cost model clarity:** Which channels cost the business nothing beyond running the Gateway? Which channels require paying third-party API fees (Twilio, etc.)? Is this documented for business owners?
- [ ] Does the frontend show ALL available channels or only 2? (`workspace-channel-pairing-surface.tsx` — hardcoded `'telegram' | 'whatsapp'`)
- [ ] What happens when a channel message arrives? Trace the full path: webhook → ? → ? → agent → ? → ? → reply delivered. Does it go through `sage_turn_adapter.py`?
- [ ] The `discord_personal` anomaly: `runtime_lane: personal_gateway` but `session_owner: cloud_connector` — which is it? Does this work?
- [ ] Two `telegram_personal` paths (Gateway GramJS vs Cloud Session Manager) — is this intentional or accidental duplication?
- [ ] Adding a new channel: how many files must be touched? (Report says 12+ — verify this count)

---

## 7. Tasks & Planning — Long-Running Work

**The vision:** Give an agent a complex task. It makes a plan. It executes phase by phase. It doesn't stop until done. If it hits an error, it retries or adapts.

- [ ] Does the platform have ANY planning capability? Can the agent produce a plan before executing?
- [ ] `schedule_task` (Phase V) — what does it actually do? Propose a wake request → heartbeat fires turn? Is this the only "task" primitive?
- [ ] Durable runs (`runs_engine.py` → `run_service.py`): how long can a run last? Is there a max? Can it span multiple turns?
- [ ] What happens if an agent stops mid-task? (Crash? Timeout? Credits exhausted?) Does it resume? Does it know where it left off?
- [ ] Is there a task queue? (`local_queue.py` — claims, heartbeats, dead letters) Is this for VPS workers only or also for agent tasks?
- [ ] Can a user see running/pending/completed tasks? (`workstation-runs-pane.tsx` — does it work?)
- [ ] Workflow engine (`workflow_service.py`, `workflow_api.py`, `workflow_repository.py`) — what does it do? Is it used by agents or is it a separate thing?

---

## 8. Agent Reliability — Continuous Work, No Silent Stops

**The vision:** Agents work continuously until their task is done. They don't stop for no reason. If they hit a limit, they report it clearly. If they fail, they retry.

- [ ] What are ALL the reasons an agent can stop mid-work? (Token limit? Timeout? Loop guard? Credit exhaustion? Session expiry? Channel disconnect?)
- [ ] `direct_tool_loop_guard_service.py` — "3 repeat calls → abort" — is this per-turn or cumulative? Does it reset?
- [ ] Session lifecycle: `session_lifecycle_service.py` — max turns (20/80/200), max age (4h/24h/168h), idle timeout (3600s). What happens when a session expires mid-task?
- [ ] What happens when credits hit zero? Hard stop (per architecture decision) — is it actually a hard stop with a clear message, or does the agent just go silent?
- [ ] Is there automatic retry? If a tool call fails (network error, timeout), does the agent retry or does the turn end?
- [ ] Can the user configure max turns/timeout per agent?
- [ ] `bounded_scheduler_service.py` — "quiet hours start = 23" — does the platform stop working at night?

---

## 9. Hardware & VPS — Where Does Work Actually Run?

**The vision:** Three tiers — Cloud (no hardware needed), Gateway (user's own machine), Self-Hosted (user's VPS). Agents can run on any tier.

- [ ] Cloud tier: what actually runs on the cloud? LLM inference? Tool execution? Both?
- [ ] Gateway tier: has ANY user ever run the Gateway end-to-end? (Install → pair → WSS connect → tool invoke → result)
- [ ] VPS tier: `local_queue.py` + worker HTTP poll → claim → execute → heartbeat. Has this been tested on a real VPS?
- [ ] `hardware_runtime_target_resolver.py` — has known mapping bugs (`self_hosted_node` falls through to `cloud_provider`). Are these fixed?
- [ ] The Rust Supervisor binary (`empyralis-supervisor`) is the hard security boundary for desktop actions. It has NEVER been compiled. What is the actual security boundary for Gateway tool execution today?
- [ ] The Rust Runtime Kernel binary (`empyralis-runtime-kernel`) makes 52 policy decisions. Is it compiled? Is it actually called at runtime, or is `run_runtime_kernel` mocked?
- [ ] What VPS providers are supported? (`vps_provisioning_service.py` — what does it actually provision?)
- [ ] `cli_subscription` mode (Claude Code/Codex running on user's Gateway) — spec written, not built. What's blocking it?

---

## 10. Current State — What's Real vs What's Spec

For each of these, mark: **WORKING** / **WIRED (untested)** / **STUB** / **SPEC ONLY** / **BROKEN**:

- [ ] Create a specialist agent from the API
- [ ] Bind a Telegram bot to a specialist
- [ ] Specialist answers a message using its own AI provider credentials
- [ ] Specialist uses MCP tools (e.g., reads Gmail, creates GitHub issue)
- [ ] Specialist writes to its own memory, reads it back next turn
- [ ] User sees agent activity in a UI
- [ ] Sage creates a specialist via natural language ("create an agent that...")
- [ ] Agent makes a plan before executing a complex task
- [ ] Agent works continuously for 1+ hours without stopping
- [ ] Gateway runs on user hardware and executes a shell command
- [ ] VPS worker polls, claims, executes, and returns a result
- [ ] External AI client (Claude Code) connects to Empyralis via MCP and chats
- [ ] Kill switch stops an agent mid-execution
- [ ] User connects Gmail via OAuth, agent sends an email
- [ ] WhatsApp personal channel receives and responds to a message
- [ ] Two workspaces are fully isolated (can't see each other's agents, memory, or credentials)

---

## 11. User Interface — Product vs Platform, Fleet vs Chat

See preamble for what this platform is and is not. The concerns below are specifically about what the UI currently shows vs what it needs to show.

### 11.1 Current UI — What's There and What's Missing

- [ ] **What surfaces exist today?** Audit every pane in `frontend/lib/workspace/`:
  - `sage-chat-pane.tsx` — chat with Sage (THIS is the current home screen)
  - `sage-memory-pane.tsx` — memory timeline
  - `workstation-sage-connectors-pane.tsx` — MCP connector catalog
  - `workspace-channel-pairing-surface.tsx` — channel pairing (only shows 2 channels)
  - `workstation-billing-pane.tsx` — billing
  - `workstation-runs-pane.tsx` — durable runs/threads
  - `cloud-vps-setup-panel.tsx` — VPS setup
  - `codex-chat/` — agent activity timeline (not fully audited)
- [ ] **What's MISSING from the UI?**
  - Fleet overview (all agents, their status, last activity)
  - Agent detail page (channels bound, tools enabled, memory, configuration)
  - Agent creation wizard (name, role, channels, credentials, tools)
  - Activity feed (ledger events across all agents, filterable)
  - Task/run monitoring (what's running, what completed, what failed)
  - Per-agent configuration (model, credentials, channels, tools on/off)
- [ ] **Is `workstation-runs-pane.tsx` functional?** Can a user see running agent tasks? Completed ones? Failed ones?
- [ ] **Channel catalog is hardcoded to 2 channels** (`'telegram' | 'whatsapp'`). Backend supports 27+. Is this still true? When was the frontend last updated?

### 11.2 Professional UX — For Businesses, Not Developers

- [ ] **Blank state:** A new workspace has no agents, no channels, nothing. What does the user see? Is there a guided setup or an empty screen?
- [ ] **Onboarding:** `onboarding/page.tsx` — what does it actually do? Create workspace? Create first agent? Bind first channel? Or just ask questions?
- [ ] **Error states:** When an agent fails, a channel disconnects, or credits run out — does the UI show it clearly, or does it just go silent?
- [ ] **Loading states:** When an agent is thinking or a tool is executing, does the UI show progress? (`codex-chat/` has an activity timeline — is it wired?)
- [ ] **Mobile:** Is the frontend responsive? Do business owners checking on their agents from a phone get a usable experience?
- [ ] **White-label / scoping:** Can a business narrow the UI to only show THEIR agents, THEIR channels, THEIR workflows? Or is it always the full platform surface?

### 11.3 The Real Product

The core promise: a business describes what they need → Sage creates and configures specialist agents → those agents do the work autonomously → the business watches the fleet work.

- [ ] **Can this flow happen today?** Business owner says "I need an agent that monitors my GitHub issues and sends me a Telegram summary every morning." Can Sage create this agent, configure it, bind channels, and have it running — all from conversation?
- [ ] **If not, what's missing?**
  - Sage can create agents (`fleet_create_agent`) ✅
  - Sage can configure agents (`fleet_configure_agent`) ✅
  - Sage can schedule tasks (`schedule_task`) ✅
  - Agent can use GitHub MCP tools? (Verify)
  - Agent can send Telegram messages? (Verify)
  - Agent persists and runs daily? (Verify — wake request infrastructure)
- [ ] **The "one real user" test:** Could a real business use this TODAY? If not, what's the exact list of blockers?

### 11.4 UI Architecture Decisions

- [ ] **Should the home screen be a fleet view or a chat view?** Current: chat with Sage. Proposed: fleet dashboard with agent cards + activity feed. Chat with Sage is ONE pane, not THE product.
- [ ] **Should there be a "simple mode"?** Power users get full fleet management. Regular users get: "Talk to your agent" — one chat, one agent, everything else hidden.
- [ ] **Should the UI be per-business-branded?** White-label: business uploads logo, colors, agent name. Their employees see THEIR agent, not "Empyralis."
- [ ] **MCP-first UX:** The most powerful users will never open the Empyralis UI. They'll connect Claude Code or ChatGPT via MCP. Is this the primary path or the power-user path? The UI and MCP surface should have feature parity.

---

## 12. Training Data Warning

Your training data has a cutoff. Today is **2026-07-03**. When validating any concern that involves "does X still work," "what's the latest Y," or "is Z allowed," do a quick web search instead of relying on training data. If training data and web search conflict, web search wins. The platform was burned by this once already — an earlier agent cited February 2026 ToS as current in July. Don't repeat that.

---

## 13. Target Architecture — From Now to Final Product

The north star is defined in the preamble above. The architecture rules are in `docs/PLATFORM-MAP.md` Appendix A — don't duplicate them here.

### 13.1 Current State (July 2026)

- Sage exists and works — operator agent with fleet tools
- Specialist agents exist — can be created, configured, bound to channels
- 3 cloud channels proven: Telegram Bot, Discord Bot, Slack
- MCP bridge: 8 providers auto-register, 30 in catalog
- Empyralis IS an MCP server — external AI can manage fleet via `/mcp`
- schedule_task exists for proactive agents
- 108 tests pass, backend + frontend boot
- **Missing:** Fleet Console UI (Phase W), Gateway never compiled/run, ~49 platform voice violations remain

### 13.2 Phase Plan — Current to Target

```
PHASE W — Fleet Console UI  ←── CURRENT
  Home = fleet view (agent cards, status, last activity)
  Agent detail (channels, model, tools, memory, triage)
  Activity feed (ledger events, filterable)
  Channel catalog (data-driven from API)
  Agent creation wizard
  Target: from the UI alone — create a specialist, bind Telegram,
          send message, see it in fleet view

PHASE X — Gateway Hardening
  Compile + test Rust Supervisor (shell, fs, OCR, screenshot, mouse,
  keyboard). End-to-end Gateway: install → pair → WSS → tool invoke
  → result. cli_subscription Gateway build per spec. Gateway outbox
  + reconnect hardening.

PHASE Y — One Real User
  Deploy frontend to production URL. Fix channel catalog (show all 27,
  not 2). Fix remaining ~31 impersonation strings. Onboard ONE real
  business: create agent → bind channel → get daily value → come
  back next day. Guided onboarding flow.

PHASE Z — Managed Agency
  Multi-agent fleet visible and manageable from UI. Per-agent:
  credentials, channels, tools, memory, model. Task monitoring:
  running, completed, failed. MCP-first path: external AI manages
  fleet with full parity to UI. White-label: business sees THEIR
  agents, not Empyralis chrome. server/ consolidation (ONE of each
  primitive).
```

### 13.3 What the Agent Should Validate About the Target

- [ ] Does the current architecture (PLATFORM-MAP.md) support this target, or are there structural blockers?
- [ ] What's the shortest path from "108 tests pass on localhost" to "one real business gets daily value"?
- [ ] Which Phase W items are already partially built vs need to be built from scratch?
- [ ] Is the `server/` consolidation (ONE of each primitive) the right move, or should we stay with `server_modules/` and refactor in place?
- [ ] What's missing that's NOT in the phase plan but should be?

---

## Instructions for the Agent Reading This

**CRITICAL: Do NOT read any files from the platform.** Everything you need is in the documents provided to you: this file (`concerns.md`), `PLATFORM-MAP.md`, and the screenshots. Do not open, read, search, or trace any code in the repo. You are working exclusively from what has been given to you. Answer from those documents alone.

1. Read the provided `PLATFORM-MAP.md` first — it has the full architecture.
2. For each concern above, answer based on what's in the provided documents. Do not read platform code.
3. Mark each checkbox:
   - ✅ = confirmed in provided documents
   - ⚠️ = documents indicate it exists but unclear if tested/complete
   - ❌ = documents indicate it's not built or not working
   - 🔍 = cannot determine from provided documents alone
4. For each ⚠️ or ❌, state what the documents say and what's unclear.
5. Add any concerns you discover that aren't on this list.
6. **No time or cost estimates.** Focus on architecture, correctness, and what's built vs what's missing.
