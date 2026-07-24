# Gap Analysis: Personal-Account Channels — OpenClaw vs Empyralis

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). `sage_*` code/variable/string identifiers (e.g. `"sage-main"`, `sage_agent_runtime_service.py`) are legacy code artifacts only, not a live product concept. Anywhere this document's prose says "Sage" or "master," read: the owner-facing agent. This is a lighter-touch terminology note, not a full rewrite — the body below is unchanged and may still use "Sage" throughout.

READ-ONLY comparison. No code changed. Snapshot as of 2026-07-21, branch `fix/hardware-detail-width`.
Every claim below is file:line-cited from BOTH codebases — OpenClaw at `/Users/mansur/openclaw` (read-only reference, MIT), Empyralis at `/Users/mansur/empyralis`. Builds on `docs/OpenClaw.md`'s existing Signal/iMessage/mention-gating research (cited inline where reused) and `docs/design/reliability-audit-2-channels.md`'s channel-infrastructure inventory (also cited inline) rather than re-deriving them.

Scope: the five PERSONAL-ACCOUNT channels — Telegram (MTProto), WhatsApp, Signal, iMessage, WeChat. Excludes hosted/bot channels (Telegram-bot, Discord, Slack, Email) — those are a structurally separate system (`server_modules/*.py` only, no Agent Computer/gateway involvement — `reliability-audit-2-channels.md:12`).

---

## Cross-channel facts (apply to every table below)

- **Reactions are platform-wide absent.** Zero outbound reaction-send code exists for any personal channel — repo-wide grep for `send_reaction`/reaction-action handlers across `server_modules/personal_channels_service.py` and all of `empyralis-gateway/src/channels/**/*.ts` returns no hits. Telegram's and WhatsApp's own capability manifests self-declare `reactions: false` (`empyralis-gateway/src/channels/telegram/runtime.ts:943`, `empyralis-gateway/src/channels/whatsapp/runtime.ts:463`).
- **No reaction/button-based approval flow exists anywhere** — grepped `personal_channels_service.py` for approval+reaction/button handling, zero hits. OpenClaw uses 👍/👎 reactions as an approval UX on Telegram, WhatsApp, Signal, and iMessage alike.
- **Real inbound media download+forward exists, but only for the two socket-based channels** (Telegram, WhatsApp). The generic transcription/attachment pipeline (`server_modules/personal_channels_service.py:1711-1789`, `_process_inbound_media_for_turn`) fetches bytes, transcribes voice via `personal_channel_transcription_service.py`, and produces real image/file attachments for the agent turn — but it only fires when the inbound payload's `media` array is populated, which the three poll/RPC-based local-bridge channels (Signal, iMessage, WeChat) never populate. Their bridges emit a `<media:attachment> (N)` **text placeholder only** (`bridges/signal-cli-bridge.ts:242-245`, `bridges/imsg-imessage-client.ts:505-513`).
- **Two disjoint reconnect models.** Telegram/WhatsApp use `foundation/reconnect-utils.ts`'s shared exponential-backoff policy; Signal/iMessage/WeChat ride `local-bridge-runtime.ts`, which has "no reconnect/backoff concept at all" (`reconnectAttempts` hardcoded to `0`, `local-bridge-runtime.ts:292,322` — cited in `reliability-audit-2-channels.md:14,78`). iMessage's new imsg-native runtime is the one exception, with its own real reconnect loop (`imsg-imessage-runtime.ts:394-462`).

---

## 1. Telegram

**Architecture note:** OpenClaw's Telegram is a **bot** via grammY (`grammy: 1.43.0`, bot-token auth — confirmed `docs/OpenClaw.md:730-741`). Empyralis's Telegram is a **full personal MTProto user-session** via GramJS (`empyralis-gateway/src/channels/telegram/login.ts`). An entire class of OpenClaw bot-only features (native `/`-command menu, inline keyboards/`callback_query`) is **architecturally inapplicable** to a user-session account — marked `n/a-architecture` below, not a gap.

