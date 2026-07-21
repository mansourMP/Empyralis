# Gap Analysis: Bot/Server-Resident Channels — OpenClaw vs Empyralis

Read-only comparison. Scope: Telegram (bot-token), Discord, Slack, WeChat
(Official Account / WeCom), Email — the server-resident/bot channels, as
opposed to the personal full-account channels (WhatsApp, Signal, iMessage,
personal Telegram/GramJS) already covered in `docs/OpenClaw.md`.

Sources: OpenClaw git checkout at `/Users/mansur/openclaw` (real TypeScript
source in `extensions/<channel>/src/`, plus `docs/channels/*.md`) vs
Empyralis `/Users/mansur/empyralis` (`server_modules/*.py`,
`server_modules/connectors/*.py`, `empyralis-gateway/src/channels/*`).
Every claim below is file:line-cited on both sides where source exists; where
OpenClaw's implementation is an external/closed-source plugin (WeChat/WeCom),
that is called out explicitly rather than guessed at.

---

## Headline findings (read this first)

1. **WeChat/WeCom is dead code in production on Empyralis — confirmed, not
   inferred.** `server.py` has zero references to "wechat" (`grep -n wechat
   server.py` → no matches), while the sibling Telegram-hosted router IS
   registered there (`server.py` includes
   `sage_telegram_hosted_router`). `server_modules/routes_wechat_official.py`
   is a complete, never-mounted FastAPI router.
   `server_modules/connection_catalog_service.py:2019-2032` contains an
   explicit in-repo admission: *"routes_wechat_official.py's router is not
   yet registered... No UI/route exposes wechat_official_service.assign_wechat_official"*
   and sets `runtime_usable=False` (`connection_catalog_service.py:2046`).
   A second, independent WeChat implementation exists in
   `empyralis-gateway/src/channels/wechat/` (TS, Official-Account-shaped:
   `signature.ts`, `xml.ts`, `token-manager.ts`) and is *also* never
   imported outside its own directory/tests. The only WeChat-family thing
   actually reachable today is `wechat_work`, an **outbound-only** group-robot
   webhook poster (`connection_catalog_service.py:1971-2003`, explicitly
   "no inbound replies" per its own comment) — not a conversational channel.
   OpenClaw itself has **no in-repo WeChat/WeCom code either** — it delegates
   to two closed-source Tencent npm plugins
   (`@tencent-weixin/openclaw-weixin` for personal QR-login WeChat,
   `@wecom/wecom-openclaw-plugin` for WeCom) neither of which is vendored on
   this machine; the WeCom one has no doc page at all (catalog entry at
   `/Users/mansur/openclaw/scripts/lib/official-external-channel-catalog.json:3-42`
   points to a `docs/plugins/community.md#wecom` anchor that doesn't exist).
   **Net: neither product can hold a real WeChat/WeCom conversation today.**
   Empyralis's gap is closer to done (two full protocol implementations sitting
   one router line + one UI form away from working); OpenClaw's is a bet on a
   third party.

2. **Slack interactivity is switched off at the Slack-app level, not just
   unbuilt.** `slack-app-manifest.json:59-61` sets
   `"interactivity": {"is_enabled": false}` and `"socket_mode_enabled": false`.
   That means buttons, modals, and native approvals aren't a missing code
   path — they're architecturally impossible without a new app manifest and
   a customer-facing re-install/re-authorization. OpenClaw supports both
   transports and a full interactive-components stack
   (`extensions/slack/src/interactive-dispatch.ts`,
   `extensions/slack/src/streaming.ts`, `extensions/slack/src/blocks-render.ts`).

3. **Discord and Telegram-bot are the most mature of the five on Empyralis** —
   both use the real underlying transport (Discord Gateway websocket via
   `discord.py`; Telegram webhook + polling fallback with signature
   verification) and both have genuine, tested reliability work (dedup,
   fail-closed webhook auth, pairing). Their gaps are concentrated in
   **interactive UI** (no inline/component buttons that round-trip a
   callback) and a handful of **outbound reliability gaps** (no rate-limit
   retry on Discord sends, no 401 circuit-breaker on Telegram).

