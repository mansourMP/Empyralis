# Inbound Attribution Audit — how a deployed agent perceives who's talking to it

> **Terminology note (2026-07-23):** "Sage" is legacy product terminology — the founder ruled the concept removed from the product; the platform has only agents (owner-facing, customer-facing serving the owner, and AskAI). `sage_*` code/variable/string identifiers (e.g. `"sage-main"`, `sage_agent_runtime_service.py`) are legacy code artifacts only, not a live product concept. Anywhere this document's prose says "Sage" or "master," read: the owner-facing agent. This is a lighter-touch terminology note, not a full rewrite — the body below is unchanged and may still use "Sage" throughout.

**Date:** 2026-07-22
**Scope:** every inbound path into a deployed Empyralis agent — Telegram (Hosted bot + Personal full-account), WhatsApp, WeChat, iMessage, Signal, Slack, Discord, plus the web console. Verified against live code, not docs. `docs/OpenClaw.md` and the platform map were read but every claim below is grep/file:line-checked against the current `main` branch (HEAD `fafab001d` at time of writing).

**Bottom line up front:** there is no single canonical inbound envelope. There are at least **four independent turn-persistence/attribution mechanisms** that don't fully agree with each other: (1) `agent_turn.py`'s `AgentTurnRequest`/channel+surface stamp (web console only), (2) `personal_channel_sage_bridge_service.py`'s `is_owner`/`is_group` provenance system (Telegram-Personal, WhatsApp, Signal, iMessage, WeChat-local-bridge-that-doesn't-exist), (3) `command_registry.py`'s `identity_links`-based owner check (commands, all channels), and (4) `agent_channel_router.py`'s bare `is_group` text-prefix convention (Slack, Discord, GitHub, Telegram-Studio). Three of the seven channels have zero owner-detection. One channel (WeChat) collapses every distinct customer into one shared conversation.

---

## 1. Sender + surface identity per channel

### Is there a canonical inbound envelope struct?

**No — partial, and inconsistent.** The closest thing to a typed canonical struct is `NormalizedSageTurn` (`server_modules/channel_adapter.py:49-63`):

```
workspace_id, tenant_id, message, surface ("chat"|"acp"), mode,
current_user, attachments, channel_origin, channel_sender_id,
channel_sender_name, channel_message_id
```

This struct has **no `is_owner`, no `is_group`, no chat-type field at all.** `surface` here means product surface ("chat" vs "acp"), not DM-vs-group. Every channel that funnels through `sage_turn_adapter.execute_sage_turn()` (`server_modules/sage_turn_adapter.py:41`) — which per its own docstring is meant to be "the SINGLE entry point for ALL Main Agent channels" — only carries `channel_sender_id`/`channel_sender_name`/`channel_origin` through this struct. Owner/group signal, where it exists at all, is threaded **outside** this struct as ad-hoc kwargs (`is_owner`, `is_group`, `chat_label`) that only `personal_channel_sage_bridge_service.py`'s `_build_unified_sage_personal_reply_async` (`server_modules/personal_channel_sage_bridge_service.py:377-480`) understands, and it converts them into a **text prefix baked into the message string** before the LLM ever sees it (`_owner_provenance_message`, lines 150-197; `_personal_channel_guard_metadata`, lines 237-263) — not a structured field the model or any downstream code can query.

`agent_channel_router.py` (Slack/Discord/GitHub/Telegram-Studio path) has its own, separate, even weaker convention: `_studio_channel_context_prefix()` (`server_modules/agent_channel_router.py:179-228`) will prepend `"[Posted in <type> \"<label>\", a shared channel...]"` to the message text **only if** the connector's metadata dict happens to contain `is_group`/`chat_type`/`chat_label` keys. Per that function's own docstring (lines 200-208) and confirmed by the Slack/Discord audit below, **no live connector actually sets those keys today** — so this is dead/unexercised plumbing for those channels.

### Per-channel findings

**Telegram (Personal / full-account, GramJS session — `empyralis-gateway/src/channels/telegram/runtime.ts`)**
- Sender id: yes — real per-message Telegram user id (fixed by commit `15923cb42`, see §5).
- Surface: yes, and thorough — `is_group` (private/group split), `is_mentioned` (entity-based @mention only, not the raw platform "mentioned" flag), `is_reply_to_sage` (per-chat-scoped sent-message-id map), `isBroadcastTelegramChat()` (`runtime.ts:606`) hard-excludes channel posts before sender resolution even runs.
- Owner-detect: yes — `is_self_chat` (`runtime.ts:1516`), i.e. Telegram "Saved Messages".
- Verdict: **best-covered channel in the codebase.**

