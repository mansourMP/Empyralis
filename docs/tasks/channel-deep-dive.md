# Channel Deep Dive — Setup Methods, Lanes, One-Click vs Hardware, Rules

> **OUTDATED (2026-07-23):** this reference doc is from 2026-06-30 — its
> ground has since been re-covered, more currently, by
> `docs/design/reliability-audit-2-channels.md`, `docs/design/gap-personal-channels.md`,
> and `docs/design/gap-bot-channels.md`. It also predates the 2026-07-23
> founder ruling that "Sage" is dead product terminology (the platform has
> only agents — owner-facing, customer-facing serving the owner, and AskAI);
> this document uses "Sage" throughout as a live concept. Kept for history;
> do not build from this.

**Created:** 2026-06-30
**Source:** Code audit + `channel_lane_contract_service.py` + frontend connectors pane + graphify graph
**Status:** reference doc — pending your review before creating tasks

---

## 1. The rule: Channels are pigeons

From `platform_event.py:5`:
> The channel layer NEVER speaks as the agent. It is a transport pigeon.

From `PLATFORM.md` (Section 7, Decision 1):
> Channels never hold routing logic, policy, or session state. The control plane is the single brain — channels are dumb pipes. Adding a channel becomes a thin translation layer, not a feature reimplementation.

A channel does exactly three things:
1. **Receive** inbound messages → normalize to `AgentTurnRequest`
2. **Deliver** outbound `AgentTurnResponse.reply` or `PlatformEvent.channel_text`
3. **Report** delivery status

That's it. No parsing commands, no enforcing quotas, no managing sessions.

---

## 2. Channel taxonomy — two families, three runtime lanes

### Family 1: Studio Business Channels (cloud bots)

| Channel | Connector ID | Stage | Setup method | Hardware needed? |
|---------|-------------|-------|-------------|:---:|
| **Telegram Bot** | `telegram_bot` | **Live** | Bot token paste → webhook auto-set | No |
| **Discord Bot** | `discord_bot` | **Live** | Bot token + OAuth2 → auto-join server | No |
| **Slack App** | `slack` | **Live** | App manifest + OAuth → auto-install | No |
| **WhatsApp Business** | `twilio_whatsapp` | Roadmap | Twilio setup + webhook | No |
| **Email (Gmail)** | `google_workspace` | Partial | OAuth consent → mailbox watch | No |
| **Email (SMTP/IMAP)** | `smtp` | Partial | Server credentials + IMAP IDLE | No |
| **Web Chat Widget** | `web_widget` | Roadmap | Embed script → instant | No |
| **Apple Messages for Business** | `apple_messages_business_msp` | Roadmap | MSP registration | No |

**Runtime lane:** `studio_business_connector` — cloud-only, no Gateway required.
**Session owner:** `cloud_connector` — Empyralis manages the session.
**Agent surface:** Studio specialists (business agents), not Sage personal.

### Family 2: Personal Channels (user's own accounts, via Gateway)

| Channel | Bridge | Stage | Setup method | Hardware needed? |
|---------|--------|-------|-------------|:---:|
| **Telegram Personal** | `telegram_gramjs` | **Live** | QR login → Gateway bridge | **Yes** |
| **WhatsApp Personal** | `whatsapp_baileys` | **Live** | QR login → Gateway bridge | **Yes** |
| **Discord Personal** | `discord_bot` | **Live** | Bot token (but runs via Gateway) | **Yes** (contradicts lane) |
| **Signal** | `signal_local_bridge` | Planned | Bridge config → Gateway | **Yes** |
| **iMessage** | `bluebubbles_local_bridge` | Planned | BlueBubbles server + Gateway | **Yes** (Mac required) |
| **WeChat** | `wechat_local_bridge` | Planned | Bridge config → Gateway | **Yes** |

**Runtime lane:** `personal_gateway` — requires Empyralis Gateway on user hardware.
**Session owner:** `paired_gateway` — user's machine manages the session.
**Agent surface:** Sage (personal assistant), not Studio.

### Family 3: Sage Hosted (the shortcut)

| Channel | Route file | Stage | Setup method | Hardware needed? |
|---------|-----------|-------|-------------|:---:|
| **Sage Telegram** | `routes_sage_telegram_hosted.py` | **Live** | Bot token paste → `/start` | No |
| **Discord DM** | via `routes_personal_channels.py` | **Live** | Bot invite → DM the bot | No |
| **Web Chat** | via `direct_chat_*_service.py` | **Live** | Login → chat widget | No |

