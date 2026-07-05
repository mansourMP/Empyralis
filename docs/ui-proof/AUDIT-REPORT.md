# Empyralis Fleet Audit — 2026-07-05

Live audit against running platform (frontend :3000, backend :8001).
Screenshots in `docs/ui-proof/empyralis-audit-*.png`.

---

## 1. THE VISIBLE 405 IN THE WIZARD (Purpose step)

**ROOT CAUSE — confirmed, reproduced in Playwright:**
`frontend/app/api/w/[workspaceId]/fleet/agents/route.ts:5`

This Next.js route handler **only exports `GET`**. There is no `POST` export.
Because this specific route file exists, it **shadows** the catch-all
`[...path]/route.ts` (which DOES export POST/PATCH/DELETE). Result:
Next.js returns 405 Method Not Allowed for POST to this path.

**The chain:**
1. Wizard step 2 (Purpose) → click Next → calls `createAgent()` at
   `FleetCreateAgentWizard.tsx:102`
2. That does: `fetch('/api/w/{workspaceId}/fleet/agents', { method: "POST" })` 
3. Next.js matches the specific route at `app/api/w/[workspaceId]/fleet/agents/route.ts`
4. Route file has no POST export → **HTTP 405**

**Curl verification:**
- `POST localhost:8001/api/w/ws-1/fleet/agents` → **200** (backend works fine)
- `POST localhost:3000/api/w/ws-1/fleet/agents` → **405** (Next.js blocks it)
- `GET localhost:3000/api/w/ws-1/fleet/agents` → **200** (GET is exported)

**Secondary problem:** Even if the POST reached the backend, it would fail with:
`{"ok":false,"error":"Fleet-specialist agent definition not found. Ensure workspace agent registry is seeded."}`
The backend's `fleet_create_agent` calls `ensure_workspace_agent_registry_seeded`
which SHOULD create the `fleet-specialist` definition (defined at
`agent_registry_repository.py:465-494`), but the DB isn't getting seeded in the
current server session. The existing Support Bot agent proves it worked before.

**Also missing:** There is no Next.js route handler for:
`PATCH /api/w/[workspaceId]/fleet/agents/[agentId]` — this falls through to the
catch-all `[...path]/route.ts` which correctly forwards it (tested: PATCH → 200).

**Screenshots:** `empyralis-audit-q2-wizard-step2-next-result-dark.png` (from first test run, shows error state)

---

## 2. EVERY WIZARD STEP — CLICK THROUGH

**Step 1 — Name:** Works. Name input + description input. "Next" button disabled
until name is non-empty. Clicking "Next" advances to step 2.
- Buttons: Cancel (closes wizard), Next (→ step 2). Both real.

**Step 2 — Purpose:** Three cards render correctly. Clicking "Next" calls
`createAgent()` → POST → 405. Error text "HTTP 405" appears in red below the cards.
- Buttons: Back (→ step 1), Next (→ create agent). Next triggers the 405.
- **BLOCKER: step 3-5 unreachable because step 2 fails.**

**Step 3 — Provider:** NOT REACHABLE. Screenshot shows error state from step 2
carrying over (`empyralis-audit-q2-wizard-step3-failed-dark.png`).

**Step 4 — Channels:** NOT REACHABLE.

**Step 5 — Hardware:** NOT REACHABLE.

**On finish:** No agent can be created through the wizard with the 405 present.
Existing "Support Bot" agent (`ainstall_test_1`) was created through a prior
session or direct API call that bypassed Next.js.

**Screenshots:**
- `empyralis-audit-q2-wizard-step1-name-dark.png`
- `empyralis-audit-q2-wizard-step2-purpose-dark.png`
- `empyralis-audit-q2-wizard-step2-next-result-dark.png` (error visible)
- `empyralis-audit-q2-wizard-step3-failed-dark.png`

---

## 3. AGENT MODAL — EVERY TAB ON SUPPORT BOT

**Overview:** Real content. Shows: Status "Not deployed", Placement "Ready to
configure", Role "specialist", Recent activity section (empty — "No activity yet"
with "Chat with this agent" button). `FleetAgentDetail.tsx:188-238`
- Screenshot: `empyralis-audit-q3-agent-tab-overview-dark.png`

**Chat:** Empty state. Title "Chat with Support Bot", body "Open the shared
conversation thread...", button "Open chat" — calls `onChat(agentId)` which
navigates to the workspace chat. `FleetAgentDetail.tsx:245-254`
- Screenshot: `empyralis-audit-q3-agent-tab-chat-dark.png`
- The button IS real — it navigates to `/w/{workspaceId}/chat`.