**Telegram (Hosted bot, Bot API — `server_modules/sage_telegram_hosted_service.py`)**
- Sender id: yes, per-message (was collapsed to `chat_id` before `15923cb42`, now fixed at 7 call sites).
- Surface: yes — `chat_type` field (`sage_telegram_hosted_service.py:838`); group/supergroup addressing gated by `_GROUP_ADDRESSING_CHAT_TYPES` (line 1465) requiring an explicit mention/reply (fix `9fd84136f`); pairing (`verify_and_pair`) is hard-restricted to `chat_type == "private"` (line 956).
- Owner-detect: implicit, not explicit — the whole channel is architected as **one paired Telegram chat per workspace** (`_SAGE_HOSTED_PAIRS`, keyed by `chat_id`), so "owner" = "whoever is in the one paired chat." There is no per-sender owner check inside a paired chat.
- This is a **materially different implementation** from Telegram-Personal (different file, different auth model, different "owner" concept) — worth remembering the "Telegram" row below is really two systems.

**WhatsApp (`empyralis-gateway/src/channels/whatsapp/message-mapper.ts`)**
- Sender id: yes — `sender_jid` (differs from `remote_jid` inside a group).
- Surface: yes — `is_group: isGroup` (`message-mapper.ts:212`), `is_mentioned` (line 213).
- Owner-detect: yes — `is_self_chat: ownedJid ? remoteJid === ownedJid : false` (`message-mapper.ts:211`).
- Verdict: on par with Telegram-Personal.

**Signal (`empyralis-gateway/src/bridges/signal-cli-bridge.ts`)**
- Sender id: yes — `sender_jid = source` (line 355).
- Surface: yes — `is_group = Boolean(groupInfo.groupId)` (line 299); `is_mentioned` via `dataMessage.mentions[]` (lines 220-227, 311-313).
- Owner-detect: yes — `is_self_chat = fromMe && !isGroup && remoteJid === account` ("Note to Self", line 324), with an explicit echo-loop guard (lines 325-351) so the agent's own self-chat replies aren't re-ingested as new commands.
- Verdict: full parity with Telegram/WhatsApp. This is genuinely built, not a stub — confirmed by both the subagent's file reads and `docs/OpenClaw.md`'s own STEP-11 research log.

**iMessage (`empyralis-gateway/src/channels/imsg-imessage-runtime.ts`, the default runtime since the `imsg`-adoption work; legacy `bridges/bluebubbles-bridge.ts` webhook path still exists)**
- Sender id: yes — `sender_jid = message.sender`.
- Surface: yes for group — `is_group = message.is_group === true`. `is_mentioned` is **hardcoded `false`** — imsg's RPC payload has no mention field.
- Owner-detect: **NO — broken.** `imsg-imessage-runtime.ts:497-515` builds the inbound payload and never sets `is_self_chat`, even though the protocol type supports the field. Downstream, `personal_channels_service._is_owner_message` (`server_modules/personal_channels_service.py:919-957`) falls back to `_channel_owner_linked_id()` (`personal_channels_service.py:871-882`) when `is_self_chat` is absent — and that function **only has branches for `whatsapp_personal` and `telegram_personal`**:
  ```python
  def _channel_owner_linked_id(*, channel_key: str, state):
      if channel_key == WHATSAPP_PERSONAL_CHANNEL_KEY: return str(state.get("linked_jid") or "").strip()
      if channel_key == TELEGRAM_PERSONAL_CHANNEL_KEY: return str(state.get("linked_user_id") or "").strip()
      return ""
  ```
  (`personal_channels_service.py:878-882`) — for `imessage_personal` this always returns `""`, so `_is_owner_message` always returns `False`. **`is_owner` is permanently False for every iMessage message, including the owner's own texts to themselves.** The owner always gets the "external/untrusted sender" prompt-injection wrapper (`guard_personal_gateway_inbound_message`), never the clean owner provenance header, and the owner-unified cross-channel memory (`_owner_unified_conversation_key`) is unreachable from iMessage.