| Feature | OpenClaw (file:line) | Empyralis (file:line) | GAP | Priority |
|---|---|---|---|---|
| Text receipt, media download+forward (real bytes) | `docs/channels/telegram.md:8,697-737` | `telegram/runtime.ts:229-266` (`classifyTelegramInboundMedia`), `:1985-1988` (`downloadAndStoreTelegramMedia`) | No | — |
| Groups, mention/reply gating | `src/channels/mention-gating.ts` (`docs/OpenClaw.md:716-724`) | `telegram/runtime.ts:527-560` `hasExplicitTelegramMention` (fixed 2026-07-19, see `docs/OpenClaw.md`) | No | — |
| Voice notes + transcription | `docs/channels/telegram.md:697-707` | `server_modules/personal_channel_transcription_service.py` (Whisper-based STT), wired via `personal_channels_service.py:1764-1779` | No | — |
| Outbound reply-to-id threading | `docs/channels/telegram.md:631-647` | `telegram/runtime.ts:1111,2066-2100` (`replyTo` on send) | No (OpenClaw additionally auto-attaches a *visible* quote excerpt — cosmetic gap) | Low |
| Forum/topic threads (per-topic session isolation) | `docs/channels/telegram.md:649-693`; `extensions/telegram/src/forum-service-message.ts` | **Not found** — zero "topic"/"forum"/"thread_id" hits in `telegram/runtime.ts` or `personal_channels_service.py` | **YES** | Med — small-med build (GramJS exposes `replyTo.replyToTopId`; needs topic-scoped session keys) |
| **Quoted-reply text/content shown to agent** | `docs/channels/telegram.md:887` (reply/quote/forward normalized into context window) | `telegram/message-mapper.ts:53-96` only carries `replyToExternalMessageId` (an ID, resolved to boolean `is_reply_to_sage` in `runtime.ts:712`) — **no quoted text is ever fetched or forwarded** | **YES** | **High** — medium build (GramJS `getMessages` lookup; own-sent-cache already exists to extend) |
| Reactions received | `extensions/telegram/src/bot-handlers.runtime.ts:1878` (`message_reaction` event) | **Not found** — only `NewMessage` registered (`runtime.ts:1946`) | **YES** | Low — small build |
| Message edits received | grammY dispatches `edited_message` natively | **Not found** — no `EditedMessage` handler (confirmed: only `replyTo.replyToMsgId` references exist, no edit-event listener) | **YES** | Low — small build |
| Edit own sent message | `docs/channels/telegram.md:606-624`; `bot-handlers.runtime.ts:2393-2395` | **Not found** — zero "editMessage" hits in `telegram/*.ts` or `personal_channels_service.py` | **YES** | Med — powers OpenClaw's live-streaming-preview UX; Empyralis can only send new messages, never edit in place |
| Delete/unsend own message | `docs/channels/telegram.md:606-624`; `bot-handlers.runtime.ts:2425` | **Not found** | **YES** | Low — small build |
| Reactions sent (agent reacting) | `docs/channels/telegram.md:619-627` | Capability manifest declares `reactions: false` (`runtime.ts:943`) | **YES** | Low — small build |
| Polls | `docs/channels/telegram.md:894-928`; `poll-visibility.ts` | **Not found** — zero "poll" hits | **YES** | Low — small-med build |
| Stickers: metadata, vision description, send | `docs/channels/telegram.md:738-791`; `sticker-cache.ts`, `sticker-vision.runtime.ts` | Received only, downgraded to a generic image bucket, no emoji/pack metadata, no vision description, no send (`telegram/runtime.ts:255-260`) | **YES** | Low — medium build for full parity |
| Multi-device / session-conflict (`UpdateNewAuthorization`) | `docs/channels/telegram.md:319` (409 conflict, bot-API analog) | **Not found** in `telegram/reconnect.ts` — no explicit MTProto "logged in elsewhere" handling | **YES** | Med |
| Exec approvals via reaction/inline button | `docs/channels/telegram.md:932-950` | **Not found** (platform-wide absence, see Cross-channel facts) | **YES** | Med |
| Native bot command menu, inline keyboards/`callback_query` | `docs/channels/telegram.md:455-517,518-604` | N/A — no bot identity on a user-session account | n/a-architecture | — |
| Broadcast/channel-post handling | grammY dispatches `channel_post` distinctly | Dropped outright by design (`isBroadcastTelegramChat`, `runtime.ts:602-609`) | Deliberate product decision, not oversight | Low |