4. **Email is not a channel on either side.** OpenClaw has no
   `docs/channels/email.md` and no email extension — its closest thing is a
   Gmail-specific Pub/Sub webhook → agent-turn pipeline
   (`/Users/mansur/openclaw/src/hooks/gmail.ts`) built on a closed external
   `gog` CLI, itself absent from OpenClaw's own channel list. Empyralis has
   real outbound SMTP send + IMAP pull-fetch
   (`server_modules/connectors/smtp_connector.py`) but **zero inbound
   listener** — independently confirmed by both my own grep and a prior
   in-repo audit comment at `connection_catalog_service.py:444-479`. This is
   a deliberate "connector, not a channel" classification on the Empyralis
   side, not an oversight (`connection_catalog_service.py:468-479`).

---

## Channel 1 — Telegram (bot-token, server-resident)

Empyralis has two bot-token paths: the single shared hosted bot
(`server_modules/sage_telegram_hosted_service.py`,
`server_modules/routes_sage_telegram_hosted.py`) and a per-agent BYO-bot
connector stack (`server_modules/connectors/telegram_*.py` +
`server_modules/connectors/telegram/*.py`). OpenClaw's Telegram is grammY
(Bot API, bot-token auth — confirmed via `package.json`'s
`"grammy": "1.43.0"` dependency), source at
`/Users/mansur/openclaw/extensions/telegram/src/`.

| Feature | OpenClaw (file:line) | Empyralis (file:line) | GAP? | Priority |
|---|---|---|---|---|
| Inline keyboard buttons + callback_query round-trip | `docs/channels/telegram.md:518-604`; `callback-query-answer-state.ts:1-19`; `interactive-dispatch.ts:1-40` (real `callback_data`, `editMessage`/`editButtons`/`clearButtons`/`deleteMessage`) | **None.** `connectors/telegram/keyboard.py:11-131` only builds a persistent **reply keyboard** (plain-text button labels resent as a normal message) — no `inline_keyboard`, no `answerCallbackQuery` anywhere in the codebase | **yes** | **high** |
| Message reactions (set/read) | `reaction-level.ts:1-29`; `allowed-updates.ts:9-18` adds `message_reaction` to allowed updates | None — no `setMessageReaction`/`message_reaction` handling anywhere | yes | med |
| Forum topics / per-topic session routing | `docs/channels/telegram.md:649-696`; `thread-bindings.ts:756` (`chatId:topic:topicId` session keys, per-topic agent routing) | None — no `message_thread_id`/`is_topic_message`/`forum` anywhere in `sage_telegram_hosted_service.py` or `connectors/telegram*` | yes | med |
| Streaming / live message-edit previews | `docs/channels/telegram.md:330-423` — debounced `editMessageText` partial/block/reasoning streaming | None — always sends one final message; `edit_message` exists as a raw primitive (`connectors/telegram/transport.py:164-248`) but no streaming-preview loop uses it | yes | high |
| 401 (bad token) circuit breaker | `sendchataction-401-backoff.ts:68-208` — real-401 detector, exponential 1s→5min backoff, auto-suspend after 10 consecutive | None — `_telegram_api` (`sage_telegram_hosted_service.py:288-298`) logs and continues on any error; background poll (2s) and typing loop (4s) keep hammering a dead token | **yes** | **high** |
| Polling-stall watchdog | `docs/channels/telegram.md:1019-1030` — restarts polling after 120s of no progress | None — `_background_polling_loop` (`sage_telegram_hosted_service.py:1435-1499`) only catches exceptions, not silent hangs | yes | med |
| setMyCommands (native `/` menu) | `bot-native-command-menu.ts:433,475` for every configured account | `sage_telegram_hosted_service.py:251-286` — **only for the single shared hosted bot**; never called from the BYO connector path | partial | med |
| Sticker / media-group (album) handling | `docs/channels/telegram.md:697-792` — stickers, WEBP/TGS, media-group batching via `mediaGroupFlushMs` | None — `sage_telegram_hosted_service.py:709-763` and `connectors/telegram/media.py:66-153` handle photo/document/voice only, no stickers, no album batching | yes | med |
| Outbound message actions (delete, react, create-topic) | `docs/channels/telegram.md:606-629` | `sage_telegram_hosted_service.py:909-1008` has send_photo/send_document only — no delete/react/create-forum-topic tool actions | yes | med |
| Webhook signature verification (fail-closed) | `docs/channels/telegram.md:867-877` | `sage_telegram_hosted_service.py:691-706` HMAC + `routes_sage_telegram_hosted.py:131-146`, fails closed if unconfigured | no | — |
| Dedup / update-offset persistence | `update-offset-store.ts:134-241` — persisted per bot-id | Hosted path: in-memory only (`sage_telegram_hosted_service.py:1060`) — restart can redeliver. BYO path: persisted (`connectors/telegram/webhook.py:73-83`) | partial | med |
| Reply-delivery guarantee (redeliver on failure) | doc `867-877` | `routes_sage_telegram_hosted.py:243-247,296-300` — returns 503 (not 200) on failed delivery so Telegram redelivers; **arguably stronger** than OpenClaw's documented behavior | no (Empyralis ahead) | — |
| Group @mention / reply-addressing gate | native `@botusername`/reply detection | `sage_telegram_hosted_service.py:777-849` `text_addresses_bot` — equivalent | no | — |
| Multi-bot / multi-account | per-account state keys (`state-account-id.ts`) | BYO connector stack = 1 bot token per agent, isolated state (`telegram_connector_services.py`) — architecturally equivalent | no | — |