**WeChat — two completely different, non-overlapping implementations exist**
- `wechat_personal` (local-bridge, meant to be architecturally identical to Signal/iMessage): declared in `local-bridge-runtime.ts:138-146` but **has no bridge binary anywhere in the repo** — only a test harness. Vaporware; not reachable in production.
- `wechat_official_service.py` (Official Account / WeCom cloud webhook — the one actually live): `routes_wechat_official.py:103` → `handle_inbound_callback` (`wechat_official_service.py:717`).
  - Sender id: yes — `remote_jid = sender_jid = fields["FromUserName"]` (lines 391, 400).
  - Surface: **no group/broadcast concept at all** — `map_wechat_inbound_message` (lines 374-404) never sets `is_group`. It's a 1:1 customer-to-official-account API by Tencent's design, but the code doesn't even carry the field.
  - Owner-detect: **NO.** `from_me` is hardcoded `False` (line 403). More importantly, this channel **bypasses `personal_channels_service.py` entirely** — no `_is_owner_message`, no `_enforce_dm_policy`, no dm-policy gate of any kind. It calls `sage_command_dispatcher.dispatch_command` / `sage_reply_dispatcher.dispatch_sage_reply_safe` directly (`wechat_official_service.py:759-773`).
  - See §2 for the thread-collapse consequence, which is worse than a plain missing owner-check.

**Slack (`server_modules/connectors/slack_connector.py`, webhook `connectors_actions.slack_events_webhook`)**
- Sender id: yes — raw Slack `user_id` (`slack_connector.py:647`). **No display-name resolution** — `actor_display_name` is set to the raw user-id string (`connectors_actions.py:1203`), never resolved via Slack's `users.info` despite the app requesting `users:read` scope.
- Surface: computed (`channel_type`, `message_type` — `slack_connector.py:652`, `723-759`) but **discarded before reaching the model** — `route_inbound_channel_message` hardcodes `surface="chat"` (`agent_channel_router.py:354`) and the Slack webhook handler never populates the `is_group`/`chat_type` metadata keys `_studio_channel_context_prefix` would need to say anything (`connectors_actions.py:1204-1212` only carries `slack_team_id`/`slack_channel_id`/`slack_thread_ts`).
- Owner-detect: **NO. Zero occurrences** of `is_admin`/`is_owner`/`is_primary_owner`/`workspace_owner` anywhere in `slack_connector.py`, `connectors_actions.py`, or `agent_channel_router.py`. Every Slack sender — workspace admin or brand-new guest — is treated identically.
- Note: `slack-app-manifest.json:53` declares a request URL (`/api/sage/slack/inbound`) that does **not match** the live mounted route (`/channels/slack/events`) — the manifest is stale relative to the code.

**Discord (`server_modules/connectors/discord_connector.py`, `discord_bot_runtime_service.py`)**
- Sender id + username: yes — real `user_id` and `username` from `data.author` (`discord_connector.py:831-832`), better than Slack.
- Surface: better than Slack — `message_type` distinguishes `direct_message`/`mention`/`message`, with explicit Group-DM detection (`_is_group_dm_message`, lines 1092-1112) so a multi-party Group DM isn't misread as a private 1:1. Same discard-before-the-model problem as Slack (`agent_channel_router.py:354` hardcodes `surface="chat"`; Discord webhook metadata never sets `is_group`/`chat_type`).
- Owner-detect: **NO for guild/mention traffic** (same zero-hits grep). **Partial exception for true 1:1 DMs**: `_handle_dm_via_gateway` (`discord_connector.py:1115`) requires `/pair CODE` (`discord_pairing_service.pair_discord_workspace`) — but nothing in `discord_pairing_service.py` enforces uniqueness, so **multiple different Discord users can independently pair to the same workspace**, and nothing distinguishes "the person who deployed this agent" from "any Discord user who successfully ran `/pair`."
- Dead code: `agent_turn.py:1134` (`build_discord_turn_request`) and `discord_connector.py:950` (`dispatch_inbound_event`, which *does* stamp a real `owner_user_id`) are **never called by any live path** — confirmed by repo-wide grep, referenced only from exports/tests. The live Discord traffic never touches `agent_turn.py` at all.

### Summary table

