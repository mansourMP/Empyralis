# Business Channels

Status: Active
Owner: Platform
Last verified: 2026-06-10
Source of truth: connection catalog, routes, deployed agent channel services,
and channel platform services

## Studio Channel Catalog

`server_modules/connection_catalog_service.py`,
`server_modules/routes_connections.py`,
`server_modules/channel_lane_contract_service.py`, and
`server_modules/channel_platform_service.py` define the business/Studio and
cloud connector catalog. Launch-capable business messaging channels in the
inspected catalog include:

- `telegram_bot` / binding `telegram`: Telegram Bot API, live capable,
  launch allowed.
- `discord_bot` / binding `discord`: Discord Bot, live capable, launch allowed.
- `slack`: Slack App, live capable, launch allowed.

Launch-capable connected-app style channels include `google_workspace`,
`microsoft_365`, `github`, `linear`, `notion`, `dropbox`, `s3`, `smtp`,
`wechat_work`, and `instagram_business`. They are categorized as work systems
or connected apps, not necessarily customer chat surfaces.

Partial or roadmap business channels in the inspected catalog include
`web_chat`, the generic `email` channel, `smtp_imap` as a channel-platform
record, `whatsapp_business` / `whatsapp_twilio`, `teams`, `matrix`, and
`apple_messages_business`, depending on `live_capable`, `runtime_usable`, and
`launch_allowed` flags. `smtp` as a work-app connector is live when configured;
the generic email channel remains partial. Web Chat is explicitly not
launch-ready in `docs/reports/web-chat-channel-audit.md`.

## Cloud Setup Model

Cloud channels and connected apps do not require Agent Computer. Setup starts
through `POST /api/connections/{connection_id}/setup/start`.

- OAuth or app-install connections return an OAuth setup response when
  `connection_oauth_service` has a provider mapping.
- Manual-token, bot-token, webhook, SMTP, and API-key connections return
  `/api/connectors/vault` with the required credential fields from the
  canonical connection catalog.
- `POST /api/connections/{connection_id}/verify` currently verifies
  `telegram_bot` and `discord_bot`. Other cloud connectors may still connect
  and run through their connector services, but they do not yet have the same
  generic verify endpoint.

Canonical no-gateway setup examples:

| Connection | Setup kind | Required credential shape |
| --- | --- | --- |
| `telegram_bot` | `bot_token` | `bot_token`, `chat_id` |
| `discord_bot` | `bot_install` | `bot_token`, `channel_id`, `guild_id`, `application_id`, public key fields |
| `slack` | `oauth` | Slack OAuth/token fields |
| `google_workspace` | `oauth` | Google access token via OAuth for Gmail and Calendar; Drive requires explicit scope opt-in |
| `microsoft_365` | `oauth` | Microsoft access token via OAuth |
| `notion` | `oauth` | integration token or OAuth access token |
| `linear` | `oauth` | API key or OAuth access token |
| `dropbox` | `oauth` | access token |
| `smtp` | `smtp_imap_credentials` | host, port, username, password, TLS flag |
| `wechat_work` | `webhook_url` | webhook URL |
| `instagram_business` | `graph_api_token` | access token, Instagram account id, page id |

Hosted Sage Telegram is modeled as `sage_telegram_hosted`, a separate planned
canonical connection item. The current launch-capable cloud Telegram entry is
still `telegram_bot`, which represents a customer bot-token connector.
`sage_telegram_hosted` must not be confused with `telegram_personal` and must
stay planned until official bot provisioning, pairing, signed inbound events,
outbound replies, approvals, rate limits, and replay tests are certified.

Channel-origin setup remains platform-owned. Telegram users who start
from a bot or channel before linking should receive an Empyralis continue link,
not chat-command setup instructions. After sign-in/signup, the continue intent
lands on the workspace Connectors surface; provider credentials, relinking,
OAuth, and revocation stay in the web/native product.