**What's different:** Sage Hosted bypasses the Studio channel system entirely. It's a direct path: channel → `agent_turn.py` → Sage. No manifest, no specialist binding, no runtime profile. This is the "just works" path.

---

## 3. Setup experience — the "one-click" problem

### What "one-click" actually means per channel

| Channel | Current setup | Ideal one-click |
|---------|--------------|-----------------|
| **Sage Telegram** | Copy bot token from @BotFather → paste in dashboard → click "Set Webhook" → user messages `/start` on Telegram | **Already close.** Could be: "Create bot" button → OAuth Telegram → auto-create bot + set webhook. User opens Telegram, presses Start. |
| **Discord Bot** | Create app in Discord Developer Portal → copy token → paste → OAuth2 URL generated → admin clicks to add bot to server | Could be: "Add to Discord" button → OAuth → auto-create app + install bot + join server. One flow. |
| **Telegram Personal** | Install Gateway → QR login with GramJS → Gateway bridges to cloud | Gateway is the bottleneck. Can't be one-click until Gateway is one-click. |
| **WhatsApp Personal** | Install Gateway → QR login with Baileys → Gateway bridges to cloud | Same — Gateway required. |
| **Slack App** | Create app manifest → install to workspace → OAuth | Already has manifest. Could be "Install to Slack" button → OAuth flow. |

### The bottleneck: Gateway channels can't be "one-click"

Personal channels (Telegram Personal, WhatsApp, Signal, iMessage, WeChat) all need the Gateway running on user hardware. The Gateway is a separate install:
1. Download Gateway binary
2. Install on Mac/Windows/Linux
3. Authenticate to Empyralis cloud
4. Bridge local channel (QR login, config file, etc.)

This is inherently multi-step. The one-click promise only works for cloud bot channels.

### The shortcut that already works

