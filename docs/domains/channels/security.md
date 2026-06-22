# Channels Security

Status: Active
Owner: Platform
Last verified: 2026-06-06
Source of truth: channel routes, services, and tests

## Lane Enforcement

`server_modules/channel_lane_contract_service.py` defines personal gateway
channels, Studio connector channels, reserved private runtime items, and public
Studio webhook paths. Personal routes must start with `/personal-channels/`.
Public Studio webhook wrappers must match known webhook paths.

Studio channel binding rejects personal Agent Computer channels and personal
accounts. Source: `server_modules/channel_platform_service.py`.

## Personal-Channel Access

Every personal-channel route requires API auth, asserts the personal route
path, loads the gateway registration, enforces workspace access, and checks
gateway owner user id when present. A registration with no owner id is usable
only by owner/admin roles. Source: `server_modules/routes_personal_channels.py`.

Personal configure and send paths enforce Rust gateway service decisions before
gateway execution or approval request. Manual personal sends are marked
critical and require owner approval before dispatch. Sources:
`server_modules/routes_personal_channels.py` and
`server_modules/personal_channels_service.py`.

Personal dispatch also checks kill switch state and gateway-advertised channel
support/health before inbound handling. Source:
`server_modules/personal_channels_service.py`.

## Studio Webhook Authentication

The lane contract lists public Studio webhook paths for Telegram, Slack, GitHub, and Discord.
A legacy Twilio WhatsApp webhook path may exist in code but WhatsApp is not a supported channel;
that path must not be exposed or marketed. Connector code contains provider-specific
webhook auth/signature handling; for example GitHub requires a configured
webhook secret and valid signature, Discord rejects missing or invalid signature
headers, and Slack routes verified event payloads into the channel router.
Sources: `server_modules/channel_lane_contract_service.py` and
`server_modules/connectors_actions.py`.

Tests outside the required prompt also cover public webhook wrappers and
provider signatures, including
`server_modules/tests/test_routes_connectors_security_boundary.py`,
`server_modules/tests/test_connectors_actions_discord.py`, and
`server_modules/tests/test_slack_connector.py`.

## Secret Boundary

Studio channel accounts store raw credentials in the vault connector path and
return only `account_ref` and `secret_ref` projections. Metadata is sanitized
and sensitive keys such as token, secret, password, session, and refresh token
are removed. Source: `server_modules/channel_platform_service.py`.

Personal session/auth material belongs to the paired gateway/Agent Computer
lane. Cloud state stores safe projections and message records, not the local
session files. Source: `docs/domains/channels/personal-vs-studio-channel-model.md` and
`server_modules/personal_channels_repository.py`.

## Replay And Rate Limits

Personal inbound replay protection is implemented by SQLite uniqueness on
`gateway_id`, `channel_key`, and `external_message_id`. Personal outbound uses
unique `gateway_id`, `channel_key`, and `idempotency_key`. Source:
`server_modules/personal_channels_repository.py`.

Business inbound replay/idempotency is implemented in the channel event journal
and duplicate check before execution. Business routing also checks concurrency,
deployment pause, incident state, and daily quota before execution. Source:
`server_modules/agent_channel_router.py`.

Not implemented in the inspected personal-channel code: a timestamp/signature
replay window for gateway channel payloads beyond gateway session/Rust gate and
message idempotency checks.
