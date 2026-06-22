# Personal Vs Studio Channel Model

Last verified: 2026-06-10
Status: Live boundary with implemented lane split

This document freezes the boundary between:
- personal channels owned by the local gateway
- Studio/business channels owned by the cloud connector stack

This distinction is mandatory.
Without it, Empyralis will keep mixing personal local-runtime work with
business webhook infrastructure.

## Two Different Channel Families

| Dimension | Personal channel lane | Studio/business channel lane |
| --- | --- | --- |
| Primary owner | End user on their own device | Workspace/business deployment |
| Canonical process owner | `empyralis-gateway` | `server_modules/connectors/*` |
| Session location | Local device | Cloud control plane / provider-managed webhook integration |
| Auth material | Personal session files / local login state | API keys, bot tokens, webhook secrets, business connector credentials |
| Ingress model | Local session event enters gateway first | Public/provider webhook or cloud polling path |
| Example targets | Telegram personal, Signal, iMessage, WeChat, local browser/app acting as the user (WhatsApp personal is not supported) | Telegram bot, Slack, Discord, GitHub, Notion, Linear, email, phone (Twilio WhatsApp is not supported) |
| Delivery expectation | Feels like the user is acting from their own account | Business/deployed-agent messaging and support flows |
| Failure mode | Device offline or local gateway offline | Cloud connector/webhook/provider offline |

## Frozen Rules

### 1. Personal Channels Terminate At `empyralis-gateway`

If the channel is a personal account session, the first runtime owner must be
the local gateway.

That means:
- personal channel auth/session files live on the device
- reconnect logic lives in the local gateway
- inbound personal messages enter the cloud through the gateway protocol

### 2. Studio/Business Channels Stay In The Connector Stack

Business/API-managed channels remain in:
- `server_modules/connectors/*`
- `server_modules/routes_connectors.py`
- related webhook / poll / outbox services

The existing cloud connector lane remains the right place for:
- Telegram bot / webhook products
- Slack / Discord / GitHub / Notion / Linear / email / phone integrations
- (Twilio WhatsApp is not viable — Meta bars third-party AI as of Jan 15 2026)
- Connected app integrations such as Dropbox, Amazon S3, SMTP / IMAP, WeChat Work, and Instagram Business

### 3. Shared Lower Engine Does Not Remove The Boundary

Both lanes may eventually converge on lower contracts such as:
- canonical run creation
- activity timeline
- approvals
- artifact delivery

That does **not** make them one ingress system.

They still differ in:
- session ownership
- auth material
- reconnect model
- operator expectations
- failure and privacy boundaries

### 4. WhatsApp Is Not A Supported Channel

**WhatsApp is not a supported channel in any form.** Personal WhatsApp (Baileys/QR)
is banned by the provider. WhatsApp Business Cloud API (Twilio or direct) bars third-party
general-purpose AI assistants as of Jan 15 2026.

Historical note: this section previously distinguished "Twilio WhatsApp" (cloud API)
from personal WhatsApp (Baileys). Neither path is viable. Do not build, market, or plan
either path. The analogous Telegram distinction (bot API vs personal MTProto) remains valid;
both Telegram paths are supported.

### 5. Personal Channel Sessions Must Not Depend On The Studio Webhook Stack

Personal channels must not be implemented by stuffing more behavior into:
- `routes_connectors.py`
- existing Telegram webhook bridge stacks (WhatsApp webhook stacks are legacy dead code)
- business connector registries

They need a dedicated local lane behind `empyralis-gateway`.

## Current Repo Mapping

### What Already Exists

Cloud/business lane already exists through:
- `server_modules/routes_connectors.py`
- `server_modules/connectors/telegram_*`
- `server_modules/connectors/whatsapp_*`
- `server_modules/connectors/slack_*`
- `server_modules/connectors/discord_*`

Local capability execution already exists through:
- `empyralis-supervisor`
- `server_modules/supervisor_client.py`

Implemented personal-gateway lane now exists through:
- `empyralis-gateway/src/channels/whatsapp/*`
- `empyralis-gateway/src/channels/telegram/*`
- `empyralis-gateway/src/channels/local-bridge-runtime.ts`
- `empyralis-gateway/src/bridges/bluebubbles-bridge.ts`
- `server_modules/routes_personal_channels.py`
- `server_modules/channel_lane_contract_service.py`

Current implemented personal lane truth:
- personal WhatsApp: gateway lane code exists in the repo but is dead/unsupported — do not
  expose to customers. This channel is not viable (Baileys banned, WhatsApp Business API blocked).
- personal Telegram uses the gateway lane with local session state and reconnect
  ownership
- Signal, iMessage, and WeChat have an Agent Computer local-bridge contract and
  route/service plumbing, but the canonical connection catalog keeps them
  `planned`, `setup_available: false`, and `runtime_usable: false` until bridge
  certification is complete
- the gateway publishes inbound personal messages into cloud through the gateway
  protocol
- outbound personal replies route back through the same gateway control plane

Canonical launch truth:

| Channel | Current state | Gateway required | Customer launch status |
| --- | --- | --- | --- |
| `telegram_personal` | GramJS gateway runtime | Yes | Advanced/fragile — not the recommended customer path; use hosted bot |
| `whatsapp_personal` | Baileys gateway runtime | Yes | **DEAD — DO NOT USE.** Baileys gets banned; WhatsApp Business API bars third-party AI as of Jan 15 2026 |
| `signal_personal` | local bridge contract plus signal-cli bridge code | Yes | Planned/locked until certified |
| `imessage_personal` | local bridge contract plus BlueBubbles bridge code | Yes, Mac | Planned/locked until certified |
| `wechat_personal` | generic local bridge contract | Yes | Planned/locked until certified |

The gateway can project bridge health for Signal/iMessage/WeChat when a
selected Agent Computer reports it, but setup remains locked by
`server_modules/connection_catalog_service.py` and
`server_modules/routes_connections.py` until the canonical catalog is updated
and the certification tests stop asserting the planned state.

### What Still Needs Productization

The repo still needs:
- richer operator UX for pairing, QR/login, doctor, approvals, and resume
- live certification against real personal accounts and reconnect scenarios
- stronger browser-session fidelity for “existing session attach” mode

The current `local_companion` concept is only a partial local-runtime substrate.
The implemented gateway is the forward path that should replace the remaining
`local_companion` assumptions rather than coexist as a second model.

## Memory, Approval, And Policy Boundary

### Personal lane

Default expectations:
- more local/privacy-sensitive
- device-aware availability
- stronger need for local approvals and checkpointing
- personal session data remains local unless policy explicitly allows otherwise

### Studio/business lane

Default expectations:
- cloud-managed availability
- workspace/business memory and deployment policy
- provider/API-managed auth and transport
- no assumption of one specific local device being online

## Migration Rule

Future work must follow this order:

1. freeze the architecture and protocol
2. build `empyralis-gateway`
3. route the supervisor behind the gateway
4. add personal channels to the gateway lane

Current repo truth:
- steps 1 through 4 now have implemented baseline code paths
- future work should focus on productization, live proof, and operator surfaces

Do **not** solve the absence of personal channels by adding more behavior to the
Studio connector stack.

## Historical Rebuild Boundary

The original Phase 0 version of this document froze only the channel-family
boundary and did not implement the gateway, personal
Telegram, or Studio connector rewrites. (Personal WhatsApp was not implemented — it is not supported.)

The live repo now contains baseline gateway and personal-channel
implementations. This document remains the product boundary: personal accounts
belong to the selected Agent Computer gateway lane, while Studio/business
channels remain in the cloud connector lane.
