# Sage Channels

Status: Active
Owner: Platform
Last verified: 2026-06-10
Source of truth: connection catalog, channel routing code, and personal-channel
routes

## Web And Mobile Chat

The Sage API accepts `chat`, `mobile`, `web`, `desktop`, and `voice` surfaces in
`server_modules/sage_agent_runtime_contract.py`. `server_modules/sage_chat_api.py`
normalizes the surface and calls `handle_sage_chat()`.

The React workstation chat pane uses the canonical direct-chat/session stream
client rather than posting directly to `/api/sage/chat`. It creates a session,
persists the user turn, streams trace/step/chunk/final events, renders the
assistant reply, then refreshes canonical thread state. Source:
`frontend/lib/workspace/workstation-chat-pane.tsx`.

## Personal Channels To Sage

Personal channels are Sage/Agent Computer channels, not Studio channels. The
implemented personal keys are:

- `telegram_personal` via `telegram_gramjs`
- `whatsapp_personal` via `whatsapp_baileys`
- `signal_personal` via `signal_local_bridge`
- `imessage_personal` via `bluebubbles_local_bridge`
- `wechat_personal` via `wechat_local_bridge`

Technically-implemented personal keys (not recommended as default paths):

- `telegram_personal` — Advanced/fragile. Recommended Telegram path is the hosted bot (`telegram_bot` / `sage_telegram_hosted`).

**Dead / unsupported — do not expose to customers:**

- `whatsapp_personal` — WhatsApp Baileys is banned. WhatsApp Business API bars third-party AI assistants as of Jan 15 2026. This lane must not be offered or marketed.

Bridge-contract keys with backend/gateway plumbing but locked customer setup:

- `signal_personal`
- `imessage_personal`
- `wechat_personal`

Sources: `server_modules/connection_catalog_service.py`,
`server_modules/channel_lane_contract_service.py`,
`server_modules/routes_personal_channels.py`, and
`server_modules/personal_channels_service.py`.

Personal inbound messages enter from the paired gateway. The handler requires
`external_message_id`, `remote_jid`, and text; ignores `from_me`; records the
inbound message; calls the Sage personal-channel bridge; creates or reuses an
outbound pending record; then asks for owner approval before dispatch. Sources:
`server_modules/personal_channels_service.py` and
`server_modules/personal_channel_sage_bridge_service.py`.

## Business Channels Are Studio

Business/customer channels route to deployed Studio agents through
`server_modules/agent_channel_router.py` and connector/webhook services. They
do not become Sage turns unless a product decision explicitly routes them to
Sage. `server_modules/channel_platform_service.py` rejects binding personal
Agent Computer channels to Studio cloud agents.

Not implemented in the inspected code: attachment, voice, image, or file
payloads normalized into personal-channel Sage turns. The inbound handlers
require text and do not pass media payloads into Sage.

## Cloud Channels

Cloud channels do not require Agent Computer. `telegram_bot` is the Telegram
path for a bot account and `telegram_personal` is the personal-account path.
Sage channel setup surfaces should expose one Telegram card with Bot/Personal
choice inside the setup panel. The product copy must not imply that all
Telegram usage depends on gateway hardware:

- `telegram_bot`: cloud Telegram Bot API, no Agent Computer required.
- `telegram_personal`: personal Telegram account, selected Agent Computer
  required.

Use the personal lane only when the user intentionally wants Sage to use their
own logged-in account. Use the bot lane for the simpler Telegram entry point.
If the product wants one official Empyralis Telegram bot that every customer can
message without bringing a bot token, use the separate `sage_telegram_hosted`
connection. It is planned until official bot provisioning, pairing, signed
inbound events, outbound replies, approvals, rate limits, and replay tests are
certified. Do not overload `telegram_personal` for that purpose.

For all cloud/personal message surfaces, setup stays inside Empyralis. Telegram
may send a platform connect link when a user starts a channel before linking,
but it must not ask the user to configure Sage by chat commands. The
`/continue?source=channel_connect...` handoff preserves the channel intent
through login/signup and routes the user back to the workspace Connectors
surface. (WhatsApp is not a supported channel.)

Launchable channel setup surfaces:

- `telegram_bot`: cloud Telegram Bot API. The setup panel collects the Telegram
  bot token and target chat ID / channel username. It must not ask for Agent
  Computer.
- `telegram_personal`: personal Telegram account through the selected Agent
  Computer. It must use the stepped phone / code / password flow returned by
  the gateway.
- `whatsapp_personal`: **NOT SUPPORTED.** WhatsApp Baileys is banned and WhatsApp Business API bars
  third-party AI assistants. Remove from any launchable setup list.
- `slack`: cloud Slack bot/user-token connector. The setup panel collects the
  connector credentials that Sage or Studio can use for workspace messaging.
- `discord_bot`: cloud Discord bot connector. The setup panel collects bot,
  channel, guild, application, and public-key fields needed by the Discord
  connector contract.

Launchable no-gateway app connectors:

- `google_workspace`: Gmail and Calendar through one Google OAuth connection.
  Google Drive is optional and must be enabled only when Drive is also
  verification/demo-ready.
- `microsoft_365`: Outlook mail, calendar, and OneDrive through Microsoft
  OAuth.
- `github`: OAuth or app install for repository work and signed webhooks.
- `notion`, `linear`, and `dropbox`: OAuth/token-backed work-app connectors.
- `smtp`: custom mailbox credentials through the work-app connector, not the
  partial generic `email` channel.
- `wechat_work`: webhook URL backed work-app connector.
- `instagram_business`: Graph API token backed work-app connector.

For cloud connectors, `/api/connections/{connection_id}/setup/start` either
returns an OAuth start response or a `/api/connectors/vault` setup contract
with required fields. Agent Computer is not involved.

The broader app catalog also contains many `live_when_configured` cards that are
not yet runtime-certified by connector-module proof. Those should not be sold as
done until each has a concrete connector runtime and focused tests.

Email is shown as one channel entry but is backed by app connectors:

- Gmail uses the `google_workspace` connector.
- Custom mailbox setup uses the `smtp` connector.
- The partial `email` catalog item must not be used as the visible channel
  setup truth while Gmail/SMTP are the actual launchable paths.

Disabled bridge channels:

- `signal_personal`
- `imessage_personal`
- `wechat_personal`

These stay visible only as disabled bridge lanes until their Agent Computer
runtime bridges are certified end to end. The UI may open the real Agent
Computer bridge setup contract returned by
`POST /api/connections/{id}/setup/start`, but it must show certification
required and must not present the bridge as launch-ready until
`/api/connections/{id}/doctor` and `/api/connections/{id}/certify` show proof.