---

## 2. WhatsApp

Both sides use the same underlying library — Baileys 7.0.0-rc13 (`openclaw/extensions/whatsapp/package.json:12`, `empyralis-gateway/package.json:24`). OpenClaw's plugin is ~150 files/~15k lines; Empyralis's is 8 files/~4k lines.

| Feature | OpenClaw (file:line) | Empyralis (file:line) | GAP | Priority |
|---|---|---|---|---|
| Inbound real media download | `extensions/whatsapp/src/inbound/media.ts` | `whatsapp/runtime.ts:1516,1777-1782` (`downloadMediaMessage` via Baileys) | No | — |
| Groups | `extensions/whatsapp/src/inbound/monitor.ts` | `message-mapper.ts` (`is_group`), `runtime.ts:1357-1360` | No | — |
| Voice notes + transcription | `docs/channels/whatsapp.md:395-398` | `message-mapper.ts:100-107` (ptt detect), `runtime.ts:939-950` (ffmpeg transcode), `personal_channels_service.py:1764-1779` | No | — |
| Revoked/logged-out session detection | Baileys `DisconnectReason.loggedOut` | `whatsapp/reconnect.ts:44`, `runtime.ts:1183-1184` (wipes local auth) | No | — |
| Multi-device conflict (440/`connectionReplaced`) | Not explicitly named in `connection-controller.ts` | `reconnect.ts:46-56` — explicit named `"conflict"` status + distinct backoff | **Empyralis is ahead** | — |
| QR pairing UX | Terminal-rendered only; own docs warn this is fragile for remote/headless hosts (`docs/channels/whatsapp.md:112-117`) | In-app QR image render (`PersonalChannelConnectPanel.tsx:409-473`) | **Empyralis is ahead** | — |
| Phone-number/pairing-code login (no-QR alternative) | Not found — doc states "current login is QR-based" (`docs/channels/whatsapp.md:69`) | Fully wired (`login.ts:29-59`, `runtime.ts:109,1690-1696` `requestPairingCode`) | **Empyralis is ahead** | — |
| **Full quoted-message body extraction (inbound)** | `inbound/extract.ts:424-456` `describeReplyContext` — extracts quoted body/media for *any* quoted message | `message-mapper.ts:176-214` only sets `quoted_stanza_id` when the quoted message's `participant === ownedJid` (i.e. only replies to Sage's own messages) — no general quote-body extraction | **YES** | **High** — medium build, Baileys already exposes `contextInfo.quotedMessage`, this is parsing not new plumbing |
| **Outbound reply-to/quote (visible WhatsApp quoting)** | `quoted-message.ts` + `outbound-adapter.ts`; `replyToMode` config | `message-mapper.ts:227,239` carries `reply_to_external_message_id` through the contract, but **no `sendMessage(...,{quoted:...})` call consumes it** at any of the 4 send sites in `runtime.ts` (750,804,814,821) — dead field | **YES** | **High** — small build, cheapest high-value fix (wiring, not new capability) |
| **Reactions received** | `approval-reactions.ts:284` (`reactionMessage`) | **Not found**; `reactions: false` (`runtime.ts:463`) | **YES** | Med |
| **Reactions sent** | `send.ts:293` `sendReactionWhatsApp`, `channel-react-action.ts:199-224` | **Not found**; same `reactions: false` flag | **YES** | **High** |
| Ack reactions + reaction-based approval | `channel-react-action.ts`, `approval-reactions.ts`, `ackReaction` config | **Not found** anywhere in `whatsapp/` or `foundation/` | **YES** | Med |
| **Read receipts (blue ticks) sent** | `inbound/monitor.ts:919-927` (`sock.readMessages(...)`), default-on (`accounts.ts:28,136`) | **Not found** — zero `readMessages`/`sendReadReceipt`/`chatModify` hits anywhere in `whatsapp/*.ts` | **YES** | Med — small build, single Baileys call |
| Contact cards (vCard) | `vcard.ts` (full FN/N/TEL parser), `inbound/extract.ts:328` | **Not found** | **YES** | Low-Med |
| Location messages | `inbound/extract.ts:370` `extractLocationData` | **Not found** | **YES** | Low |
| Stickers (dedicated type + send) | `inbound/extract.ts:310`, `send-api.ts:186` `sendSticker` | Mapped into generic `"image"` kind, no outbound send (`message-mapper.ts:119-124`) | **YES** (minor) | Low |
| Polls | `send.ts:341` `sendPollWhatsApp` | **Not found** | **YES** | Low |
| Message edits received / delete-for-everyone | Not found in OpenClaw source either | Not found | Parity gap, neither side | — |
| Group management (add/remove/promote) | Not exposed as an agent tool either | Not exposed | Parity | — |