**Where Empyralis leads:** fail-closed webhook auth, HTTP-503-forces-redelivery
reliability pattern, and a documented history of closing real security holes
(group-pairing lockout, MarkdownV2 escaping) that read as more mature than a
docs page can prove for OpenClaw.

**Where Empyralis is behind:** everything interactive (buttons, reactions,
topics, streaming previews) and two concrete reliability gaps — no 401
circuit breaker, no polling-stall watchdog.

---

## Channel 2 — Discord

Empyralis: `server_modules/connectors/discord_connector.py` (protocol/parsing,
`discord.py`-based real Gateway websocket client) +
`server_modules/connectors/discord_bot_runtime_service.py` (lifecycle/runtime)
+ `discord_pairing_service.py` / `discord_bot_provisioning_service.py`.
OpenClaw: `/Users/mansur/openclaw/extensions/discord/src/`.

| Feature | OpenClaw (file:line) | Empyralis (file:line) | GAP? | Priority |
|---|---|---|---|---|
| Real-time Gateway (websocket, not webhook-only) | `monitor.gateway.ts`, `monitor/gateway-supervisor.ts` | `discord_connector.py:1180-1312` `DiscordGatewayListener` — real `discord.py` `Client` w/ intents | no | — |
| Gateway reconnect/backoff supervision | `monitor/gateway-supervisor.ts:73-115` — classifies disallowed-intents/fatal/reconnect-exhausted, restarts | None — comment at `discord_connector.py:1296-1302` admits discord.py's internal reconnect state "never surfaced... before this method existed"; a fatal `run_forever()` exit just dies, no restart | **yes** | **high** |
| Outbound rate-limit / retry handling | `retry.ts:53-94`, `delivery-retry.ts:44-56` — 429/`retry_after`-aware retry runner | None — `_discord_api_call` (`discord_connector.py:179-216`) raises immediately on any non-2xx, no backoff | **yes** | **high** |
| Message components (buttons/select) — inbound | `interactive-dispatch.ts`, `agent-components.dispatch.ts` — full custom_id dispatch + modals | Parsed (`discord_connector.py:690-717`, `interaction_type==3`) but `_handle_discord_interaction` (`discord_bot_runtime_service.py:590-603`) requires a command `name`, which component clicks don't carry — falls through to `"invalid_interaction"` | **yes** | **high** |
| Message components — outbound (send buttons/menus) | `send.components.ts`, `components.builders.ts` | None — `_message_payload` (`discord_connector.py:225-237`) only supports `content`/`embeds` | **yes** | **high** |
| Ephemeral interaction replies | default `ephemeral: true` for native commands | None — interaction callback (`discord_bot_runtime_service.py:624-631`) sends no `flags: 64` — always public | yes | med |
| Inbound dedup scope | `monitor/inbound-dedupe.ts:9-51` — applied to all inbound | `_DEDUP_CACHE` (`discord_connector.py:36-69`) is only invoked from the DM path (`:1109`) — guild/channel messages via `handle_parsed_event` have no dedup call | yes | med |
| Webhook-based sending (custom name/avatar) | `send.webhook.ts:79-144` | None | yes | low |
| Voice channels / voice messages | `send.voice.ts`, `voice/` dir — realtime STT/TTS | None — `Intents` (`discord_connector.py:1193-1198`) don't even request `voice_states` | yes | low |
| Stickers / custom emoji | `send.emojis-stickers.ts` | None | yes | low |
| Per-guild/per-channel policy granularity | `docs/channels/discord.md:535-601` — nested per-guild `requireMention`, per-channel allow, role allowlists | `event_matches_connector` (`discord_connector.py:788-816`) only supports a flat CSV of channel IDs or a single guild match | yes | med |
| Reaction inbound events | reaction-mode config (off/own/allowlist/all) | Parsed (`discord_connector.py:761-778`) but `should_trigger_agent_run` explicitly returns `False` for reactions (`:830-831`) — dead code path | yes | low |
| Security self-audit (misconfig detection) | `security-audit.ts:34-205` | None | yes | med |
| Mention/reply addressing gate (fails closed) | config-driven `requireMention` | `_resolve_discord_bot_id`/`_message_addressed_to_bot` (`discord_connector.py:642-679`), tested, fails closed when bot_id unknown | no | — |
| DM pairing flow | `dmPolicy: pairing` | `discord_pairing_service.py` full file, `/pair <code>` handler (`discord_connector.py:1071-1093`) | no | — |
| Interaction signature verification | ed25519, SDK-provided | `discord_connector.py:596-610` via PyNaCl, wired at `connectors_actions.py:1455-1476`, tested | no | — |
| Multi-bot / multi-agent binding | `channels.discord.accounts.<id>` config | DB unique index (`uq_agent_channel_bindings_active_inbound_owner`) + host file lock (`discord_bot_runtime_service.py:241-254`) — structurally stronger | no (Empyralis ahead) | — |
| Group DM handling | `dm.groupEnabled=false` default | `_is_group_dm_message()` (`discord_connector.py:1013-1033`), tested — correctly routes Group DMs through the mention-gated path | no | — |