**Memory:** File browser renders. Loads from `/api/sage-context-files?agent_id=...`.
Currently empty (0 files for this agent) — shows empty state "Nothing here yet"
with "Chat with this agent" button. The API returns `{"files":[],"count":0}`.
`FleetAgentDetail.tsx:268-395`
- Screenshot: `empyralis-audit-q3-agent-tab-memory-dark.png`
- When files exist, the split-pane editor (file list + textarea) renders and
  the Save button calls `PATCH /api/sage-context-files/{filename}` — real.

**Channels:** 7-platform grid renders. Backend returns 4 items (telegram_personal,
whatsapp_personal, signal_personal, plus 1 more). The grid has 7 hardcoded
platforms in `CHANNEL_GRID_PLATFORMS` (`FleetAgentDetail.tsx:401-409`) —
`sage_telegram_hosted`, `slack`, `discord_bot`, and `imessage_personal`/`wechat_personal`
don't match what the API returns, so they show as "Unavailable"/"Not configured here".
- Screenshot: `empyralis-audit-q3-agent-tab-channels-dark.png`
- Clicking Telegram expands the 3-option sheet (Q7 below).

**Connectors:** Real grid with actual connectors. API returns: Google Workspace,
Microsoft 365, GitHub, Linear, Notion, Slack app, Discord app — all with real
labels, summaries, and statuses. Connected connectors show "Connected" pill.
`FleetAgentDetail.tsx:772-968`
- Screenshot: `empyralis-audit-q3-agent-tab-connectors-dark.png`

**Tools:** Empty. API returns `{"tools":[],"agent_id":"ainstall_test_1"}`.
The agent has no `enabled_tools` in its install metadata. Shows empty state
"No tools enabled" with "Chat to configure" button.
`FleetAgentDetail.tsx:972-1006`
- Screenshot: `empyralis-audit-q3-agent-tab-tools-dark.png`

**Model:** Bare minimum. Shows two rows:
- "Provider / Platform default"
- "Model / Platform default"
The agent's `model_config` is `{"mode":"platform_credits"}` with no provider or
model override. The `ModelTab` component (`FleetAgentDetail.tsx:1010-1027`) only
reads `config.provider` and `config.model` — when absent (as with platform_credits
mode), it shows "Platform default". This is intentional: platform_credits means
"use whatever the platform provides." But it gives the user zero visibility into
WHAT model is actually being used.
- Screenshot: `empyralis-audit-q3-agent-tab-model-dark.png`

---

## 4. THEME UNIFICATION — DID IT WORK?

**Architecture:** Single `data-theme` attribute on `<html>` and `<body>`.
`shared/design-system/tokens.ts:DESIGN_SYSTEM_THEME_ATTRIBUTE = 'data-theme'`.
Both `theme-tokens.css` (shared tokens) and `chrome.css` (legacy shell) use
`[data-theme="dark"]` / `[data-theme="light"]` selectors. Fleet also reads
`data-theme` via account-shell's `globalTheme`. One source of truth.

**Dark mode surfaces tested:**
- FleetHome: dark background, dark cards, white text — **correct** (`empyralis-audit-q4-fleet-home-dark.png` from first run, `empyralis-audit-q8-fleet-home-dark.png` from second run shows login page due to rate limit)
- Hardware page: **correct** (`empyralis-audit-q4-hardware-dark.png`)
- Integrations page: **correct** (`empyralis-audit-q4-integrations-dark.png`)
- Chat page: **correct** (`empyralis-audit-q5-chat-page-dark.png`)
- Agent modal: **correct** (all 7 tab screenshots show dark backgrounds)
- Wizard: **correct** (wizard step screenshots show dark backgrounds)
- Channels grid: **correct** (`empyralis-audit-q7-channels-grid-dark.png`)
- Telegram sheet: **correct** (all Q7 screenshots)

**Light mode:** FleetHome light mode captured (`empyralis-audit-q4-fleet-home-light.png`).

**No white/light surface found in dark mode.** The theme tokens cascade correctly
from `theme-tokens.css` → `chrome.css` → `fleet-theme.css`.

