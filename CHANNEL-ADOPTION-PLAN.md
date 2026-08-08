# Channels: run OpenClaw as a transport, keep our own brain

**Status:** active. Decided 2026-08-08. Delete when the migration completes.
**Supersedes:** `CHANNEL-PORT-PLAN.md` — porting their code by hand. That plan
was an active work order to hand-carry ~190,000 lines of their TypeScript into
our codebase. It is **deleted**, not archived: an agent finding it and treating
it as present truth would start the exact program this file exists to cancel.

**Progress:** steps 1–3 merged to main 2026-08-08 (bridge plugin, inbound
wiring, outbound delivery). Durable findings from each live in `CLAUDE.md`,
not here — this file is the work order, `CLAUDE.md` is the memory.

---

## THE DECISION

Run OpenClaw's gateway as a **channel transport only**, one isolated instance
per customer machine, alongside Empyralis's own gateway. Its AI, memory,
skills and UI stay off. Empyralis's cloud remains the brain.

```
customer's machine
┌────────────────────────────────────────┐
│  Empyralis gateway   (ours, unchanged) │
│         ↕ local                        │
│  OpenClaw gateway    (theirs, NEW)     │
│    └─ 23 channels: WhatsApp, Telegram, │
│       Discord, Signal, iMessage, Slack,│
│       SMS, Feishu, QQ, LINE, Zalo…     │
│    └─ brain OFF, memory OFF, UI OFF    │
└────────────────────────────────────────┘
                  ↕ HTTPS
        Empyralis cloud — board, tasks,
        documents, agents, the actual product
```

## WHY (do not relitigate)

**Porting was measured and rejected.** Their 7 channels matching ours are
**1,229 source files / ~190,000 lines of TypeScript** (Discord alone: 397
files, 2.2MB). Empyralis's entire channel layer is ~20,000 lines. Porting
means hand-carrying 10× our current code into a different architecture across
two languages — a multi-month program where we'd own every bug we introduced.

**Their channels carry hardening we cannot buy any other way.**
`extensions/telegram/src/sendchataction-401-backoff.ts` is 240 lines for ONE
failure mode, citing issue #27092 ("the infinite loop that caused Telegram to
delete bots") and #94787 (why they read a structured `error_code` before ever
string-matching "401"). Our equivalent is one shared substring matcher across
all connectors — the exact "stale string matching" failure mode CLAUDE.md
already warns about.

**Founder's mandate, verbatim:** *"As long as it works as reliably as
OpenClaw and there is no gap in between. I'm ready to delete the entire thing
I had."*

## WHAT IS OURS, WHAT IS THEIRS — the line, and why it's there

```
THEIRS   channel transport only. a radio.
YOURS    board, tasks, documents, agents-as-teammates,
         workspace, execution locality, BYO subscription,
         the SDK engine, memory, skills
```

**Do NOT adopt their agent loop, memory, or skills.** Two reasons:

1. **Their own docs:** *"not a hostile multi-tenant security boundary — one
   trusted operator boundary per gateway."* Empyralis is multi-tenant.
   Channels fit (one isolated instance per customer). Their whole agent does
   not.
2. **Strategic:** OpenClaw has 23 channels, memory, skills, a plugin
   ecosystem — and **no board/workspace**. That gap is Empyralis's entire
   business. Adopting the thing that could close it hands them the position.
   The day they ship a team board, a full adopter is a wrapper around its own
   competitor.