**Verdict:** Discord's foundation (real Gateway, fail-closed addressing,
DB-enforced one-bot-per-agent, ed25519-verified interactions) is genuinely
solid — better in places than a docs page can prove for OpenClaw. The real
gaps cluster tightly: **no rate-limit retry, no reconnect supervision, and no
message-components (buttons) in either direction** — the last one also blocks
native in-Discord approval UI.

---

## Channel 3 — Slack

Empyralis: `server_modules/connectors/slack_connector.py` (766 lines) +
`server_modules/connectors_actions.py:1106` (`slack_events_webhook`) +
`slack-app-manifest.json` (the actual registered app's declared scopes/features).
OpenClaw: `/Users/mansur/openclaw/extensions/slack/src/` +
`docs/channels/slack.md`.

| Feature | OpenClaw (file:line) | Empyralis (file:line) | GAP? | Priority |
|---|---|---|---|---|
| Transport: Socket Mode | `docs/channels/slack.md:8,22-42` — default mode, no public URL needed | Not implemented — manifest sets `"socket_mode_enabled": false` (`slack-app-manifest.json:63`) | yes | med |
| Transport: HTTP Events API | doc `26-42` | `connectors_actions.py:1106` `slack_events_webhook`, manifest `request_url` (`slack-app-manifest.json:53`) | no | — |
| Slash commands (functional, not just declared) | native command catalog, dispatched over WS or `slash_commands[].url` | Manifest **declares** `/compact /memory /help /new` (`slack-app-manifest.json:12-33`) but `slack_events_webhook` only branches on `url_verification`/`event` payload kinds (`connectors_actions.py:1114-1117`) — Slack's slash-command POST payload shape is never parsed anywhere | **yes** | **high** |
| Interactive components (buttons/select/modals) | `interactive-dispatch.ts:103-139` — full `block_actions`/`view_submission` dispatch, dedup | **Architecturally off.** `slack-app-manifest.json:59-61` sets `"interactivity": {"is_enabled": false}`; `parse_inbound_event` has no branch for these payload kinds (`slack_connector.py:622-676`) | **yes** | **high** |
| Native exec/plugin approvals in Slack | `approval-native.ts` (233 lines) — Block Kit approval buttons | None (depends on interactivity, which is off) | yes | high |
| Streaming / incremental message edits | `streaming.ts` (367 lines) — `chat.update`-based live preview | None — no `chat.update` call anywhere | yes | med |
| Outbound threading on auto-reply | thread-aware by default, `reply_broadcast` flag | Webhook auto-reply always calls plain `chat.postMessage`, no `thread_ts` (`connectors_actions.py:1232`) — even though a threaded `post_reply(...)` function already exists (`slack_connector.py:514-535`) and is used only when an agent explicitly invokes it as a tool | **yes** | **high** |
| Block Kit rich formatting on auto-reply | `blocks-render.ts` (369 lines) | `send_message` accepts `blocks` (`slack_connector.py:407-425`) but the webhook auto-reply path never passes any — text-only in practice | yes | med |
| File uploads inbound | download + media pipeline, 20MB/8-files caps | None — `parse_inbound_event` never reads `event.files` | **yes** | **high** |
| File uploads outbound | `send.ts:558-663` — 2-step external-upload flow | `upload_file()` (`slack_connector.py:549-588`) — same 2-step flow, agent-tool-driven | no | — |
| Ephemeral messages | `chat.postEphemeral` used broadly | None | yes | low |
| Reactions outbound (add/remove) | `ackReaction`/`typingReaction` config | None — `DEFAULT_SLACK_BOT_SCOPES` requests `reactions:read` only, not `reactions:write` (`slack_connector.py:20-34`) | yes | low |
| Outbound send retry/rate-limit handling | `client-options.ts` retry policy on every Web API call | None — `_slack_api_call` (`slack_connector.py:332-349`) is a single request, no retry on 429 | yes | med |
| OAuth scopes: shipped vs. code's own defaults | 22 scopes incl. `files:*`, `reactions:write`, `assistant:write` | Manifest ships 7 scopes (`slack-app-manifest.json:37-45`); the connector's own `DEFAULT_SLACK_BOT_SCOPES` (`slack_connector.py:20-34`) lists 13 including `files:write`/`groups:*`/`mpim:history` — **the code and the shipped app manifest already disagree with each other** | **yes** | **high** |
| Multi-workspace OAuth | static `accounts.<id>` config entries | Dynamic: any OAuth'd workspace row is matched per-event by team/app/channel id (`slack_connector.py:678-720`) — no static config needed | no (Empyralis ahead) | — |
| Signature verification | signing secret, doc `30,35,488` | `verify_request_signature` HMAC-SHA256, 5-min window (`slack_connector.py:591-619`) | no | — |
| Inbound dedup (Slack retry storms) | persistent store, 24h TTL | In-memory only, 300s TTL (`slack_connector.py:47-89`) — lost across restarts/replicas | partial | med |
| Token refresh | not itemized (mostly static tokens) | Full OAuth v2 refresh-token flow (`slack_connector.py:196-330`) — though `token_rotation_enabled: false` in the manifest makes it currently inert | no (Empyralis ahead, dormant) | — |
| App Home / native Assistant threads | `app_home_opened`, `assistant_thread_started`, `assistant:write` scope | None | yes | low |

**Verdict:** the connector-level plumbing (multi-workspace OAuth matching,
signature verification, token refresh) is genuinely strong. But the shipped
Slack app is **interactivity-off by manifest**, the declared slash commands
have no parser behind them, and the automatic reply path — the thing every
user actually sees — is unthreaded, block-less plain text with no retry.
Three of those four are one-line-of-code-away fixes (thread_ts, blocks,
retry) sitting behind functions that already exist; the interactivity flag
requires a real re-manifest + reinstall.

---

## Channel 4 — WeChat Official Account / WeCom

See headline finding #1 above for the full wiring story. Condensed table:

| Feature | OpenClaw (docs-only, no inspectable source) | Empyralis (file:line) | GAP? | Priority |
|---|---|---|---|---|
| **Reachable in production at all (bidirectional chat)** | Unverifiable — depends on closed npm plugins not vendored anywhere on this machine | **No.** `routes_wechat_official.py` never mounted in `server.py` (0 grep hits); `connection_catalog_service.py:2019-2032,2045-2046` admits it in-repo; gateway TS `empyralis-gateway/src/channels/wechat/` never imported outside its own dir/tests | **critical (both, differently)** | **critical** |
| Webhook signature verification | doc-only | `wechat_official_service.py:126-148` + `channels/wechat/signature.ts:53-72` — implemented twice, SHA1+constant-time compare | n/a | — |
| XML inbound parsing | doc-only | `wechat_official_service.py:159-176` + `channels/wechat/xml.ts:29-46` — regex leaf-tag parser (deliberately avoids XXE-prone DOM parsing) | n/a | — |
| Event types handled (image/voice/video/menu-click/subscribe) | doc claims "media supported" (`wechat.md:13`), unverifiable | **Only `text`.** `wechat_official_service.py:374-404` / `message-mapper.ts:39-65` explicitly return `None`/`null` for every non-text type | yes | high (once mounted) |
| Media download (inbound) | doc claims support, unverifiable | Not implemented — docstring at `wechat_official_service.py:44-46` lists it as an unimplemented gap | yes | high (once mounted) |
| Access-token lifecycle (cache/refresh) | doc-only | `wechat_official_service.py:201-272` + `token-manager.ts:66-127` — 300s skew, lock-coalesced, invalidate-on-error-code | n/a | — |
| Rich outbound (news/articles/images/template messages) | doc claims media supported, unverifiable | Not implemented — `outbound.ts:19-26` explicitly scopes it out | yes | high (once mounted) |
| Per-account uniqueness enforcement | doc: multi-account supported | `wechat_official_service.py:504-520` — comment admits `CHANNEL_KEY_WECHAT` is missing from the DB uniqueness index, so concurrent binds aren't actually protected | yes | med (once mounted) |
| Encrypted-body / Safe Mode (EncodingAESKey) | doc-only | **Not implemented on either Empyralis side** (`wechat_official_service.py:39-43`, `signature.ts:20-26`) — any account with WeChat message encryption enabled will fail signature verification outright | yes | med |
| Outbound-only WeCom group-robot webhook | n/a | `wechat_work` — `connection_catalog_service.py:1971-2003`, `runtime_usable=True`, explicitly "no inbound replies" | n/a — the one live thing | — |

**Build size to reach parity with what's already written:** small — mount the
router (`server.py` + 2 lines per `routes_wechat_official.py`'s own
docstring), add a workspace-owner UI form calling
`wechat.assign_wechat_official`, then fix the DB uniqueness index. Media/rich
outbound is a separate, medium-sized follow-on.

---

## Channel 5 — Email

OpenClaw has **no email channel** (`docs/channels/index.md` doesn't list
one); its nearest equivalent is a Gmail-only Pub/Sub-webhook-to-agent-turn
pipeline built on a closed external `gog` CLI
(`/Users/mansur/openclaw/src/hooks/gmail.ts:1-311`,
`docs/automation/cron-jobs.md:343-391`). Empyralis's email lives in
`server_modules/connectors/smtp_connector.py` and is explicitly classified as
a connector/tool, not a channel (`connection_catalog_service.py:468-479`).

| Feature | OpenClaw | Empyralis (file:line) | GAP? | Priority |
|---|---|---|---|---|
| Inbound email → agent conversation turn | Yes, Gmail-only: Pub/Sub → `/hooks/gmail` → agent action (`src/hooks/gmail.ts:1-311`) | **None.** Confirmed by both my own grep and a prior in-repo audit (`connection_catalog_service.py:444-479`, referencing a dedicated "reliability-audit-2-channels" doc) — zero inbound listener anywhere | yes | med |
| Outbound send | Gmail API via `gog gmail send`, OAuth-only, Gmail-specific (`skills/gog/SKILL.md:39-45`) | Raw SMTP (`smtp_connector.py:231-280`) **and** separate Google Workspace/M365 OAuth send path (`runs_execution.py:4251`) — broader provider coverage | no (Empyralis ahead) | — |
| HTML body / attachments | `--body-html`, `--body-file` (`SKILL.md:42,80-84`) | `smtp_connector.py:129-147,172-176` — both supported | no | — |
| Threading (In-Reply-To/References) | `--reply-to-message-id` (`SKILL.md:45`) | **Not implemented** — `build_email_message` (`smtp_connector.py:150-182`) sets no threading headers; replies land as new top-level emails | yes | med |
| Fetch/read inbound (pull, agent-invoked) | n/a (opaque, external binary) | `fetch_emails` IMAP pull (`smtp_connector.py:353-417`) + Google Workspace equivalent (`test_google_workspace_fetch_emails_direct_tool.py:8-36`) — no cursor/dedup, `readonly=True` IMAP select | n/a | low |
| First-class "channel" classification | Absent from channel list | Explicitly demoted to `LANE_WORK_APP_CONNECTOR`, `supports_inbound=False` (`connection_catalog_service.py:468-479`) — a deliberate prior product decision on both sides | no (matches intent) | — |

**Build size:** the one concrete, contained fix is email threading
(`In-Reply-To`/`References` headers) — small. True inbound-email-as-a-channel
(webhook ingestion, e.g. Mailgun/SendGrid inbound-parse or Gmail Pub/Sub, plus
dedup and turn creation) is a medium build that **neither product has solved
robustly** — OpenClaw's version is Gmail-only and depends on an unvendored
closed binary.

---

## Prioritized cross-channel punch list (OpenClaw has it, we lack it)

**Critical**
1. **Mount the WeChat Official/WeCom router and expose a connect UI.**
   `server.py` (add the two-line `include_router` call `routes_wechat_official.py`
   already documents in its own module docstring) +
   `wechat_official_service.assign_wechat_official` needs a UI form. Small
   build — the protocol code already exists twice.

**High**
2. **Discord/Telegram/Slack: real interactive buttons (round-trip
   callback/component handling).** Telegram: no `inline_keyboard`/`callback_query`
   at all (`connectors/telegram/keyboard.py` is reply-keyboard-only). Discord:
   parsed but dropped (`discord_bot_runtime_service.py:590-603` requires a
   command `name` that component clicks don't carry) and never sent
   (`discord_connector.py:225-237` has no `components` key). Slack: switched
   off at the app-manifest level (`slack-app-manifest.json:59-61`). This is
   the single feature category blocking native in-channel approvals on all
   three. Large build (protocol-consistent custom_id/callback registry per
   channel); Slack additionally needs a re-manifest + customer reinstall.
3. **Slack: thread the automatic reply, send Block Kit, and actually parse
   slash-command payloads.** All three sit behind functions that already
   exist (`post_reply` with `thread_ts`, `send_message(..., blocks=...)`)
   but are never called from the webhook auto-reply path
   (`connectors_actions.py:1232`). The manifest declares `/compact` etc.
   but `slack_events_webhook` never branches on the slash-command payload
   shape. Small-to-medium build, high visible impact — this is what every
   Slack user sees on every reply.
4. **Discord: outbound rate-limit/retry + gateway reconnect supervision.**
   `_discord_api_call` (`discord_connector.py:179-216`) fails hard on 429 with
   no backoff; a fatal websocket death has no restart path
   (`discord_bot_runtime_service.py:270-272`). Medium build (wrap the REST
   call, add a watchdog thread).
5. **Telegram: 401 circuit breaker on the hosted bot's background poll/typing
   loops.** A revoked token currently causes an infinite tight-retry hammer
   instead of detection + suspend. Small-medium build, real reliability risk.
6. **Slack: shipped OAuth scopes don't match the connector's own code
   capability.** `slack-app-manifest.json` requests 7 scopes;
   `slack_connector.py`'s own `DEFAULT_SLACK_BOT_SCOPES` lists 13. Fixing the
   manifest is trivial but requires existing installs to re-authorize.
7. **Slack: inbound file/image attachments are silently dropped** — no
   `event.files` handling anywhere in `parse_inbound_event`.
8. **WeChat: rich/media inbound+outbound** (image/voice/video, template
   messages) — currently text-only even once the router above is mounted.

**Medium**
9. Telegram forum/topic-aware session routing (no per-topic agent routing).
10. Telegram streaming/live-edit reply previews (`edit_message` primitive
    exists but nothing streams into it).
11. Discord per-guild/per-channel policy granularity (only a flat channel-ID
    allowlist today, vs. OpenClaw's nested per-guild config).
12. Slack Socket Mode (irrelevant to Empyralis's own cloud-hosted single
    endpoint today, but blocks any future "connect from your own network"
    story) and native streaming (`chat.update`-based live preview).
13. Email threading headers (`In-Reply-To`/`References`) — small, contained.
14. WeChat encrypted-body/Safe-Mode support and the missing DB uniqueness
    constraint on multi-account binding.

**Low** (cosmetic / low-leverage pre-launch): Discord voice, stickers,
webhook-branded sends, presence; Slack ephemeral messages, App Home/Assistant
threads, reaction write-scope; Telegram stickers/media-group batching;
inbound-email dedup/cursor state.

## Where Empyralis is already ahead of what OpenClaw's docs claim

- **Telegram**: HTTP-503-forces-Telegram-to-redeliver on failed reply
  delivery (`routes_sage_telegram_hosted.py:243-247`) is a more explicit
  guarantee than OpenClaw's docs describe.
- **Discord**: one-bot-token-per-agent is enforced by a real Postgres unique
  index plus a host-level file lock
  (`discord_bot_runtime_service.py:241-254`), not just config-level dedup.
- **Slack**: multi-workspace routing is fully dynamic (any OAuth'd workspace
  row is matched per-event) with no static `accounts.<id>` config needed, and
  the OAuth token-refresh flow (`slack_connector.py:196-330`) is more complete
  than what OpenClaw's docs describe (though currently inert —
  `token_rotation_enabled: false` in the manifest).
- **Email**: broader provider coverage (raw SMTP for any provider + OAuth
  Gmail/M365), vs. OpenClaw's Gmail-only, closed-binary-dependent path.