---

## 3. Signal

Reuses and extends `docs/OpenClaw.md`'s "STEP 11 — SIGNAL CHANNEL" research (2026-07-19), which already covers self-chat, dmPolicy, typing, and the Summary Table there. New findings from this pass below; established facts are referenced, not repeated.

| Feature | OpenClaw (file:line) | Empyralis (file:line) | GAP | Priority |
|---|---|---|---|---|
| Send/receive text, DM/group gating, self-chat command channel, typing indicators | `docs/OpenClaw.md:519-684` (full prior research) | `bridges/signal-cli-bridge.ts` (mirrors OpenClaw's own JSON-RPC contract) | No (already built, see cache doc) | — |
| Attachment/media real download+forward | `docs/OpenClaw.md:562-584` — `deps.fetchAttachment(...)` downloads bytes | `signal-cli-bridge.ts:230-245` — attachment-only messages become `<media:attachment> (N)` **text placeholder**; "This bridge does not fetch attachment bytes — that's a real, separate gap" (comment at line 235) | **YES** (documented gap, unbuilt) | Med |
| Read receipts | `docs/OpenClaw.md:601-611` `sendReceiptSignal` (DM-only, opt-in) | Not built | **YES** (matches TG/WA — see cache) | Low |
| Reactions | `docs/OpenClaw.md:613-622` `sendReactionSignal`/removeReaction, used for approvals | Not built (platform-wide) | **YES** | Med |
| **Quoted-reply text shown to agent** (vs. gating-only quote match) | `docs/channels/signal.md` — quote used for reply routing/context | `signal-cli-bridge.ts:259-264,314-317` — `dataMessage.quote.id` used ONLY to compute the boolean `is_reply_to_sage`; the quoted text itself is never extracted or forwarded | **YES** | Med — small-med build, `dataMessage.quote` already carries author+text per signal-cli's JSON schema |
| **Outbound rich/styled text** | `docs/channels/signal.md:239` — native mode supports `text_mode: "styled"` for bold/italic | **Not found** — zero styling/markdown-to-Signal-formatting code in `signal-cli-bridge.ts` | **YES** | Low — small build |
| Multi-account | `channels.signal.accounts.<id>` | Single-account only (matches every other local-bridge channel — not Signal-specific) | Parity by design | — |
| Reconnect/backoff on SSE drop | (native mode's own SSE reconnect) | SSE reconnect failures **silently swallowed** (`.catch(() => undefined)`, `signal-cli-bridge.ts:433-435`); no backoff (`local-bridge-runtime.ts` hardcodes `reconnectAttempts: 0`) | **YES** | Med — reliability, not a feature gap per se |
| In-app pairing/setup UX | `openclaw channels login`/pairing wizard | None — `SageLauncher.tsx:58`: *"Signal requires a signal-cli bridge already running on your own computer — there's no in-app setup for this yet... point your Gateway at it with the EMPYRALIS_SIGNAL_BRIDGE environment variables."* | **YES** | Med |
| Stickers, polls, location, contact cards | Not documented as distinct Signal features in OpenClaw's own docs either (Signal protocol has limited native support for these) | Not found | Parity — not a real gap | — |

---

## 4. iMessage

Reuses `docs/OpenClaw.md`'s iMessage/BlueBubbles research (2026-07-21). **Important state change since that research was written**: Empyralis shipped a new imsg-native runtime the same day (`git log`: commit `2402605a5`, files `bridges/imsg-imessage-client.ts` (930 lines) + `channels/imsg-imessage-runtime.ts` (576 lines), both timestamped after the cache doc's iMessage section) — this section reflects the **current, post-imsg-migration state**, not the BlueBubbles-only state the cache doc describes as "current."

Empyralis's imsg runtime deliberately mirrors OpenClaw's own architecture (in-process child spawn, JSON-RPC over stdio, `watch.subscribe`/`since_rowid` replay, staged 4-layer health probe) — this is real, close-to-parity infrastructure work, not a naive port. The comparison below is about **feature completeness within OpenClaw's "basic mode"** (no SIP/Library-Validation disable — the only mode Empyralis or a security-conscious customer would run) and what OpenClaw's opt-in "Private API mode" additionally unlocks.

| Feature | OpenClaw (file:line) | Empyralis (file:line) | GAP | Priority |
|---|---|---|---|---|
| Text receive, group gating (mention/reply-to-Sage), 4-stage health probe, in-app recheck/install actions, restart-recovery replay via `since_rowid` | `docs/channels/imessage.md:731-747`, `probe.ts:290-337` | `imsg-imessage-runtime.ts:70-576` (near-identical architecture, cited method-by-method in its own comments) | No (strong parity) | — |
| **Basic-mode media receive (real attachment bytes)** — OpenClaw's own docs: basic mode is "text and media send/receive only" (`docs/channels/imessage.md:193`), no SIP required | `docs/channels/imessage.md:12,193` | `imsg-imessage-runtime.ts:428` explicitly requests `attachments: false` in `watch.subscribe` (metadata-only); `imsg-imessage-client.ts:505-513` maps any attachment to a `<media:attachment> (N)` **text placeholder** | **YES** | **High** — this is a basic-mode capability, not gated by the SIP tradeoff; small-med build (flip `attachments: true`, wire through existing generic media pipeline at `personal_channels_service.py:1711-1789`) |
| **Basic-mode media send (outbound)** | `docs/channels/imessage.md:193` (basic mode: media send works) | `imsg-imessage-client.ts:429-449` `buildImsgSendParams` — text-only params, no attachment/media field in the RPC call at all | **YES** | **High** — same basic-mode capability OpenClaw ships by default; small-med build |
| **Quoted-reply text shown to agent** | Private API mode: threaded replies (`docs/channels/imessage.md:182`) | `imsg-imessage-client.ts:565` `reply_to_id` used ONLY to compute `is_reply_to_sage` in groups — never surfaced as quote context to the agent, and not computed at all for DMs | **YES** | Med |
| Reactions/tapbacks received (as a system event, not chat noise) | Private API mode (`docs/channels/imessage.md:182,604`) | `imsg-imessage-client.ts:551-553` — `is_reaction`/`is_tapback` messages are explicitly **dropped** (`return null`), not even logged as a system event | **YES** | Low (SIP-gated on OpenClaw's own side too) |
| Edit/unsend own message | Private API mode (`docs/channels/imessage.md:182`) | Not built | **YES** | Low (SIP-gated) |
| Native Apple polls (`sendWithEffect`, `poll`/`poll-vote`) | Private API mode (`docs/channels/imessage.md:182,569-572`) | Not built | **YES** | Low (SIP-gated) |
| Typing indicator, read receipts | Private API mode (`docs/channels/imessage.md:182,586-599`) | Not built (documented in `docs/OpenClaw.md:586-611` as "typing built for Signal only; BlueBubbles/WeChat/iMessage bridges simply never get a successful typing call") | **YES** | Low (SIP-gated) |
| Group management (rename, add/remove participants) | Private API mode (`docs/channels/imessage.md:182`) | Not built | **YES** | Low (SIP-gated) |
| SIP/Private-API disclosure to the operator | Explicit `<Warning>` block, opt-in, dedicated-Mac guidance (`docs/channels/imessage.md:190-194,247-253`) | N/A — Empyralis never offers Private API mode at all, so no disclosure is needed yet; if ever built, must ship with equivalent warning treatment | Product-decision gap, not a bug | — |

---

## 5. WeChat

**This is the largest architectural gap of the five channels — Empyralis has no personal WeChat channel at all today**, and this is an existing, explicit, founder-documented decision, not an oversight this pass discovered fresh (see `reliability-audit-2-channels.md:29,77-84` and the in-code comment below), but it is the single biggest feature delta versus OpenClaw among all five channels.

| Aspect | OpenClaw | Empyralis |
|---|---|---|
| What exists | Personal WeChat via QR login through Tencent's **external plugin** `@tencent-weixin/openclaw-weixin` (Tencent's own "iLink API") — `docs/channels/wechat.md:1-171`. Not in OpenClaw's core repo; no source available locally to cite beyond the doc. | `empyralis-gateway/src/channels/wechat/*.ts` implements **WeChat Official Account / WeCom (企业微信)** — a **business/webhook API**, explicitly NOT the personal-account bridge: `wechat/types.ts:1-9` — *"This module implements 'official WeChat'... NOT the personal-account bridge modeled by `../local-bridge-runtime.ts`'s `wechat_personal` entry. Personal WeChat automation has no supported API and is explicitly out of scope."* |
| Personal WeChat bridge implementation | Real (external plugin, QR login, direct chats + media) | **Zero.** `reliability-audit-2-channels.md:29`: "No gateway-side implementation exists — zero files match `*wechat*` under `empyralis-gateway/src`" (that audit predates the Official-Account module's addition, but confirms no *personal*-bridge file has ever existed). No `wechat-cli-bridge.ts` exists (contrast: `signal-cli-bridge.ts`, `bluebubbles-bridge.ts`, `imsg-imessage-client.ts` all exist for their channels). |
| Catalog/UI honesty | N/A | `wechat_personal` is still **listed as a connectable channel** in `personal_channels_service.py:81,220-223` ("WeChat personal runs through an Agent Computer local bridge... Connect an Agent Computer with a WeChat bridge to enable Sage messaging") and on the public landing page (`frontend/lib/marketing/landing-page.tsx:36`, per audit doc) despite zero backend. The in-app copy is more honest: `SageLauncher.tsx:59` — *"Personal WeChat has no official API to build a bridge against, so this isn't supported yet."* — but this is a UI-only disclosure, not reflected in the machine-readable catalog fields, which the audit doc flags as contradictory (`launch_status=LAUNCH_LIVE_WHEN_CONFIGURED`, `setup_available=True` — `reliability-audit-2-channels.md:29`). |
| Groups | Not advertised by the plugin's own capability metadata (`docs/channels/wechat.md:13-14`) | N/A (no channel exists) |
| Media | Supported (`docs/channels/wechat.md:13`) | N/A |
| DM pairing/allowlist | Standard OpenClaw pairing model (`docs/channels/wechat.md:84-96`) | N/A |

**Note on the existing Official Account module**: `wechat/{signature,outbound,message-mapper,xml,token-manager,config,server}.ts` is real, working code for a *different* product (WeChat Official Account/WeCom business messaging, requiring a publicly-reachable inbound HTTPS endpoint — a networking posture Empyralis's Agent Computer gateway otherwise explicitly avoids, per `wechat/types.ts:15-29`). It is not a stepping-stone toward personal WeChat — the two use unrelated Tencent APIs and auth models. Not counted as partial progress on the personal-channel gap above.

**Founder decision point, not a build recommendation**: OpenClaw proves a personal-WeChat bridge is technically possible via Tencent's iLink API (through a third-party npm package Empyralis doesn't control or ship). Whether to pursue an equivalent is a build/buy/legal decision (unofficial API surface, ToS risk, a dependency on an external maintainer) outside the scope of this read-only comparison — flagging the fact, not recommending the path.

---

## Prioritized gap list (features OpenClaw has that Empyralis lacks)

### High priority
| Channel | Feature | Why it matters | Build size |
|---|---|---|---|
| WhatsApp | Wire existing `reply_to_external_message_id` field into the outbound `sendMessage` call | Field already flows end-to-end through the contract; nobody connected it at the 4 send call-sites in `runtime.ts`. Cheapest high-value fix on this entire list. | **Small** |
| WhatsApp | Reactions sent (agent reacting) | Flagship low-friction UX (ack reactions, exec/plugin approvals via 👍/👎) OpenClaw uses heavily; Empyralis has zero reaction capability on any channel. | Medium |
| WhatsApp | Full quoted-message body extraction (not just "replied to Sage or not") | Agent currently only sees quote context when replying to its own prior message — replies quoting a third party or older message are invisible. Baileys already exposes the data; this is parsing, not new plumbing. | Medium |
| Telegram | Quoted-reply text/content shown to agent | Same problem as WhatsApp — agent knows *that* a reply happened, never *what* it was replying to. Common Telegram interaction pattern ("reply to that with X"). | Medium |
| iMessage | Basic-mode real media receive (currently placeholder-only) and send (currently not implemented at all) | Not SIP-gated — this is OpenClaw's *default*, no-tradeoff capability. Empyralis's imsg integration doesn't reach parity with OpenClaw's basic mode, let alone Private API mode. | Medium (receive), Medium (send) |

### Medium priority
| Channel | Feature | Why it matters | Build size |
|---|---|---|---|
| WhatsApp | Reactions received, read receipts sent | Reactions feed approval UX; read receipts are a small, visible trust signal (blue ticks) users notice immediately on a personal-number channel. | Small (read receipts), Medium (reactions) |
| Signal | Real attachment download+forward | Currently a documented, long-standing placeholder-only gap shared with iMessage/WeChat. | Medium |
| Signal | Quoted-reply text to agent (currently gating-only) | Same class of gap as Telegram/WhatsApp. | Small-Medium |
| Telegram | Forum/topic session isolation | Multi-topic Telegram groups currently collapse into one conversation context. | Small-Medium |
| Telegram | Edit own sent message | Powers "live update" streaming-preview UX patterns OpenClaw relies on; Empyralis can only send new messages. | Medium |
| Telegram | Session-conflict detection (`UpdateNewAuthorization`) | A personal account can be logged in elsewhere simultaneously; Empyralis has no distinct detection for it. | Medium |
| All channels | Reaction-based/button-based approvals | Platform-wide absence; OpenClaw uses this on every channel it supports. | Medium (shared plumbing once one channel has reactions) |

### Low priority (real gaps, smaller impact)
- Telegram: reactions received, message edits received, delete/unsend own message, native polls, sticker metadata+vision+send (all small-to-medium builds).
- WhatsApp: contact cards (vCard), location messages, dedicated sticker type+send, polls (small builds each).
- Signal: read receipts, reactions, outbound styled/rich text (small builds each).
- iMessage: reactions/tapbacks-as-system-event, edit/unsend, native polls, typing, read receipts, group management — all gated behind OpenClaw's own opt-in SIP/Private-API tradeoff, so building these implies first deciding whether Empyralis will ever offer that tradeoff at all (a product/security decision, not just an engineering one).

### Architectural, not a gap (excluded from the count above)
- Telegram: native bot command menu, inline keyboards/`callback_query` — inapplicable to a personal MTProto user-session account (no bot identity to attach them to).
- WhatsApp: message edits received, delete-for-everyone — not found in OpenClaw's own source either (parity, not a gap).

### The one structural gap, not a feature gap
- **WeChat has no personal-account channel at all in Empyralis** — a founder-documented, deliberate decision (`wechat/types.ts:1-9`, `SageLauncher.tsx:59`), correctly disclosed in-app but inconsistently reflected in machine-readable catalog metadata (worth a separate, non-channel-feature fix: either hide `wechat_personal` from the catalog until real, or make `launch_status`/`setup_available` match the honest in-app copy). OpenClaw reaches personal WeChat only via a third-party external plugin using Tencent's non-public iLink API — proof of technical feasibility, not evidence of a low-risk path Empyralis should assume it can replicate without its own legal/ToS review.
