# Agent output: linear-docs

Here is the structured summary of all 8 Linear documents, with every factual claim extracted.

---

## 1. PLATFORM OVERVIEW: Empyralis — one agent, one service, many configurations

**Slug:** `86a18989075a` | **Created:** 2026-06-30 | **Updated:** 2026-07-01

### Core Shape
- One `Agent` class used by both the main agent (Sage) and every sub-agent — same code, different configuration.
- One shared service layer: channel router, OAuth vault, MCP client, session store (JSONL), memory system (file-based), agent-spawn mechanism, remote-hands protocol. One of each. No per-agent duplicates.
- User-visible configurations on top: Sage, sub-agents, channel bindings, MCP connections, memory files.
- Rule: "one primitive, many scoped uses" — no building smaller-x when big-X already exists.

### Agents
- **Sage** — main agent, one per user, all tools allowed by default, speaks directly to the user.
- **Sub-agents** — same `Agent` class, user-facing. Each has: name (agent-picked in user's language at creation), instructions/values (public config), three-tier memory scope, channel bindings (optional, separate from Sage's), connector allowlist (subset of user's OAuth vault).
- Sage can list, create, inspect, and message sub-agents. Sub-agents cannot see each other or Sage's private memory.
- **External agents** (OpenClaw, Hermes, third-party) connect via MCP as tools — not a separate agent kind.

### Three-Tier Memory (per agent)
- `config/` (public): name, instructions, values, allowlist, channel bindings — readable by owning agent + Sage.
- `outputs/`: customer conversations, actions taken, task history (audit trail) — readable by owning agent + Sage.
- `private/`: internal reasoning, working notes, scratchpad — readable by owning agent only (Sage only if user turns on debug).
- File-based, three subfolders, no database, agent-authored content, each file has a purpose header, no artificial size cap.

### Hardware Tiers
- **Base tier (cloud-only):** MCP connectors, memory, channels, chat. No shell, no browser control (those need somewhere to run). Enough for customer-support sub-agents, email/calendar workflows, Notion/Drive automation.
- **Hardware tier (user brings hardware):** Base tier + shell + browser control. Options: user's own Mac/Linux/Windows via Gateway, or user's own VPS via dial-out worker. Both speak same remote-hands protocol.
- **Managed cloud tier:** DEFERRED. Platform-provisioned VPS with shell/browser. Requires payment infrastructure that does not exist yet.

### Credit Model
- Every new user gets 10,000 free credits automatically.
- Credits cover the ENTIRE service (AI model + platform runtime), not just AI tokens.
- Out of credits: buy more, subscribe, or bring own hardware (hardware tier is free at credit level).
- No fallback to a different AI model at zero credits. Hard stop with clear message.
- No paid tiers built yet — deferred until international payment infrastructure is ready.

### UI
- `legacy/frontend` is THE UI — sidebar with Memory, Tasks, Library, AI setup, Connectors, Projects, Agents, Hardware — stays, rewired to new `server/` backend.
- `frontend/v2/` (minimal black chat skeleton) is being killed once legacy is live against new backend.

### Directory Structure
```
empyralis/
  legacy/
    backend/   # v1 backend as REFERENCE ONLY (deleted only after v2 has 1 real user)
    frontend/  # THE UI — rewired to server/ endpoints
  server/      # v2 backend
    agent/     # ONE Agent class + runtime loop
    tools/     # shell, browser, memory, MCP
    channels/  # ONE router + adapters
    memory/    # file-based memory
    mcp/       # ONE MCP client + connector registry
    vault/     # ONE OAuth credential store
    api/       # REST endpoints (rewire target)
  frontend/v2/ # KILLED once legacy is rewired
```

### Phases (dependency-ordered, NOT a schedule)
1. Backend unification (one Agent class, one runtime, one router, one vault, one MCP client, one session store).
2. Rewire legacy frontend to `server/api/main.py`. Delete `frontend/v2/`.
3. Sub-agent primitive (agent-picked name, instructions, three-tier memory, connector allowlist, Sage tools for CRUD).
4. Per-sub-agent channels (own Telegram bot/WhatsApp/etc., same router, routes by binding).
5. Memory + MCP polish (file-header format, Sage-inspects-sub-agent-config, extend MCP beyond 5 apps).
6. Hardware layer (one remote-hands protocol for Gateway and user VPS).
7. Real user validation (one full workflow end-to-end, one real person, iterate).

Phases 1-7 map to Linear sub-issues under MAN-22.

### Non-negotiables
- No time-boxed planning.
- No silent scope-cutting (every removal named, owner has veto).
- No approval system (THE moat — agent acts on reasoning, not gates).
- No agent-voice / platform-voice impersonation (pigeon theory).
- One primitive, many scoped uses.
- Legacy UI stays, legacy backend is reference only.
- One-road AI, no silent fallback.

### What v1 built that stays
- 30 MCP apps (MAN-14, production-ready).
- OAuth + vault encryption pattern.
- Legacy frontend (entire UI).
- All principle documents.
- Gateway concept + partial working code (Telegram Personal and Discord via Gateway proven working).

### What v1 built that stays killed
- Five-agent-kind taxonomy (dead — one `Agent` class + configurations).
- Dual decision engines (Python + Rust) — Python only.
- Per-agent connector duplication — vault is user-scoped, sub-agents get allowlist views.
- Custom tool wrappers — shell + browser + MCP only.
- Approval system — permanently dead.
- `frontend/v2/` bare skeleton.

### History
- Earlier version (2026-06-30) framed build as "6-week rebuild" with August 11, 2026 ship deadline and week-numbered sub-issues (MAN-23 through MAN-28). Withdrawn 2026-07-01: week labels did not correspond to real time (four created and marked Done within ~3 hours on 2026-06-30), and time-based planning was used to justify cutting scope.

---

## 2. PRODUCT SHAPE: Agent model — one Agent class, user-facing configurations

**Slug:** `09aad1212721` | **Created:** 2026-06-30 | **Updated:** 2026-07-01

### Core Model
- Empyralis has ONE `Agent` class. Sage and every sub-agent are instances of the same class with different configuration.
- Matches converged industry pattern: Anthropic has one agent (Claude), OpenAI has one (ChatGPT).

### User-Facing Concepts

**1. Sage (main agent)**
- One instance per user.
- Hardcoded master manifest.
- All tools allowed by default.
- Reach-in assistant available in every channel the user has.
- Speaks to user directly.

**2. Sub-agents**
- USER-FACING. Created by user or Sage on user's behalf.
- Each has: name (agent-picked in user's language, overridable), instructions/values (public config), three-tier memory scope, channel bindings (optional, separate from Sage), connector allowlist (subset of user's OAuth vault).
- Example: Mom's business-support sub-agent can have its own business Telegram bot; her personal Telegram stays with Sage.
- Sub-agents cannot see each other. Cannot read Sage's private memory.
- Sage can list, create, inspect, and message sub-agents.

**3. Connected Services**
- Every app integration is a Connected Service: Gmail, Slack, Notion, Drive, Salesforce, etc.
- External agents (OpenClaw, Hermes) connect via MCP — they are tools, not separate agent kinds.
- One MCP client for whole platform. Credentials scoped to user's vault. Sub-agents get allowlist view.

**4. Automations**
- Not a separate concept. A sub-agent (or Sage) config with a schedule/webhook/trigger.
- Example: "every morning at 8am, summarize new emails" = Sage with cron trigger.

### Three-Tier Memory
- Same as Platform Overview: `config/` (public), `outputs/` (audit trail), `private/` (scratchpad).
- Concrete example: when dad asks Sage "how did that support conversation go?", Sage reads the sub-agent's `outputs/` folder, NOT the sub-agent's `private/` scratchpad.

### Internal / Developer Concepts
- **Skills:** reusable procedure bundles (Anthropic SKILL.md pattern). NOT agents. Packaged instructions/resources any agent loads on demand. Progressive disclosure.
- **Surfaces:** WHERE an agent runs (web chat, Telegram, Discord, hardware desktop, mobile). Not an agent kind — same agent instance, different surface.

### What Is Dead
| Old concept | Why removed |
|---|---|
| "Deployed Agent" as separate kind | Becomes an Automation config |
| "Agent Computer" as agent kind | Hardware tier tool surface |
| "External Agent" as user-facing kind | MCP tools |
| "Studio Agent" as separate kind | Merged into Sub-agent |
| "Native" as separate mode | Same as Sub-agent — one Agent class |

### What Is Added
- Sub-agents as user-facing.
- Three-tier memory scoping.
- Skills (SKILL.md pattern).
- Agent-picked names in user's language.

### User Mental Model
> "I have Sage. Sage does everything by default. If I have a workflow that needs its own identity — its own Telegram bot, its own values, its own memory — I ask Sage to create a sub-agent for it."

### Status
- TARGET state. Current code still uses old terms internally; phase work rewires that.

---

## 3. PRODUCT SHAPE: Channel families — cloud channels, Gateway-required, per-agent bindings

**Slug:** `87f79a94df41` | **Created:** 2026-06-30 | **Updated:** 2026-07-01

### Distinction
- Channels = messaging surfaces where agent appears (Telegram, Slack, Email).
- Apps = integrations agents USE (Gmail, Notion, Stripe) — run via MCP.
- 15 total channels across 3 families.

### Family 1: Cloud Channels (no user hardware)
- OAuth or token paste. Runs on Empyralis cloud. No user hardware needed.
- Telegram Bot, Discord Bot, Slack, WhatsApp Business, Email/Gmail, Email/SMTP, Web Chat, Apple Messages for Business.
- Any can be bound to Sage (default) OR sub-agent (Phase 4).
- Potentially "one-click" if OAuth flows wired.

### Family 2: Personal Channels (Gateway required)
- CANNOT be one-click. Requires Gateway running on user hardware (Mac, Linux, Windows, or user's own VPS).
- Telegram Personal, WhatsApp Personal, Signal, iMessage, WeChat, Discord Personal.
- Reason: these platforms don't expose bot APIs for personal accounts.
- Gateway sits on user hardware, intercepts messages, relays to/from cloud agent.
- Part of hardware tier.
- Personal channels always bind to Sage.

### Family 3: Direct Sage-Hosted Channels
- Subset of Family 1, pre-provisioned by platform. Already working.
- Telegram hosted (Sage), Discord DM (Sage), Web Chat (Sage).

### Routing Rules
1. Sage is default fallback for any unbound channel.
2. Personal channels (Family 2) always bind to Sage.
3. Cloud channels (Family 1) can bind to Sage OR sub-agent (Phase 4).
4. Web Chat has multiple paths (Sage direct + sub-agent web routes when configured).

### One-Click Definition
- User goes from "I want this" to "agent in this channel" in under 2 minutes without leaving dashboard.
- Family 1: potentially one-click. Family 2: never one-click. Family 3: already one-click.

### In-Channel Hardware-Tier Upgrade
- When base-tier user wants a personal channel, platform should detect and say "you'll need the hardware tier (Gateway install)." UX hook not built yet.

### Coding Assistant Instructions
- Family 1: wire OAuth flow, support binding to Sage or sub-agent.
- Family 2: requires Gateway (hardware tier), link to Gateway install, bind to Sage.
- Family 3: already wired for Sage, configure routing.

### History
- Reshaped 2026-07-01. Prior version used "Studio specialists / Studio channels" from 5-agent-kind taxonomy (dead). "Studio" replaced by "sub-agent."

---

## 4. PRODUCT SHAPE: Execution layers — brain, hands, memory

**Slug:** `8293a6c89488` | **Created:** 2026-06-30 | **Updated:** 2026-07-01

### Three Independent Dials
An Empyralis agent = brain + hands + memory. Each layer has independent placement options.

### Brain (LLM)
- **Cloud (default):** Empyralis-hosted (platform credits) or BYOK cloud API.
- **Local:** User's Mac/PC via Ollama or similar (deferred until real user requests it).

### Hands (Action Execution)
- **Cloud (base tier):** MCP tools, cloud channels, memory. No shell, no browser control.
- **Gateway (hardware tier):** User's Mac/Linux/Windows. Shell, file ops, browser control, screenshot.
- **User VPS (hardware tier):** User's own remote server via dial-out worker. Shell, file ops. No browser/screenshot (headless).
- **Managed cloud (deferred):** Platform-provisioned VPS with browser + shell. Requires international payments.
- Gateway and VPS worker speak the same remote-hands protocol — one primitive, two install packages (Phase 6).

### Memory (State, Transcripts, Context)
- **Cloud (default):** file-based, workspace-scoped.
- **Local:** could sit on user's device for privacy scenarios (not default).
- Three-tiered per agent.

### Common Patterns
| Pattern | Brain | Hands | Memory |
|---|---|---|---|
| Base tier (default) | Cloud | Cloud (MCP + channels only, no shell) | Cloud |
| Hardware tier — Gateway | Cloud | Mac/Linux/Windows Gateway | Cloud |
| Hardware tier — user VPS | Cloud | User's VPS worker | Cloud |
| Local-brain + Gateway | Local (Ollama) | Gateway | Cloud |
| Fully self-hosted | Local | Local Gateway | Local |

### Why This Matters
- Different comfort zones: cloud (convenience), local execution (privacy), fully self-hosted (sovereignty).
- Same Agent class runs regardless of layer placement — just calls whichever layer is configured.

### Coding Assistant Instructions
- LLM call: brain layer (`server/agent/providers.py` chooses cloud vs local).
- Tool/action execution: hands layer (cloud MCP OR remote-hands protocol).
- Memory read/write: memory layer.
- Don't assume cloud for hands — check hardware-tier setup first.

### History
- Updated 2026-07-01: "Agent Computer Gateway" shortened to "Gateway"; hands options mapped to base vs hardware tier.

---

## 5. PRODUCT SHAPE: MCP integration — cloud-to-cloud, no user hardware

**Slug:** `b8322eecf6d0` | **Created:** 2026-06-30 | **Updated:** 2026-06-30

### How MCP Works
- Empyralis is an MCP **client**, not an MCP server.
- App integrations run cloud-to-cloud. Users need ZERO hardware to connect apps.

### Flow
1. User clicks "Connect [app]"
2. OAuth flow in browser, token returned to Empyralis backend
3. Token stored encrypted in vault (`~/.empyralis/state/runtime/mcp_servers.json`)
4. Empyralis registers an MCP server entry for that workspace
5. MCP endpoint configured in `APP_MCP_SERVER_MAP`
6. Agent calls tool → Empyralis translates to MCP protocol → vendor's MCP server → response → back to agent

### Transport
- All connections use `streamable_http` (Anthropic MCP standard).
- SSE-only endpoints are not supported.

### What's NOT in MCP
- Channels (Telegram, Discord, WhatsApp, etc.) use direct webhook/API integrations.
- MCP is for APPS only (Gmail, Salesforce, GitHub, etc.).

### Reliability Stack (built 2026-06-30)
- **Timeouts:** 60s for tool calls, 30s for discovery. Two layers: httpx transport + asyncio safety net.
- **Retries:** 3 attempts for tool calls (1s, 2s backoff), 2 for discovery. Retries on connection errors, timeouts, 502/503/504/429. No retry on 401/400/404.
- **OAuth refresh:** automatic on every credential read. Systemic via `resolve_vault_credential`, covers ~50 call sites.
- **Argument validation:** validates against tool input_schema — strips unknown keys, enforces required params, type coercion, 10K char string cap.
- **Endpoint validation:** HTTPS only (prod), blocks loopback/private IPs/reserved TLDs.

### Design Rationale
- User signs up, clicks Connect — no Docker, no local server, no hardware.
- Empyralis bears infrastructure cost (~negligible per call, OAuth tokens are tiny).
- Scales to ~unlimited apps without code changes per app (just add OAuth + MCP endpoint).

### Status
- Production-ready as of 2026-06-30 (MAN-14). 30 apps wired, full reliability stack in place.

---

## 6. PRODUCT SHAPE: Supported MCP apps — 30 wired, 6 removed

**Slug:** `9c37c3a4e8cb` | **Created:** 2026-06-30 | **Updated:** 2026-06-30

### The Canonical List
- **30 apps fully wired** (frontend + backend + OAuth + MCP endpoint).
- **1 stub** (Microsoft 365, waiting on vendor).
- **6 removed** (no vendor MCP exists).

### Fully Wired (30)

**Google Workspace (3 sub-servers):**
- Gmail (`gmailmcp.googleapis.com/mcp/v1`)
- Calendar (`calendarmcp.googleapis.com/mcp/v1`)
- Drive (`drivemcp.googleapis.com/mcp/v1`)

**Dev Tools (6):** GitHub, GitLab, Jira, Linear, Confluence, Vercel

**Project Management (5):** Asana, Monday, ClickUp, Todoist, Notion

**Design (4):** Figma, Canva, Miro, Webflow

**Business / CRM / Sales (9):** Salesforce, HubSpot, Intercom, Stripe, Square, DocuSign, Calendly, Typeform, Zoom

**Storage (3):** Dropbox, Box, Airtable

**Communications (1):** Slack

### Stub — Waiting on Vendor (1)
- **Microsoft 365:** backend entry exists, `endpoint: None`. Microsoft uses per-service tenant URLs (`agent365.svc.cloud.microsoft`), no public unified GA endpoint yet.

### Removed — No Vendor MCP Exists (6, removed 2026-06-30)
- Had no official vendor-hosted MCP server (only local npm/npx packages, don't fit cloud-to-cloud architecture): Bitbucket, Pipedrive, Mailchimp, QuickBooks, Xero, FreshBooks.
- If/when these vendors ship hosted MCP, they can be re-added.

### Special Cases
- **Square:** frontend shows `/sse`, backend uses `/mcp`. Use streamable_http.
- **Confluence:** separate from Jira (different `/authv2` path under `mcp.atlassian.com`).
- **DocuSign:** uses `mcp-d.docusign.com` (`-d` may indicate dev; revisit when prod URL confirmed).
- **GitLab:** Premium/Ultimate tier required (vendor limitation).

### Coding Assistant Instructions
- Don't wire an app not on this list without verifying: vendor hosts streamable_http MCP server, URL responds 401 unauthenticated, OAuth config exists in `OAUTH_PROVIDER_CONFIGS`.
- Adding new app = update `APP_MCP_SERVER_MAP` + frontend `CONNECTOR_DETAIL_MAP` `mcpEndpoint` + confirm OAuth config.

---

## 7. DECISION: AI provider strategy — platform credits + BYOK, one road, no silent fallback

**Slug:** `2f13c73882e7` | **Created:** 2026-06-30 | **Updated:** 2026-07-01

### The Decision (rewritten 2026-07-01)
- Platform provides AI credits by default. BYOK stays available as alternative. No silent fallback between roads.
- Prior version (2026-06-30) said "No platform subscription. No platform-managed AI credits. Users bring their own AI." — withdrawn so non-developers (Mom, Dad, influencer-referred consumers) can use it without needing an API account.

### Path 1: Platform Credits (default)
- Every new user gets 10,000 free credits automatically.
- Credits cover entire service (AI inference + platform runtime).
- Exhausted: buy more, subscribe (deferred), or connect hardware tier (free at credit level).
- Under the hood: platform routes to whichever AI provider gives best cost/quality ratio (currently DeepSeek). Provider name never surfaced to end user — no logos, no model IDs, just "Platform AI."

### Path 2: BYOK API Key
- User pastes Anthropic or OpenAI (or other supported) API key. Stored encrypted in vault.
- Platform calls API on user's behalf. Every call debits user's own API balance, not platform credits.
- Best for developers, power users, users who burned through platform credits.
- Status: shipped.

### Path 3: Local AI Models (deferred)
- User runs Ollama, LM Studio, or OpenAI-compatible local inference server.
- Platform calls `http://localhost:11434` (or user-configured endpoint) instead of cloud API.
- Best for privacy-conscious, hardware enthusiasts, air-gapped deployments.
- Deferred until real user requests it.

### Non-Negotiables
- **One road at a time.** Each user has ONE explicitly selected active provider.
- **No fallback ever.** If active road fails (out of credits, invalid key, local model offline), agent stops with clear message. Does NOT silently switch.
- **Provider identity hidden on platform tier.** Exception: BYOK path, showing chosen provider is fine (user picked it).
- **Hard-stop copy is generic.** No brand names. "You've reached your platform AI limit — open AI & Setup."

### Why No Subscription Yet
- Not a principle — a real prerequisite. Owner not in the US; international payment infrastructure (Stripe/equivalent) takes weeks-to-months for country of operation. 10k free credits are bridge until subscriptions exist.

### What Empyralis Does NOT Do
- No silent provider switching (not between DeepSeek→Anthropic, not platform credits→vault key, not BYOK→local).
- No billing surprises. Users stop at zero credits with clear message. Not charged, not switched, not silently degraded.
- No credit sharing. One user, one credit balance. Sub-agents draw from owning user's credits.

### Implementation Notes
- v2 Agent class accepts `api_key` parameter for BYOK.
- Platform-credit path routes through resolver that picks platform provider based on user's active-provider setting.
- Resolver enforces one-road policy: possessing a vault key does NOT mean using it — user must explicitly switch.
- Local AI path swaps `anthropic.AsyncAnthropic(...)` for OpenAI-compatible client pointing at local endpoint.

---

## 8. DECISION: Agent simplification — synthesis from Anthropic + OpenAI research

**Slug:** `ce005e458c1e` | **Created:** 2026-06-30 | **Updated:** 2026-07-01

### The Decision
- Empyralis adopts converged industry pattern: one Agent class + tools + user-facing sub-agents + skills + automations.
- Research date: 2026-06-30 (MAN-19). Researched: Anthropic Claude/Claude Code/Claude Agent SDK/Claude Cowork/Claude Projects/Skills, and OpenAI ChatGPT/Custom GPTs/Agents SDK/Operator/Codex/Memory.

### Key Findings — Both Companies Converged
1. One user-facing agent brand (Claude / ChatGPT). Not multiple "agent kinds."
2. Agent = config, not a class hierarchy. Immutable dataclass: name + instructions + tools + model. Stateless Runner executes it.
3. Tools define capability, not the agent's "kind." Same Claude in Claude Code (66+ tools), Claude.ai (chat tools), Claude Cowork (desktop tools).
4. Sub-agents = same Agent class. Spawned by parent or created by user. Isolated context. Returns summary/reply to caller.
5. Computer Use / desktop control = a TOOL surface, not an agent kind.
6. Skills (Anthropic SKILL.md) = reusable procedure bundles, NOT agents.
7. Memory = file-based, inspectable. Anthropic uses CLAUDE.md hierarchy. No vector DB required. LLM scans and picks relevant entries.

### Empyralis's Version
- Sage = main agent (one per user). Equivalent to Claude/ChatGPT.
- Sub-agents = same Agent class, user-facing. Own name, instructions, three-tier memory, channels, connector allowlist.
- Automations = Sage or sub-agent config with trigger (cron, webhook). Not a separate agent kind.
- External agents (OpenClaw, Hermes) = MCP tools. Not user-facing agent kinds.
- Hardware (Gateway/VPS) = tool surface, not agent kind.
- Skills = reusable expertise bundles (SKILL.md format).

### Dead Terms (kill on sight)
- "Studio agent" → user-facing sub-agent.
- "Deployed agent" → Automation config.
- "External agent" (as user-facing kind) → MCP tools.
- "Agent Computer" (as agent kind) → hardware tier surface.
- "Native / Studio / External" three-agent-configuration split → collapsed.

### MCP Role
- MCP is the universal protocol both companies have adopted.
- MCP registry IS the connector catalog. No parallel custom connector catalog.

### User Mental Model
> "I have Sage. Sage does everything by default. If I need a workflow with its own identity — its own name, values, Telegram bot, memory — I ask Sage to create a sub-agent. That's the platform."

### What We're NOT Copying
- GPT Store / public marketplace (can come later, architecture must not depend on it).
- Multiple parallel multi-agent patterns (pick one: Anthropic-style sub-agents, parent stays in control).
- OpenAI's GPT vs Assistant vs Agent terminology (pick "Sage" + "sub-agent" + "tool" and stop).

### What We're Explicitly Adding
- Sub-agents as user-facing (not just developer-hidden).
- Three-tier memory scoping per agent.
- Skills (Anthropic SKILL.md pattern).
- Per-sub-agent channel bindings (Phase 4).

### Execution
- Tracked via Platform Overview phases under MAN-22.
- Prior ticket MAN-17 (agent taxonomy refactor) was canceled — replaced by MAN-22 phase issues.

### Coding Assistant Instructions
- Sage is the only main-agent name.
- Sub-agents share same Agent class as Sage.
- Tools attach at runtime, not compile-time.
- Computer Use / hardware access is a tool surface.
- Skills are NOT agents.
- MCP is protocol for ALL tool connections, including third-party agents.
- Reject proposals that: add new agent kind/class hierarchy, build parallel tool catalog separate from MCP, model "Deployed"/"External"/"Studio"/"Native" as separate user-facing concepts, or add custom abstractions for things shell+browser+MCP already do.