## Certified Versus Catalog-Listed Apps

Do not treat every `live_when_configured` work-app card as certified. The
connection catalog exposes `readiness_status`, `certification_required`, and
`certification_requirements`; `/api/connections/{id}/doctor` and
`/api/connections/{id}/certify` are the backend proof gates. Focused
certification tests still need to prove providers whose catalog entry is live
but whose runtime module is not present.

Certified/implemented enough to document as launchable now:

- Core cloud channels: `telegram_bot`, `slack`, `discord_bot`
- Core work apps: `google_workspace`, `microsoft_365`, `github`, `notion`,
  `linear`, `dropbox`, `s3`, `smtp`, `wechat_work`, `instagram_business`

Catalog-listed but not yet runtime-certified in the inspected repo:

- `figma`, `todoist`, `airtable`, `canva`, `asana`, `hubspot`, `zoom`,
  `calendly`, `clickup`, `jira`, `stripe`, `salesforce`, `webflow`, `monday`,
  `gitlab`, `bitbucket`, `confluence`, `miro`, `mailchimp`, `pipedrive`,
  `intercom`, `docusign`, `square`, `typeform`, `quickbooks`, `xero`,
  `freshbooks`, `vercel`

These should either gain real connector/runtime modules and tests, or their
catalog status should be downgraded before they are exposed as launch-ready.

## Apple Messages For Business

`apple_messages_business` is the official Apple Messages lane. It is a planned
business channel, not a personal iMessage API and not an Agent Computer bridge.
It is modeled as a cloud/MSP business messaging adapter with:

- user-initiated conversations
- mandatory AI disclosure
- mandatory human handoff
- MSP-backed inbound/outbound transport
- proof-log fields for transcript, approvals, handoff state, and outbound
  message ids

The adapter contract lives in
`server_modules/business_messaging_channel_adapter_service.py`. The connection
catalog keeps setup disabled until Apple Business identity, MSP credentials,
privacy/support review materials, and launch testing are complete.

## Channel Accounts And Bindings

Studio channel accounts are projected from vault connectors as
`vault:{credential_id}` account refs with `vault://credential/{id}` secret refs;
raw secrets are redacted from account projections. Source:
`server_modules/channel_platform_service.py`.

`upsert_agent_channel_binding()` loads a deployed agent in the authenticated
tenant/workspace, validates the selected catalog item and account provider,
then persists a per-agent endpoint key such as `agent:{deployed_agent_id}:telegram`
into the deployed agent's `channels` field. It also saves specialist channel
bindings when a backing install exists and emits a security audit event. Source:
`server_modules/channel_platform_service.py`.

Studio cannot bind personal Agent Computer channels. The platform service
rejects personal runtime lane items and rejects accounts whose runtime lane is
`personal_gateway`. Source: `server_modules/channel_platform_service.py`.

## Routing

Inbound business messages route to deployed Studio agents through
`server_modules/agent_channel_router.py`. The router resolves a public channel
owner, enforces the Rust deployed-agent public-route gate, records inbound
events, checks duplicates, pause/incident state, daily quota, applies memory and
business plan overlays, executes the canonical channel turn, records outbound
events, persists memory snapshots, and records channel activity.

## Test Sends

`test_agent_channel_binding()` is dry-run only. Live test sends from Studio are
blocked by a `ChannelPlatformError`. Source:
`server_modules/channel_platform_service.py`.

Not launch-certified in the inspected code: a live Studio Web Chat widget
launch flow; durable generic Email ingress marked launch-ready; Apple Messages for Business
dispatch through a real MSP account; the `sage_telegram_hosted` runtime, pairing, and official bot
operations path.

Note: WhatsApp Business (`whatsapp_business` / `whatsapp_twilio`) is not a viable launch path.
Meta bars third-party general-purpose AI assistants from WhatsApp Business Cloud API as of
Jan 15 2026. Do not present WhatsApp Business as a planned or upcoming Studio channel.