Their "engine" is a harness around a model, same category as the Claude Agent
SDK we already run (Anthropic's own, extracted from Claude Code). Switching
harnesses buys different tradeoffs, not a better brain. **Keep the SDK.**

---

## FEASIBILITY — VERIFIED LIVE 2026-08-08

Verdict: **viable, with one ugly hack at the centre.**

Ran live against openclaw v2026.6.10 under an isolated `--profile radiopoc`:

```
headless boot                    ✓ no TUI, no browser, no prompts
  openclaw --profile <n> gateway run --port <p> \
           --bind loopback --auth token --token <t>
  NOTE: gateway.mode:"local" must be set in config or it
        refuses to start ("suspicious or clobbered config")

plugin loading                   ✓ via plugins.load.paths
plugin → external HTTP           ✓ PROVEN: gateway_start hook fired,
                                   POSTed to a mock Empyralis listener
message_received hook            ~ THIS IS THE TAP — but CORRECTED
                                   2026-08-08 (step 2): it is POST-GATE,
                                   not unconditional, and carries neither
                                   isGroup nor wasMentioned. See "THE
                                   UPSTREAM GATING ANSWER" below.
message_sending / message_sent   ✓ fire on every outbound; can rewrite
                                   content or return { cancel: true }
outbound send RPC / CLI          ✓ real, policy-enforced (clean rejection
                                   "unsupported channel" when unconfigured)
config generation                ✓ JSON5, fully programmable via
                                   `openclaw config patch --file <path>`
instance isolation               ✓ `--profile <name>` → ~/.openclaw-<name>
version pinning                  ✓ npm i -g openclaw@<version>
```

### THE HACK — read this before building

**There is no transport-only mode.** Two hooks look built for it; neither is:

- `inbound_claim` — only fires inside a pre-registered per-conversation
  binding (approval-gated opt-in). No "claim everything" switch.
- `before_agent_reply` — docs say it short-circuits the model turn. Shipped
  code gates it to cron: `if (params.trigger === "cron" && hookRunner?.hasHooks("before_agent_reply"))`
  (`embedded-agent-BgF2MOkH.js`). Does **not** fire for normal inbound.

**Workaround that works:** configure **no model provider credentials**. Every
agent turn then fails fast and cleanly (`FailoverError: No API key found`),
no hang, no crash, nothing sent to the user. Swallow that error reply via
`message_sending`, and deliver Empyralis's real reply out-of-band via the
`send` RPC.

It works. But the design centre is "make the turn fail on purpose," which
spins up a turn per message to discard it, and breaks quietly if that error
path ever changes.

**A feature request has been drafted** asking them for a real transport mode
(`agent.mode: "transport"`, or widening `before_agent_reply` beyond cron, or
a channel-level `inbound_claim`). Draft at
`scratchpad/openclaw-issue-draft.md` — founder to review and post. If they
say yes, the hack disappears.

### SECURITY LOCKDOWN — mandatory, per customer instance

```
bind            loopback only (default 127.0.0.1:18789) — NEVER expose
auth            token/password ALWAYS on, even on loopback.
                gateway.auth.mode must never be "none"
Bonjour/mDNS    ON BY DEFAULT — it advertised itself on the LAN
                unprompted during testing. TURN IT OFF.
isolation       one OpenClaw process per customer, never shared.
                Their trust model is single-operator.
audit           `openclaw security audit --fix` targets exactly these
                footguns — run it as part of provisioning
```

Context: tens of thousands of OpenClaw gateways were found internet-exposed
leaking API keys and chat history. Not a code flaw — people exposed them. If
we ship it to customers, we own that mistake on their behalf.

### UNVERIFIED — what would settle it

- **No live channel message has been tested through the tap.** The
  `message_received` call site is confirmed by source reading, not by a real
  Telegram/Discord message. **Settle it:** point one real bot token at a test
  channel and watch the plugin's event log.
- ~~**Open question:** whether a channel plugin's ingress calls the shared
  dispatch for senders who fail its own allowlist/mention gate, or drops them
  earlier.~~ **ANSWERED 2026-08-08 — see below. It drops earlier.**
- Which config paths hot-reload vs require restart (`config.schema.lookup`
  reports `reloadKind` per path — not yet surveyed).
- No config-migration changelog reviewed; check before committing to a pinned
  version long-term.

### THE UPSTREAM GATING ANSWER — settled 2026-08-08, from shipped source

**OpenClaw gates BEFORE the tap, and the tap gets no gate facts.**

```
inbound message
   |
   v
[OpenClaw channel ingress]  decideChannelIngress()
   |    message-access-CeqV-XzC.js:382
   |    admission: "drop" | "skip" | "pairing-required" | "admit"
   |    gate effects literally named "block-dispatch"
   |
   +-- drop / skip ---> RETURNS. never enqueued. never dispatched.
   |                    (Discord: message-handler.preflight-*.js:1009
   |                     Telegram: bot-*.js:4132 — on a mention miss it
   |                     fires ONLY the internal hook, never the plugin one)
   v
[dispatch-B2e1grFo.js:1240]  message_received hook  <-- OUR TAP
   |
   |  event = toPluginMessageReceivedEvent(canonical)
   |          NO isGroup.  NO wasMentioned.
   |          (the sibling toPluginInboundClaimEvent forwards BOTH —
   |           message-hook-mappers-DsSZ9hKY.js, same file)
   |          internal fact: isGroup = Boolean(GroupSubject || GroupChannel)
   |          only GroupChannel survives, as metadata.channelName
   |          => a Telegram/WhatsApp group looks like a DM at the tap
   v
Empyralis bridge plugin -> gateway loopback intake -> channel.inbound
```

Their own doc (`docs/plugins/sdk-channel-ingress.md`): *"A mention miss
returns `admission: "skip"` so the turn kernel does not process an
observe-only turn."*

Two consequences that change provisioning (step 4):

1. **WhatsApp suppresses this hook entirely** unless
   `channels.whatsapp.pluginHooks.messageReceived: true` is set
   (`docs/channels/whatsapp.md`, "Plugin hooks and privacy"). Nothing on the
   Empyralis side can compensate for its absence.
2. **OpenClaw's own config is a second policy store.** Its
   `dmPolicy`/`groupPolicy`/`requireMention` decide what we ever see; ours
   decide what gets a turn. Provisioning must generate the OpenClaw side
   from our database, or the two drift and the owner's Empyralis settings
   silently do not describe reality.

Empyralis's answer, implemented in step 2: unknown group-ness is treated as
a GROUP (`personal_channels_service.normalize_openclaw_gate_facts`), so it
lands on Gates 2/3 (allowlist + require-mention) rather than Gate 1 (default
open). No mention is ever derived from message text. Net effect today: an
OpenClaw message gets a turn only in a chat the owner explicitly allowlisted
with `require_mention` off — or once OpenClaw starts forwarding
`wasMentioned`, which the bridge schema and mapper already carry.

---

## IMPLEMENTATION ORDER

```
1. BRIDGE PLUGIN         our plugin inside their gateway.
                         message_received → POST to Empyralis
                         message_sending  → swallow the failed-turn reply
                         PoC exists: scratchpad/openclaw-poc/
                                     empyralis-bridge-plugin/

2. INBOUND WIRING        Empyralis endpoint receives their events,
                         maps to (workspace, agent, chat), runs the
                         three gates, dispatches a turn.
                         REUSE the existing gate machinery — see
                         CHANNEL-GATEWAY-PLAN.md. Do not rebuild it.

3. OUTBOUND WIRING       Empyralis calls their `send` RPC to deliver.

4. PROVISIONING          install + pin + configure + lock down their
                         gateway per customer machine, from our
                         gateway's existing supervisor path.
                         Config generated from our database.

5. ONE CHANNEL LIVE      prove it end to end with real credentials
                         before touching any other channel.

6. CUT OVER, ONE AT A TIME
                         port → verify → swap → delete.
                         NEVER delete a working channel before its
                         replacement is proven live. Big-bang swap is
                         how you create the gap we're closing.

7. NEW CHANNELS          Feishu/Lark, QQ, LINE, Zalo, MS Teams —
                         the founder's actual market (Chinese + SE
                         Asian users). Should be config, not code.
                         NOT Matrix/Nostr/IRC/Twitch.

8. UI                    official channel logos, connect flows,
                         and group-policy control (see below —
                         open question, not a committed screen).
```

## INDEPENDENT OF ALL THIS — still needed either way

**The three authorization gates need SOME way for an owner to set them.**
Right now it's raw API call only. This is the unfinished half of the
incident that started the whole channel investigation: an agent replied
unprompted in a public Telegram group and got the founder's account banned.

**Open question, per founder 2026-08-08: does this need a custom Empyralis
screen at all, or does OpenClaw's own config cover it once adopted?**
Two candidates, in order of preference — pick whichever is real once the
bridge exists, don't assume a custom screen is needed by default:

```
A. thin passthrough    expose OpenClaw's own per-chat/per-channel policy
                       fields (they already have this — it's how their
                       gateway decides who it replies to). We just need
                       a settings surface that reads/writes their config
                       via `openclaw config patch`, not a new model.

B. agent-editable       the agent itself can change its own group policy
                       via natural language / a tool call ("only reply
                       when mentioned in this chat") — no settings screen
                       at all. Consistent with "no approval-prompt UI"
                       and "a professional tool labels, it does not
                       lecture" — a policy toggle buried in a settings
                       page is exactly the kind of surface that has to
                       earn its place.
```

Do not build a bespoke Empyralis policy screen speculatively. Check what
OpenClaw's config already exposes first — building our own model of
something they already solved is the same mistake as porting their channels
by hand.

## ABANDONED WORK — do not resurrect without a reason

- `feat/openclaw-signal-port`, `feat/openclaw-discord-port` — partial ports,
  stopped mid-flight when the 190k-line number landed. Kept, not deleted.
- `parked/discord-personal-account` — 4,525 lines, a working personal-account
  Discord channel using `discord.js-selfbot-v13` (deprecated on npm, violates
  Discord ToS). Deliberately not shipped. OpenClaw ships Bot-API-only for
  Discord despite shipping ToS-grey personal integrations for WhatsApp and
  Zalo — they drew the line at Discord specifically.

## CONSTRAINTS (standing)

- No approval-prompt UI — product law.
- No `git stash` — concurrent agents share one stash stack.
- Disposable test databases only, `DATABASE_URL` passed explicitly.
- Never weaken an existing gate or honesty guard.
- Port → verify → swap → delete. Never the reverse.