| Channel | Sender id | Surface (DM/group/broadcast) | Owner-detect | Shared-vs-separate history |
|---|---|---|---|---|
| Telegram Personal | Yes (per-message, fixed) | Yes — group/mention/reply/**broadcast**-exclude | Yes — `is_self_chat` | Owner-unified across personal channels; per-(channel,chat) otherwise |
| Telegram Hosted (bot) | Yes (per-message, fixed) | Yes — `chat_type`, group-addressing gate | Implicit (1 paired chat = owner) | `sage-main-{workspace}` — safe only because pairing is 1:1 |
| WhatsApp | Yes (`sender_jid`) | Yes — `is_group`, `is_mentioned` | Yes — `is_self_chat` | Same owner-unified system as Telegram Personal |
| Signal | Yes (`source`) | Yes — `is_group`, `is_mentioned` | Yes — `is_self_chat` + echo guard | Same owner-unified system |
| iMessage | Yes (`sender`) | Partial — `is_group` yes, `is_mentioned` always false | **NO — always False** (see above) | Per-(channel,chat) only; owner-unified path unreachable |
| WeChat (Official Account — the only live path) | Yes (`FromUserName`) | **NO — no field exists** | **NO — bypasses the owner/DM-policy system entirely** | **Collapsed — every customer shares one thread** (§2) |
| Slack | Yes, no display-name resolution | Computed but discarded before the model | **NO — no concept of workspace owner** | Collapses to `"sage-main"` absent a bound specialist |
| Discord | Yes + username | Computed but discarded before the model (except Group-DM flag) | Partial — DM-only via unenforced `/pair` | Collapses to `"sage-main"`; DM path hardcodes `thread_id="sage-main"` explicitly |

---

## 2. One history per agent, or many?

There are **three different, only-partially-overlapping "history" systems**, not one.

**(A) The SQL thread store** (`control_plane_repository.agent_threads`/`agent_turns`, written via `thread_service.py`, the thing the Work tab reads). Thread-id derivation for a channel-originated turn happens in `sage_turn_adapter.execute_sage_turn` (`server_modules/sage_turn_adapter.py:148-166`):

```python
if not resolved_thread_id and resolved_channel_origin:
    _spec_agent_id = ...
    if _spec_agent_id:
        resolved_thread_id = agent_sender_thread_id(_spec_agent_id, resolved_sender_id)   # "agent:<agent_id>:<sender_id>"
    else:
        resolved_thread_id = await get_active_thread(resolved_workspace_id, resolved_channel_origin)  # workspace.channel_active_threads[channel] or "sage-main"
if not resolved_thread_id:
    resolved_thread_id = "sage-main"
```

- If a **specialist agent is bound** to the channel: thread = `agent:<agent_install_id>:<sender_id>` (`server_modules/sage_command_dispatcher.py:190-207`) — genuinely per-(agent, sender), so a Telegram DM and a Slack DM from the same agent's two different specialists/senders never collide.
- If running as **Sage/master with no specialist bound (the default, common case)**: thread = `get_active_thread(workspace_id, channel_origin)` (`sage_command_dispatcher.py:210-243`), which reads `workspace.channel_active_threads[channel_origin]` and **falls back unconditionally to the literal string `"sage-main"` if no override is set** — for *any* `channel_origin`. Since no channel sets an override by default, **every channel (Telegram, WhatsApp, Slack, Discord, WeChat, the web console) writes into the exact same SQL thread `"sage-main"` for that workspace.** The `channel` column on that thread is a single, last-writer-wins string (`server_modules/control_plane_repository.py:11084`: `channel = EXCLUDED.channel` on every upsert) — this is the literal mechanism behind the "console read as a Telegram customer" bug fixed in `0538b1275` (see §5).

**(B) `agent_conversation_memory` (per-agent JSONL, fsync'd)** — the *actual conversational context the model sees* for personal channels, completely independent of (A). Computed in `personal_channel_sage_bridge_service.py:490-512`:

```python
_owner_unified_key = _owner_unified_conversation_key(_mem_agent)   # "owner:direct:<agent_id or _sage>"
_is_owner_direct_dm = bool(is_owner) and not bool(is_group)
_mem_key = _owner_unified_key if _is_owner_direct_dm else f"{surface_channel}:{remote_jid}"
```

This means: **the owner's own 1:1 DMs to the agent — across Telegram-Personal, WhatsApp, and Signal (the three channels where `is_owner` actually fires) — ARE one continuous shared history**, keyed identically regardless of which of those channels the owner used. This is a genuinely-built cross-channel feature (commit `b62317259`, "unified cross-channel owner memory"). Everyone else (non-owner senders, and *any* group chat even with the owner present) gets a separate per-`(channel, remote_jid)` silo — never shared. iMessage can never reach the owner-unified key (owner-detect is broken there, §1). This memory system is loaded and passed as `channel_prior_messages` (`personal_channel_sage_bridge_service.py:531`) — **only by this one caller**; grep confirms no other channel wrapper supplies `channel_prior_messages` at all.

**(C) Everyone else falls back to (A)'s turns as context.** `sage_agent_runtime_service.py:4025-4028`:
```python
if channel_prior_messages is not None:
    prior_messages = list(channel_prior_messages)
else:
    prior_messages = instruction_bundle.prior_messages or []   # sourced from the SQL thread (A)
```
Slack, Discord, Telegram-Hosted, GitHub, and the web console never pass `channel_prior_messages`, so their conversational context comes from thread (A)'s own turns — which, per the finding above, is **the same shared `"sage-main"` thread for all of them by default.** Practically: absent a bound specialist agent, a Slack message and a Discord message to the same workspace's Sage master can genuinely see each other's turns as prior context, because they're reading the same SQL thread.

**A group chat does get its own thread/silo** — confirmed for all channels that set `is_group` correctly (Telegram, WhatsApp, Signal): `_mem_key` always falls to the per-`(channel, remote_jid)` branch for a group turn even from the owner (line 507: `_is_owner_direct_dm = is_owner and not is_group`), so groups are never folded into any owner-unified thread. WeChat has no groups. iMessage inherits per-chat silo behavior by default (never reaches owner-unified regardless).

**WeChat is a special, worse case**: `wechat_official_service.py` calls `dispatch_sage_reply_safe(...)` without a `thread_id` argument at all (`wechat_official_service.py:769-773`), which defaults to the literal `"sage-main"` (`server_modules/sage_reply_dispatcher.py:407`). `dispatch_command` similarly hardcodes `thread_id="sage-main"` (`wechat_official_service.py:761`). Because a non-empty `thread_id` is passed in, `execute_sage_turn`'s per-sender derivation (`if not resolved_thread_id`) never fires — **every distinct WeChat customer (every `openid`) of a given workspace's Official Account shares the identical SQL thread and turn-serialization lock** (`f"{workspace_id}:sage-main"`, `sage_reply_dispatcher.py:56-76`). `sage_agent_runtime_service.py:4806-4814` has its own comment acknowledging this class of bug in general terms ("channel turn thread_id is frequently a shared, UNSCOPED value ... CROSS-CONTAMINATE it"). Telegram Hosted has the textually identical `thread_id="sage-main"` omission but is safe in practice because that channel enforces exactly one paired chat per workspace (`_SAGE_HOSTED_PAIRS`); WeChat has no equivalent constraint, so this is a real cross-customer conversation leak, not just a theoretical one.

---

## 3. /commands

There are **two, unrelated command systems**, one live and one dead.

**Live system: `server_modules/command_registry.py`.** Single registry (`register()`/`_registry`/`_handlers`), invoked from `sage_turn_adapter.execute_sage_turn` (lines 190-232, via `command_registry.process_message`) for every channel that goes through the unified turn pipeline, and directly via `command_registry.dispatch()` from `sage_command_dispatcher.py` for "every customer-facing channel — Telegram, Discord, WhatsApp, Slack, WeChat, iMessage" (comment at `command_registry.py:371`). Commands registered (`_register_builtins`, lines 444-499):

- Open to anyone: `/new`, `/main`, `/compact`, `/stop`, `/clear`, `/export`, `/model`, `/thinking`, `/help`, `/commands`, `/tools`, `/status`, `/whoami`, `/usage`, `/memory`, `/forget`, `/tasks`, `/agents`, `/skills`, `/tts`
- **Owner-gated (`access="owner"`)**: `/config`, `/mcp`, `/plugins`, `/debug`, `/bash`

Owner check: `_is_sender_owner(sender_id, workspace_id)` (`command_registry.py:139-188`) reads `workspace.identity_links[channel_type] = {user_id, sender_hash}` and matches the inbound `sender_id` against any linked channel — **channel-agnostic, fails to `False` (not-owner) on any lookup error.** Enforced in **two places** for defense in depth: once in `process_message` before dispatch (line 273-280), and again inside `dispatch()` itself (lines 376-379, explicitly because `sage_command_dispatcher.py` calls `dispatch()` directly and would otherwise skip the check). This is real, working code — not a stub. There is no `/pause` command; the task's example was hypothetical.

**Note:** this `identity_links`-based owner check is a **third, separate owner-identity mechanism**, distinct from `personal_channels_service._channel_owner_linked_id`/`_is_owner_message` (§1, keyed off per-channel-registration state, only WhatsApp/Telegram) and from Discord's `/pair`-based linkage (§1). Whether these three stay in sync was not verified in this pass — they are three different code paths reading three different pieces of state, which is itself worth flagging as an inconsistency risk even without a proven divergence.

**Dead system: `server_modules/personal_channel_thread_command_service.py`.** Defines `/new`, `/threads`, `/use`, `/status`, `/help` as *thread-management* commands (different semantics from the live `/new`/`/status` above — a genuine name collision, though never user-visible since this path is unreachable). `parse_thread_command()` is called exactly once in the whole codebase, from `channel_blocking_policy_service.check_personal_channel_control_command()` (`channel_blocking_policy_service.py:56`) — but only to stash the parsed result into an audit dict (`thread_command` key) for a *different* function (owner-only-command **blocking**, see below). **`build_thread_command_reply()` — the function that would actually produce a reply for these commands — is never called anywhere in the codebase** (grep confirms zero other references). Typing `/threads` or `/use <id>` into any personal channel today does nothing special: it silently falls through to the LLM as ordinary chat text (unless it happens to also match a live `command_registry.py` name).

**What `channel_blocking_policy_service.check_personal_channel_control_command` actually does** (wired into WhatsApp/Telegram-Personal/local-bridge at `personal_channels_service.py:2156, 2331, 2803`, called `_control_command_block_result`): it recognizes a fixed, separate set of "owner-only control words" (`_OWNER_ONLY_CONTROL_COMMANDS = {approve, config, connect, debug, deploy, model, policy, reset, restart, send, setup, system, tool, tools}`, `channel_blocking_policy_service.py:14-31`) and, if the sender's role isn't `"owner"`/`"admin"`, **blocks the message before it reaches the model at all** and returns a fixed denial string. If the sender IS authorized (or the word isn't in that owner-only set), this function is a no-op and the text proceeds to `command_registry`'s own gate above. This is a second, redundant, textually-different owner-only command list from `command_registry.py`'s (e.g. `send`/`connect`/`approve`/`policy`/`restart`/`setup` appear here but have no registered handler in `command_registry.py` at all — they're blocked-if-unauthorized but do nothing if authorized).

**Verdict:** commands work end-to-end for the live registry; they are gated for real (double-enforced even); but the codebase carries a fully-dead parallel command surface (`personal_channel_thread_command_service.py`) and a second, only-partially-overlapping owner-only word list (`channel_blocking_policy_service.py`) that blocks words the live registry doesn't even implement.

---

## 4. OpenClaw comparison

| Dimension | OpenClaw | Empyralis | Verdict |
|---|---|---|---|
| Canonical inbound envelope | Every channel plugin normalizes into one `ctxPayload` shape (`From`, `To`, `RawBody`, `SessionKey`, `ChatType: "direct"\|"group"`, `InboundEventKind`, `MediaType`) before ingress auth ever runs (`docs/OpenClaw.md` STEP 2). One shape, every channel. | No single shape. `NormalizedSageTurn` exists but lacks owner/group fields entirely; the fields that DO carry owner/group signal (`is_owner`, `is_group`, `chat_label`) are ad-hoc kwargs understood only by `personal_channel_sage_bridge_service.py`, and get collapsed into a text-prefix string rather than staying structured data. | **Missing.** |
| Group/mention gating | `resolveInboundMentionDecision()` is one shared function with named, per-provider-configurable policy inputs (`isGroup`, `requireMention`, `groupPolicy: open\|allowlist\|disabled`) (`docs/OpenClaw.md` GROUP/MENTION GATING section). | Each channel (Telegram gateway, WhatsApp gateway, personal_channels_service's group gate) reimplements its own group/mention gate independently — real duplicated logic, no `open`/`allowlist`/`disabled` axis; every group is implicitly "open," gated purely by mention/reply. | **Partial** — the individual gates are now solid (post `afdf884e4`/`16b4ba42b`) but there is no shared, configurable policy layer. |
| Owner vs. others | No general concept — OpenClaw's Signal channel explicitly has NO "self-chat is a command channel" notion at all; it blanket-drops every `syncMessage` as loop prevention (`if ("syncMessage" in envelope) return;`). | Empyralis-specific, deliberately built beyond what OpenClaw does: `is_self_chat` + linked-owner-id detection, extended to Signal in the researched pass. Where it's implemented (Telegram-Personal, WhatsApp, Signal) it's a genuine improvement over OpenClaw. | **Ahead of OpenClaw on 3 channels, absent on iMessage/WeChat/Slack/Discord.** |
| One-history-vs-many | Sessions are explicitly NOT cross-channel — `Session Key format: agent:<agentId>:<sessionKey>`, one session store per channel-derived key, no owner-unification across channels at all (`docs/OpenClaw.md` STEP 8). | Empyralis's owner-unified cross-channel memory (`_owner_unified_conversation_key`) is something **OpenClaw does not have** — a real product differentiator when it works (3 of 7 channels). But Empyralis also has the *unintentional* version of "shared across channels" — the `"sage-main"` SQL-thread collapse (§2) — which is a bug, not a feature, and looks similar to OpenClaw's per-channel isolation only by accident. | **Mixed: ahead by design on 3 channels, behind by accident (an actual bug) on the rest.** |
| /commands | One shared registry (`commands-registry-Brl2piP4.js`) dispatched identically whether the command arrives natively (a Telegram bot command) or as text (`/model` typed in chat); model-switch (`/model`) propagates to every channel because it writes to the single session-store entry that every channel's next message reads from. | `command_registry.py` is architecturally similar (one registry, `access="owner"` gating, dispatched from every channel) — genuinely good parity. But there is a second, fully dead command surface (`personal_channel_thread_command_service.py`) and a second, non-overlapping owner-word blocklist (`channel_blocking_policy_service.py`) that OpenClaw has no equivalent duplication of. | **Parity on the live system, worse on cruft/duplication.** |
| iMessage transport | In-process child (`imsg` via `child_process.spawn`), live JSON-RPC stdio stream, auto-start on channel enable, real preflight probe (`openclaw channels status --probe`) distinguishing 4 failure layers. | Separate manual process (`bluebubbles-bridge.ts`), 5s polling loop, no auto-start (`empyralis-gateway/package.json` has no `imessage:bridge` script), `/health` only pings BlueBubbles' own ping endpoint — cannot distinguish "bridge not running" from "wrong password" from "Messages signed out." | **Behind** (this was already the subject of a dedicated OpenClaw-parity research pass in `docs/OpenClaw.md`; not re-litigated in depth here). |

---

## 5. The owner-vs-group bug — current state

**Status: fixed for Telegram/WhatsApp/Signal specifically, via three real root causes (all merged to `main`), but the underlying attribution primitives are not universal — iMessage and WeChat never had the fix applied because they never had working owner-detection to begin with (§1).**

The recurring "family Telegram group" bug had three independently-diagnosed root causes, per commit `afdf884e4` (`fix(telegram): stop the agent treating the family group as the owner (3 root causes)`, merged to `main`):

1. `runtime.ts` read Telegram's native `mentioned` flag as "the agent was addressed" — but on a full-account (non-bot) session, that flag is true whenever *anyone* replies to the account owner, so any family member replying to the owner tripped the group-mention gate. Fixed by switching to entity-based @mention detection only.
2. `sentMessageIds` was a single global `Set`; Telegram message ids are small integers scoped per-chat, not globally unique, so a reply in one chat could numerically collide with an id Sage sent in a *different* chat, false-positiving `is_reply_to_sage`. Scoped to `Map<remoteJid, Set<messageId>>`.
3. `_owner_provenance_message` hardcoded "direct message" even for a group turn from the owner, and the non-owner branch carried no group signal at all — so the model was never told it was in a 20-person group and "translated" every message as if the owner DM'd it privately. Fixed with the `is_group`/`chat_label` framing now in `personal_channel_sage_bridge_service.py:150-263` (quoted in §1).

Follow-on commits closed adjacent gaps: `446213468`/`16b4ba42b` (hard-drop broadcast channel posts before any group/mention logic runs, both gateway and hosted-bot side — the "comments under every post" incident), `15923cb42` (hosted-bot pairing restricted to private chats; per-message sender id instead of collapsed `chat_id`), `9fd84136f` (group mention/addressing gate added across all three hosted-bot code paths), `aaadcfdf4` (the same four gaps independently re-fixed in the separate `cloud-session-manager` Telegram reimplementation, which had silently regressed all of them). **The identity check itself (`_is_owner_message`) was never the bug** — per `afdf884e4`'s own commit message, it was "sound" throughout; the bug was entirely in *group-vs-DM framing* around a correctly-identified owner.

**Does the guard actually work today?** Yes for Telegram/WhatsApp/Signal — verified: `_is_owner_message` (`personal_channels_service.py:919-957`) correctly returns `True` for an owner posting inside a group (their JID doesn't change), but `is_group` is threaded separately and independently into `_owner_provenance_message`, which now renders `"From: {name} (owner) · {channel} · message posted in the \"{group}\" group chat, visible to other participants who are NOT the workspace owner"` instead of silently saying "direct message." The model is told, in plain language, both who sent it AND that other people can see it.

**Is this guard universal, or Telegram-specific?** It is channel-agnostic in code (any caller of `_build_unified_sage_personal_reply_async` gets it) but **only actually fires where `is_owner`/`is_group` are correctly computed upstream** — which per §1 is Telegram-Personal, WhatsApp, and Signal only. iMessage's `is_owner` is always `False` (never reaches the owner-group distinction; every iMessage sender, owner included, gets the external/untrusted wrapper). WeChat has no group concept and bypasses this whole subsystem. Slack/Discord/GitHub run through the completely different `agent_channel_router.py` path, which has no owner concept at all (§1) — there is nothing there *to* misattribute-as-owner, but also nothing protecting a workspace admin's words from being read identically to any other member's.

**Is the console-vs-channel `channel`/`surface` stamp (commit `0538b1275`) consulted by the channels?**

**No — it is not even populated for channel turns, let alone consulted by them.** Verified directly:

- `agent_turn.py:1686-1687` stamps `metadata["channel"]` and `metadata["surface"]` only inside `handle_turn()`'s call to `thread_service.record_user_turn` — the code path used by the **web console / direct-chat / specialist "AgentTurnRequest" flow** (`SERVER_OWNED_DIRECT_CHAT_CHANNELS = {"web", "mobile"}`, `agent_turn.py:100`).
- Every actual channel turn (Telegram, WhatsApp, Signal, iMessage, WeChat, Slack, Discord, GitHub) runs through `sage_turn_adapter.execute_sage_turn` → `sage_agent_runtime_service.handle_sage_chat`, which is a **completely separate function that never imports or calls `agent_turn.py` at all** (confirmed by grep: zero references to `agent_turn` anywhere in `sage_turn_adapter.py` or `sage_agent_runtime_service.py`).
- `handle_sage_chat`'s own turn-persistence calls (five call sites: `sage_agent_runtime_service.py:4203, 4300, 4485, 4563, 5117`) all write `metadata={"channel": channel_origin or "sage", "request_id": ...}` — **`channel` only, never `surface`.** For example, line 4210: `metadata={"channel": channel_origin or "sage", "request_id": (request_id or None)}`.
- `_trace_surface()` (`agent_turn.py:246-250`) itself collapses every non-web/mobile/desktop/api channel to the literal string `"channel"` — even if it *were* wired up for real channels, it would not distinguish Telegram from Slack from a group vs. a DM. It only distinguishes "console" from "not console."
- The one consumer of the `surface` field is `frontend/lib/workspace/fleet/tabs/WorkTab.tsx:182,848` — a **human-facing display fix only**. The commit message for `0538b1275` says this explicitly: *"Addresses the recurring owner-vs-customer misattribution (the console half of it)."* It fixes what a human sees in the Work tab. It has zero effect on what the agent itself perceives about who it's talking to — that's governed entirely by the separate `is_owner`/`is_group` mechanism in `personal_channel_sage_bridge_service.py`, described in §1/§2, which predates this commit and is unrelated to it.

---

## Appendix: key files referenced

- `server_modules/agent_turn.py` — web console turn builder; `channel`/`surface` stamp (console-display only)
- `server_modules/thread_service.py` — thin wrapper over `control_plane_repository` thread/turn writes
- `server_modules/control_plane_repository.py:11032` — `ensure_agent_thread` (single last-writer-wins `channel` column)
- `server_modules/sage_turn_adapter.py` — the real unified channel entry point (`execute_sage_turn`, `execute_sage_turn_for_channel`)
- `server_modules/sage_agent_runtime_service.py` — `handle_sage_chat`, the actual LLM turn; five separate `record_user_turn`/`record_assistant_turn` call sites, none carrying `surface`
- `server_modules/personal_channel_sage_bridge_service.py` — the real owner/group provenance system (`is_owner`, `is_group`, owner-unified memory key)
- `server_modules/personal_channels_service.py:919-957` — `_is_owner_message`; `:871-882` — `_channel_owner_linked_id` (WhatsApp/Telegram only)
- `server_modules/command_registry.py` — live command system, `_is_sender_owner` (identity_links-based)
- `server_modules/personal_channel_thread_command_service.py` — dead command surface (`build_thread_command_reply` never called)
- `server_modules/channel_blocking_policy_service.py` — second, non-overlapping owner-word blocklist
- `server_modules/agent_channel_router.py` — Slack/Discord/GitHub/Telegram-Studio router; no owner concept
- `server_modules/channel_adapter.py:49-63` — `NormalizedSageTurn`, the closest thing to a canonical struct (no owner/group fields)
- `server_modules/wechat_official_service.py:717,759-773` — WeChat inbound; hardcoded `thread_id="sage-main"` cross-customer collapse
- `empyralis-gateway/src/channels/telegram/runtime.ts`, `.../whatsapp/message-mapper.ts`, `.../bridges/signal-cli-bridge.ts`, `.../channels/imsg-imessage-runtime.ts` — gateway-side field extraction per channel