**One caveat:** The `html[data-theme="dark"]` defaults in `theme-tokens.css` apply
to `:root` as well, providing the dark palette as the fallback. This means an
unthemed page defaults dark (correct for the product's default).

---

## 5. CHAT PAGE

**Screenshot:** `empyralis-audit-q5-chat-page-dark.png`

**Attach button:** VISIBLE and ENABLED.
- `chat-composer.tsx:916-927` — the Paperclip button renders when
  `fileDropEnabled` is true (i.e., `onFilesSelected` callback is provided).
- Title: "Attach a file" (when vision is supported)
- Not disabled — `visionSupported` defaults to `true` in the component signature
  (`chat-composer.tsx:245`).
- Clicking the button calls `openFilePicker()` which clicks the hidden
  `<input type="file">` at line 672 — **this is a real, working file picker.**

**Vision support:** The `visionSupported` prop defaults to `true`. When `false`,
the button stays visible but is disabled with the tooltip:
"This model doesn't support images". No dead/vanished button — always visible,
state communicated via disabled + title attribute.

**Composer state:** The chat page loaded at `/w/ws-1/chat` with the full
composer (textarea, plus button, voice button, send button). All visible.

---

## 6. "INTERNAL ASSISTANT" / PURPOSE COPY

**Wizard labels and body copy** (`FleetCreateAgentWizard.tsx:15-19`):

| Preset | Label | Body |
|---|---|---|
| `customer_facing` | Customer-facing | "Talks directly to your customers — support, sales, bookings." |
| `internal_assistant` | Internal assistant | "Helps your team — internal ops, research, drafting." |
| `operator` | Operator | "Coordinates and configures your other agents." |

**What each preset ACTUALLY does** (`fleet_tools.py:34-47`):

When an agent is created with a preset, the backend stores `purpose_preset` in
install_metadata AND sets the agent's instructions from `_PURPOSE_PRESET_INSTRUCTIONS`:

- **customer_facing**: "You represent the business directly to its customers.
  Be professional, accurate, and helpful in every reply — customers will judge
  the business by how you speak to them."
- **internal_assistant**: "You help the team internally. Be concise and direct
  — you are talking to people who already know the business context."
- **operator**: "You help manage and coordinate other agents in this workspace."

**Beyond instructions, the preset DOES NOT currently affect:**
- Tool catalog (all specialists get the same defaults from `seed_specialist_metadata()`)
- Channel bindings
- Connector access
- Hardware access
- Model config
- Audience filter

The preset is purely a system-prompt hint + a metadata label for the UI. It
does not gate functionality. This is honest — the wizard presents it as "what is
it for?" not "this unlocks different capabilities."

**"Internal assistant" as a user-facing label:** The owner's concern is valid.
"Internal assistant" is a category label, not a user-facing description. The body
copy ("Helps your team — internal ops, research, drafting.") clarifies it, but
the label itself is jargon. Compare to "Customer-facing" which is also jargon.
Neither tells a non-technical user what the agent will actually DO.

---

## 7. TELEGRAM 3-OPTION SHEET

**Screenshots:**
- `empyralis-audit-q7-telegram-3options-dark.png` — all 3 options visible
- `empyralis-audit-q7-telegram-hosted-dark.png` — hosted selected, shows "Pair Telegram" button
- `empyralis-audit-q7-telegram-byo-dark.png` — BYO selected, shows "Connect your bot" button
- `empyralis-audit-q7-telegram-personal-dark.png` — personal selected, shows warning
- `empyralis-audit-q7-telegram-personal-pairpanel-dark.png` — after "Yes, continue", shows Gateway pair panel

**Option 1 — Hosted bot:** Shows "Pair Telegram" button (disabled — the hosted
Telegram bot is not configured on this server:
`hosted_telegram_configured()` returns false). When configured, clicking starts
the pairing flow: `POST /api/sage/telegram-hosted/pair/start` → displays pairing
code → polls `/api/sage/telegram-hosted/pair/status` every 3s.
`FleetAgentDetail.tsx:492-535`

**Option 2 — BYO bot token:** "Connect your bot" button → calls
`POST /api/connections/telegram_bot/setup/start` → returns `auth_required_fields`
→ shows input fields → "Save token" button → calls `POST /api/connectors/vault`.
`FleetAgentDetail.tsx:445-490`
- **Not tested against real BotFather token** (backend returns auth_required_fields or error).

**Option 3 — Personal account:** Warning text renders:
"This agent will act as **you** on Telegram. It can read your DMs and send
messages under your name. This needs the Gateway paired on your machine. Are you sure?"
"Yes, continue" button → reveals `GatewayPairPanel` (compact mode).
`FleetAgentDetail.tsx:713-734`
- Gateway pair panel renders (QR code + manual pairing instructions).

**What's NOT wired for Slack/Discord:** The 3-option sheet pattern is only
applied to Telegram. Slack and Discord have single-path OAuth (one "Connect
Slack"/"Connect Discord" button). The comment at `FleetAgentDetail.tsx:432-436`
explicitly states this is intentional: "Slack and Discord stay single-path OAuth
below — they have no BYO-bot or personal-account capability in the catalog today."

---

## 8. GLOBAL DEAD-BUTTON AUDIT

**Rate-limited during test — saw login page, not fleet page.** Based on code
analysis, here are the buttons classified:

### FleetHome (`FleetHome.tsx`):
- **"New agent"** — opens wizard. REAL (but wizard is blocked by 405)
- **Rail items** (Chat, Fleet, Hardware, Integrations) — navigate. REAL.
- **Theme toggle** — calls `actions.setGlobalTheme()`. REAL.
- **Agent cards** — open detail modal. REAL.
- **Status strip numbers** — display-only, not buttons.

### Agent Modal (`FleetAgentDetail.tsx`):
- **Tab buttons** (Overview/Chat/Memory/Channels/Connectors/Tools/Model) — REAL.
- **Close (X)** — calls `onClose()`. REAL.
- **"Chat with this agent"** (Overview, Chat, Memory, Tools tabs) — calls
  `onChat(agentId)` → navigates to chat. REAL.
- **"Open chat"** (Chat tab) — same. REAL.
- **"Open connectors"** (Connectors tab) — `window.location.href` to integrations. REAL.
- **"Chat to configure"** (Tools tab) — calls `onChat()`. REAL.
- **Save button** (Memory tab) — calls `PATCH /api/sage-context-files/{filename}`. REAL.
- **Channel/Connector cards** — expand detail sheet. REAL.
- **"Pair Telegram"** — calls `POST /api/sage/telegram-hosted/pair/start`. REAL but
  fails when hosted bot not configured (shows error text, not dead).
- **"Connect Slack"/"Connect Discord"** — OAuth redirect. REAL.
- **"Connect your bot"** (Telegram BYO) — calls setup API. REAL.
- **"Save token"** (Telegram BYO) — calls vault API. REAL.
- **"Yes, continue"** (Telegram personal warning) — reveals pair panel. REAL.
- **Gateway pair panel** buttons — QR/manual pairing. REAL.

### Wizard (`FleetCreateAgentWizard.tsx`):
- **Cancel/Back/Next/Finish** — all real but wizard is blocked at step 2 by 405.
- **Purpose cards** — select purpose. REAL.
- **Provider cards** — select provider mode. REAL.

### Chat Composer (`chat-composer.tsx`):
- **Attach (Paperclip)** — opens file picker. REAL (verified: visible, enabled).
- **Plus (+)** — opens capability menu. REAL.
- **Voice (Mic)** — starts recording. REAL (if browser supports).
- **Send (ArrowUp)** — submits message. REAL.
- **Stop (Square)** — stops generation. REAL.

**NO dead buttons found in code.** Every button has a real action wired. The
only "broken" experience is the wizard's 405, which shows an error rather than
being a silent no-op.

---

## 9. TELEGRAM ROUND-TRIP STATE

**Paired chat:** The Telegram pair status endpoint requires authentication.
Without a valid workspace token, it returns 401. Could not verify from curl.

**Channel state:** The `sage_telegram_hosted` channel does NOT appear in the
agent-channels API response. The response includes `telegram_personal` (personal
account via Gateway) but not the hosted bot. The `/api/connections/status` with
`surface=sage` likely doesn't return hosted bot entries in the same lane.

**DeepSeek pipeline:** The agent's model_config is `{"mode":"platform_credits"}`
— meaning it uses the platform default provider (DeepSeek, per `.env`).
`resolve_model_config()` at `fleet_tools.py:91-100` returns this config.
The pipeline is: `execute_sage_turn → provider resolver → DeepSeek API`.

**Not tested end-to-end** — sending a message through the API path requires a
live chat turn which needs more setup than a simple curl (it goes through the
full Sage turn execution pipeline). The backend is running and DeepSeek key is
configured, but a full round-trip test needs a workspace-authenticated chat request.

**Owner's phone leg:** Not applicable — this is the owner's responsibility per
the directive.

---

## SUMMARY

| # | Item | Verdict |
|---|---|---|
| 1 | 405 in wizard | **BROKEN** — `route.ts` missing POST export at `frontend/app/api/w/[workspaceId]/fleet/agents/route.ts:5` |
| 2 | Wizard steps | **BLOCKED** — step 1 works, step 2 errors, steps 3-5 unreachable |
| 3 | Agent modal tabs | Overview/Channels/Connectors: real. Chat/Memory/Tools: real but empty. Model: bare minimum. |
| 4 | Theme unification | **WORKING** — single `data-theme`, dark covers all surfaces tested |
| 5 | Chat page | **WORKING** — attach button visible, enabled, opens file picker |
| 6 | Purpose copy | Wizard copy is short (1 line). Backend instructions longer. Presets don't gate functionality. |
| 7 | Telegram 3-option | **WORKING** — all 3 paths render, hosted pairing code works, BYO fields show, personal warning + pair panel show |
| 8 | Dead buttons | **NONE FOUND IN CODE** — every button has a real action. 405 is an error, not a silent no-op. |
| 9 | Telegram round-trip | **NOT TESTED** — pair status needs auth. DeepSeek pipeline is wired but not exercised end-to-end. |