**Sage Telegram Hosted** (`routes_sage_telegram_hosted.py`) is the closest thing to one-click today:
1. User creates bot with @BotFather (Telegram's bot creation tool)
2. Pastes token into Empyralis dashboard
3. Empyralis auto-sets webhook
4. User opens Telegram, messages `/start` → connected

Steps 1-2 could be collapsed: "Create Telegram Bot" button in dashboard → OAuth → Empyralis calls Telegram API to create bot → auto-configures webhook → user gets a link to their bot. That's ~2 minutes.

---

## 4. Channel routing rules

### Rule 1: Sage is the default

Any inbound message that doesn't match a specialist-bound channel routes to Sage. Sage is the fallback, not a choice.

### Rule 2: Personal channels = Sage only

Personal channels (Telegram Personal, WhatsApp, Signal, iMessage, WeChat) bind to Sage only. The integration settings panel (`deployed-agents/integration-settings.tsx:269`) explicitly shows "Sage only" for `personal_gateway` lanes. Specialists cannot receive messages from personal channels.

### Rule 3: Studio channels = specialists

Studio business channels (Telegram Bot, Discord Bot, Slack, Email) bind to specialists. These are customer-facing business agents, not personal assistants.

### Rule 4: Discord is split

`discord_personal` has a metadata contradiction (`channel_lane_contract_service.py:118`):
- `runtime_lane`: `personal_gateway` (implies hardware)
- `session_owner`: `cloud_connector` (implies cloud)

This is documented as PLATFORM.md Violation #13. Discord DM works today via the cloud connector path — it's effectively a hosted bot that happens to also have a personal channel definition.

### Rule 5: Web chat is both

Web chat has two paths:
1. **Legacy web handler** (`direct_chat_response_service.py`) — Sage direct, no routing
2. **Studio channel** — planned (`stage: "roadmap"`) for business agents

---

## 5. The channel pairing system

`workspace-channel-pairing-surface.tsx` implements the pairing UI. The flow:

```
User clicks "Connect Channel" → selects provider
    │
    ├── Cloud bot (Telegram/Discord/Slack)
    │   └── OAuth or token paste → webhook auto-set → link created → "active"
    │
    └── Personal channel (WhatsApp/Signal/etc.)
        └── Gateway required → QR/config displayed → Gateway bridges → link created
```

**`CHANNEL_PROVIDER_DEFINITIONS`** (line 59): Currently only `telegram` and `whatsapp` are defined as channel providers. Discord and Slack are missing from this list — they exist as connectors but not as channel pairings.

---

## 6. All channel files

### Backend — channel lane & routing
| File | Role |
|------|------|
| `channel_lane_contract_service.py` | Canonical channel taxonomy: personal specs, studio roadmap, platform catalog |
| `agent_channel_router.py` (95KB) | Routes inbound messages to correct agent surface |
| `channel_transport.py` | Pure transport: deliver bytes, nothing more |
| `channel_sdk.py` | Channel SDK pattern (mirrors OpenClaw plugin-sdk) |
| `personal_channel_sage_bridge_service.py` | Bridges personal channels to Sage |
| `personal_channel_thread_command_service.py` | Thread commands for personal channels (`/new`, `/threads`, `/use`) |
| `personal_channels_service.py` | Gateway-channel state sync |
| `business_messaging_channel_adapter_service.py` | Business channel adapter |

### Backend — route files
| File | Status |
|------|--------|
| `routes_sage_telegram_hosted.py` | **Live** — Sage Telegram hosted bot |
| `routes_personal_channels.py` | **Live** — personal channel CRUD + inbound |
| `routes_connectors.py` | **Live** — connector/channel setup API |
| `routes_slack.py` | **Dead** — imported, never mounted |
| `routes_imessage.py` | **Dead** — mounted, no bridge |
| `routes_signal.py` | **Dead** — mounted, no bridge |
| `routes_wechat.py` | **Dead** — mounted, bridge planned |

### Backend — connectors (58 files in `connectors/`)
| File | Role |
|------|------|
| `connectors/telegram_connector.py` | Telegram Bot API integration |
| `connectors/discord_connector.py` | Discord bot integration |
| `connectors/whatsapp_connector.py` | WhatsApp Business API |
| `connectors/slack_connector.py` | Slack app integration |
| `connectors/signal_connector.py` | Signal bridge |
| `connectors/imessage_connector.py` | iMessage/BlueBubbles bridge |
| ... | 52 more connectors for apps/MCP, not channels |

### Frontend
| File | Role |
|------|------|
| `workspace-channel-pairing-surface.tsx` | Channel pairing UI (connect/disconnect flows) |
| `workstation-sage-connectors-pane.tsx` (7,800+ lines) | Connector/channel definitions, setup forms, status |
| `workstation-chat-pane-model.ts` | Runtime lane resolution for chat model selection |
| `deployed-agents/integration-settings.tsx` | Channel-to-agent binding UI |

---

## 7. What ships vs what waits

### Ship v1 (already working)
| Channel | Type | Setup time target |
|---------|------|-------------------|
| **Sage Telegram Hosted** | Cloud bot | ~2 min (token paste) → **target 30 sec** (OAuth auto-create) |
| **Discord Bot** (DM path) | Cloud bot | ~3 min (developer portal + token + OAuth) |
| **Web Chat** | Cloud native | Instant (just log in) |

### Ship v1.1 (near-ready, needs wiring)
| Channel | Blocker |
|---------|---------|
| **Slack App** | Route is dead — needs remount + OAuth wiring |
| **Telegram Bot (Studio)** | Needs specialist binding tested |
| **Discord Bot (Studio)** | Needs specialist binding tested |

### Post-ship (needs Gateway)
| Channel | Blocker |
|---------|---------|
| **Telegram Personal** | Gateway one-click install |
| **WhatsApp Personal** | Gateway + QR bridge |
| **Signal** | `signal_local_bridge` doesn't exist yet |
| **iMessage** | BlueBubbles setup is inherently Mac-only + multi-step |

---

## 8. The one-click rule

> **A channel qualifies as "one-click" if the user can go from "I want this" to "my agent is in this channel" in under 2 minutes without leaving the dashboard.**

Channels that qualify today: **0**
Channels that could qualify with OAuth wiring: **Sage Telegram, Discord Bot, Slack** (3)
Channels that can never qualify (require hardware): **Telegram Personal, WhatsApp Personal, Signal, iMessage, WeChat** (5)

The fastest path to a one-click demo: wire Telegram OAuth bot creation → user clicks "Create Telegram Bot" → Empyralis calls Telegram API → bot created + webhook set → user gets a `t.me/MyAgent` link. ~30 seconds.
