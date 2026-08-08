# Channel Gateway Hardening — OpenClaw-Informed Plan

**Status:** Active work order for a separate agent session. Not a permanent
doc — delete once the work lands, per CLAUDE.md's "design/audit docs are not
kept" rule. This is a handoff brief, not a historical record.

**Written:** 2026-08-07, by an agent session that spent several hours
investigating this with the founder (Mansur). Everything below is either
verified directly against source (this repo or OpenClaw's real repo/docs) or
explicitly marked as a decision made by the founder. If you're picking this
up fresh: trust the file:line citations, re-verify anything load-bearing
before you build on it (code drifts), and do not re-litigate the decisions
marked DECIDED below — bring anything you think is wrong back to the founder
rather than silently overriding it.

---

## 0. What Empyralis is — required context before you touch anything

**One sentence:** Empyralis is *the owned-context layer for a team, with
execution attached.*

Unpack that, because every decision below follows from it:

- **Not a coding tool.** Claude Code / Codex / Cursor are the execution layer.
  Empyralis is the layer *above* them. Never build a coding surface — no
  editor, no diff-authoring UI, no terminal emulator.
- **Not an agent-hosting service.** Hosting an agent is commodity ($5.99/mo
  on Hostinger). The durable asset is the team's own context — their
  documents, skills, memory, and work history — which compounds and which
  Linear structurally cannot hold and Anthropic won't give them ownership of.
- **Agents are teammates on a board, not autonomous horizon-workers.** A
  human always manages. The founder explicitly does not believe in
  unsupervised long-horizon agent work.
- **The board is the product; chat is only input.** Nothing of value may
  exist only in a conversation. If an outcome lives only in a chat log,
  that's a product bug.
- **Multi-tenant and hosted.** Many customers, RLS enforced across dozens of
  tables, credit metering, per-workspace isolation. This is the single most
  important fact when evaluating anything borrowed from single-operator
  projects like OpenClaw.

**The user:** someone who runs agents *on behalf of other people* — a
developer hosting agents for client businesses, a team lead whose teammates
consume the agent's output, a person setting up an agent for a family
member. They need the agent to do real work on real machines, and everyone
else to see the outcome without seeing the conversation. **Not** a solo
developer coding alone — that person already has Claude Code and needs
nothing here.

**Why channels matter to this product specifically:** channels are how a
*customer* or *family member* reaches an agent that someone else deployed for
them. That is precisely why over-broad channel reach is dangerous here in a
way it isn't for a personal single-operator assistant: the person messaging
the agent is frequently NOT the person who owns the account it runs on.

**Execution locality principle** (settled, applies to everything):
> Identity lives in the workspace. Execution happens where the agent is
> placed. The connection carries only jobs and results.

---

## 1. The incident that started this

The founder connected his personal Telegram account. His agent ended up
active in a large public Telegram group (not a DM). Someone posted; the
agent replied. People asked "who is this bot?" — the agent replied to those
too. Nothing stopped it. The account got banned/spammed.

**Root cause, confirmed in code, not guessed:** connecting a channel account
today authorizes the agent to react to *everything* on that account — every
group, every channel it's ever a member of. There is no scoping step between
"connect Telegram" and "the agent can reply anywhere." See §4 for exactly
where this breaks.

---

## 2. Standing product laws that constrain every decision here (from CLAUDE.md)

- **No approval system.** No approve/deny buttons, no approval-pending
  states, ever. Any fix here must be enforced structurally (a lookup, a
  gate), never "ask the model to be careful" or "ask the user to approve
  each reply."
- **Guardrails are named and narrow.** An enumerated list of high-consequence
  actions, never a blanket gate over everything.
- **A surface must earn its place.** Don't add UI unless it's genuinely
  needed.
- **No dead controls.** A control that can't currently do anything is not
  rendered.
- **Hardware attaches to its owner, never the project.** Per-machine
  opt-in, default off. The channel-account equivalent of this law is
  proposed in §6.
- **Conversations are private, work is shared.** Nobody but the owner sees
  how they talk to their own agent.

---

## 3. DECIDED: what to adopt from OpenClaw, and what not to

Deep source-level investigation was done against `github.com/openclaw/openclaw`
(MIT license, real repo, verified via GitHub API — not press coverage).

**DO NOT adopt or fork OpenClaw's gateway/core.** Their own docs state
directly: *"OpenClaw is not a hostile multi-tenant security boundary...
one trusted operator boundary per gateway."* Empyralis is multi-tenant, with
RLS across dozens of tables, credit metering, and per-workspace isolation.
OpenClaw's core assumes none of that. Their own incident record (CVE-2026-44112
"Claw Chain," CVSS 9.6 — sandbox escape via TOCTOU symlink race, plus a
client-controlled `senderIsOwner` flag never validated server-side; separately,
tens of thousands of exposed gateways leaking credentials) is exactly what
happens when a single-operator design gets pushed into shared/hosted
topologies. Do not repeat that mistake by importing their core.

**DO NOT `npm install` their channel adapters.** Their adapters
(`extensions/telegram`, `extensions/discord`, etc.) all depend on
`@openclaw/plugin-sdk`, whose own `package.json` declares
`"version": "0.0.0-private", "private": true`. It has never been published.
`@openclaw/telegram`/`@openclaw/discord` 404 on the npm registry. There is no
package to install — adopting them as-is means vendoring their entire pnpm
monorepo, which is not worth the coupling.

**DO read their source and port the *design*, MIT license permits this
outright (attribution required, nothing else).** This repo has already done
exactly this once — `server_modules/mention_gating_service.py` is an
explicit, working port of OpenClaw's `resolveInboundMentionDecision` formula,
and `server_modules/channel_sdk.py`'s own docstring says *"Mirrors the
OpenClaw plugin-sdk pattern."* `docs/OpenClaw.md` still exists in this repo
as the reference these files cite — **read it before re-deriving anything.**

**For channels Empyralis already has working code for (Telegram, WhatsApp) —
adopting OpenClaw would be a downgrade, not an upgrade.** Verified directly:
- WhatsApp: both sides use `@whiskeysockets/baileys` — identical library,
  nothing to gain.
- Telegram: Empyralis's gateway (`empyralis-gateway/src/channels/telegram/`)
  uses the `telegram` npm package — **full MTProto/GramJS, a real
  user-account client.** OpenClaw's `extensions/telegram` uses `grammy` —
  **Bot API only.** Adopting theirs would strip capability Empyralis already
  has (see §6 for why this capability still matters and how to scope its risk
  instead of removing it).

**For channels Empyralis does NOT have** (Matrix, LINE, Feishu, Microsoft
Teams, QQ, Zalo, Nostr, Twitch, and others — OpenClaw has 145 total entries
under `extensions/`) — **this is where reading-and-porting genuinely earns
its keep.** No existing Empyralis code to compare against, so cloning the
repo, reading `extensions/<name>/src`, and rewriting the protocol logic into
Empyralis's own `channels/<name>/` (same pattern as `mention_gating_service.py`)
is legitimate, low-risk, and saves real time on protocols nobody here has
touched. **This is lower priority than §4 — do not start here.**

**Hermes-agent (NousResearch) was also investigated and is explicitly NOT a
reference to build from.** Confirmed directly from their real `SECURITY.md`
and docs: their group-reply default is *inconsistent* across platforms
(Telegram defaults to replying to every message once a group is allowed;
Discord defaults to mention-only — their own docs call this "the previous
open-group behavior," a legacy tradeoff, not a deliberate choice). No
confirmed working loop/cascade protection exists — someone filed the exact
issue this incident describes and it was closed as "already present" with no
mechanism ever verified. OpenClaw is measurably more mature on every axis
checked. Do not spend further investigation time on Hermes.

---

## 4. DECIDED: the three-gate model (OpenClaw's proven design, ported to Empyralis's own code)

Verified directly from OpenClaw's docs (`docs/plugins/sdk-channel-ingress.md`,
`docs/plugins/sdk-channel-plugins.md`) that these three gates live in their
**core**, not their adapters — the adapters only supply platform facts (sender
id, chat id, was-mentioned). The gates themselves are a design to copy, not
code to import.

```
INBOUND MESSAGE
      |
      v
[GATE 1] DM POLICY — who can DM the agent at all?
         default: pairing required (unknown sender gets a
         code, owner must approve before ANY message is
         processed)
      |
      v
[GATE 2] GROUP POLICY — is THIS CHAT allowed to trigger
         a reply at all?
         a row must exist in a bindings table for
         (agent_id, channel, chat_id). No row = silence,
         before any model is invoked. Default: no rows.
      |
      v
[GATE 3] MENTION GATING — inside an allowed group, was
         the agent actually addressed (mention / reply-to-
         agent)?
         default: ON (matches OpenClaw's consistent
         default across every channel — do NOT default to
         Telegram's weaker "reply to everything" behavior)
      |
      v
   TURN STARTS -> Claude Agent SDK engine
```

**The owner's own explicit commands bypass all three gates entirely.** "Vale,
text John about the demo" is the authenticated owner directing the agent, not
an unprompted trigger — there is no chat-id lookup to perform, because
nobody is *triggering* the agent, the owner is *directing* it. Keep this path
completely separate from the inbound-trigger path; do not accidentally route
owner commands through the allowlist check.

### Current state of each gate in this codebase — verified 2026-08-07

**Gate 1 (DM pairing) — mostly built.**
`server_modules/channel_pairing_service.py` (977 lines): real HMAC/token
pairing flow. `server_modules/discord_pairing_service.py` (150 lines):
SQLite-backed DM→workspace pairing, its own comment says it "mirrors
Telegram's `get_workspace_for_chat` pattern." This gate is in reasonable
shape — verify it's actually wired on every channel before assuming it's
complete everywhere.

**Gate 2 (group allowlist) — THE ACTUAL BUG. Read-only, cannot be written.**
`server_modules/personal_channels_service.py` has a `group_policy` field
(`open` / `allowlist` / `disabled`, ~line 1222) — the exact axis OpenClaw
uses. The write function exists:
`_persist_agent_group_policy_config` (~line 1292). **It has zero callers
anywhere in the codebase** — confirmed by repo-wide grep, twice, on two
separate passes tonight. No route in `routes_personal_channels.py`. No
reference anywhere in `frontend/`. The read path (`_load_agent_group_policy_config`)
IS wired into 4 live call sites — so the mechanism works, it's just
permanently stuck on whatever the code default is, because nothing can ever
change it.

**Gate 3 (mention gating) — built, wired, defaulted wrong, cannot be
changed.**
`server_modules/mention_gating_service.py` (127 lines) is a real,
working, explicit port of OpenClaw's formula. `personal_channels_service.py`
has a `require_mention` field alongside `group_policy`. But:
`DEFAULT_REQUIRE_MENTION = False` (~line 1227) — and since Gate 2's write
path doesn't exist, this default can never be overridden by a real user.
This is the second half of why the incident happened: even if the agent had
somehow been correctly scoped to fewer chats, it would still have replied to
every message in them by default.

**Conclusion: this is not a redesign. It's finishing a write path someone
left half-built, plus flipping one default.**

---

## 5. THE ACTUAL WORK — ranked, each step independently shippable

```
[1] WRITE ROUTE FOR GATE 2           ~1 day
    Wire _persist_agent_group_policy_config (already exists,
    server_modules/personal_channels_service.py ~1292) to a
    real PATCH endpoint. This alone, even with no UI yet,
    makes the mechanism usable via direct API call.

[2] FLIP DEFAULTS                     ~1 hour
    DEFAULT_REQUIRE_MENTION: False -> True
    group_policy default: -> "allowlist"
    (OpenClaw's consistent default across every channel —
    do not carry forward Telegram's legacy-open behavior)
    BACKFILL: existing bindings should default to the new
    safe values, not silently inherit the old open behavior.
    Fail loud if a backfill decision is ambiguous — never
    silently misconfigure an existing agent's reach.

[3] UI FOR GATES 2 AND 3              ~2 days
    Per-channel settings surface: which chats are allowed,
    mention-only toggle. The read path already exists and
    is wired into 4 call sites — this is presentation work
    on top of a working mechanism, not new plumbing.

[4] VERIFY GATE 1 IS COMPLETE ON EVERY CHANNEL   ~half day
    Confirm channel_pairing_service.py's flow is actually
    invoked for every channel adapter, not just the ones
    already checked (Telegram, Discord).
```

**Do steps 1-2 first, even before UI exists.** They alone would have
prevented the incident. UI is what makes it usable by a non-technical owner,
not what makes it safe.

### 5a. THE GATES MUST COVER EVERY CHANNEL, NOT JUST THE PERSONAL-GATEWAY PATH

**This is the most likely way this work ships and still leaves the bug
live.** Empyralis has at least twelve distinct inbound channel paths, and
they do NOT all share one code path:

*Personal-gateway channels* (TypeScript `empyralis-gateway/`, on the owner's
own hardware): Telegram Personal (MTProto), WhatsApp Personal (Baileys),
WeChat Official, iMessage (two bridge strategies), Signal (`signal-cli`).

*Server-side "Studio connector" channels* (Python `server_modules/`):
Discord, Slack, Telegram Hosted (Bot API), WhatsApp Business/webhook, SMS
(Twilio), WeChat Official server-side, GitHub.

### THE ACTUAL MATRIX — audited 2026-08-07, file:line verified

**Personal-gateway channels** — all five share the same three Python gate
functions in `personal_channels_service.py` (`_enforce_dm_policy` :974,
`_enforce_group_policy` :1342 → `mention_gating_service` :1406). **Mechanism
is ENFORCED everywhere. The defaults are the problem:**

| Channel | Gate 1 | Gate 2 | Gate 3 |
|---|---|---|---|
| WhatsApp Personal | Enforced, **default `open`** (`DEFAULT_DM_POLICY_MODE`, :705) | Enforced, **default `open`** (:1226) | Enforced, **no-op** (`require_mention=False`, :1227) |
| Telegram Personal | same | same | same |
| Signal (bridge) | Enforced, **default `owner_only`** (:708-728) | Enforced | Enforced |
| iMessage (bridge) | same as Signal | Enforced | Enforced — but `is_mentioned` **never computed** (`bluebubbles-bridge.ts:345`, `imsg-imessage-client.ts:582`); reply-to-agent only |
| WeChat-personal | code exists, **no bridge ships** — dead entry |

**Server-side connector channels — THREE HAVE NO GATE 1 AT ALL:**

| Channel | Gate 1 | Gate 2 | Gate 3 |
|---|---|---|---|
| **Slack** | ⚠️ **NOT ENFORCED** — `channel_type == "im"` always returns True (`slack_connector.py:761`, *"A DM is never a group — always trigger"*). **No Slack pairing service exists anywhere in the codebase.** | fixed single-channel match only (:679-721) | Enforced (:781) |
| **SMS (Twilio)** | ⚠️ **NOT ENFORCED** — no pairing call anywhere in `sms_twilio_webhook` (`connectors_actions.py:1312-1441`). Any number that texts a bound number gets a turn. | N/A (no groups) | N/A |
| **WeChat Official / WeCom** | ⚠️ **NOT ENFORCED** — `handle_inbound_callback` (`wechat_official_service.py:731`) routes every signature-verified sender straight to dispatch. | N/A (1:1 by Tencent's contract, :775) | N/A |
| Discord DM | Enforced — `get_workspace_for_discord_user` (:1200-1206) | N/A | N/A |
| Discord guild | N/A | fixed guild/channel match (:869-897) | Enforced (:953) |
| Telegram Hosted | Enforced — `is_paired(chat_id)` (:944) | Enforced (same pairing, works for groups) | Enforced, **hard block** (`routes_sage_telegram_hosted.py:197`) |
| Telegram BYO bot | N/A by design | N/A | Enforced, hard block (:512-532) |
| WhatsApp Business (operator) | Enforced (`whatsapp_ingress_service.py:434`) | N/A | N/A |
| WhatsApp Business (public agent) | N/A | N/A | N/A — **routes to a dead end**: `_SAGE_CHANNEL_ORIGIN_MAP` (`agent_channel_router.py:123-129`) has no `"whatsapp"` key, so it returns `channel_unavailable` and never reaches the agent. Routing gap, not a security gap. |
| GitHub | N/A (HMAC webhook) | owner/repo match (:609-638) | N/A |

### CORRECTION TO THE CENTRALIZATION ASSUMPTION — read before designing

`sage_turn_adapter.execute_sage_turn` **is** the universal chokepoint (its own
docstring: *"Every channel... MUST route through this function"*), and
`chat_id` **is** reliably present there via `InboundEnvelope.chat.id`
(`inbound_envelope.py:113-117`) for every channel that has a group concept.
SMS is the only exception — it never constructs an envelope at all, which is
consistent with having no groups.

**But it does NOT and CANNOT retroactively enforce the gates.** All three
gates run *upstream*, per-channel, before this function is called. By the
time execution reaches the chokepoint, the caller has *already decided to
forward*. A gate added here can only re-check what a channel chose to send —
it cannot recover a decision a channel made wrongly and silently.

So centralizing means **inverting the flow**: channels stop deciding and
start forwarding raw platform facts (sender id, chat id, is_group,
was_mentioned, is_reply_to_agent), and one shared resolver makes the
allow/deny call. That is exactly OpenClaw's architecture — their
`resolveChannelMessageIngress` takes platform facts and returns a decision;
adapters never implement policy. **Design for that, not for a check bolted
onto the existing chokepoint.**

### Other findings that change the work

- **`mention_gating_service` has THREE callers, not two** — `personal_channels_service`,
  `slack_connector`, and `discord_connector.py:953`. An earlier grep missed Discord.
- **Signal/iMessage vs WhatsApp/Telegram are inconsistent at the gateway
  layer.** `local-bridge-runtime.ts:402-404` still hard-drops unaddressed
  group messages before they reach the backend. The equivalent gate was
  *deliberately removed* from Telegram/WhatsApp on 2026-07-23
  (`telegram/runtime.ts:1556-1571`, `whatsapp/runtime.ts:1344-1359`,
  both commented "backend decides now"). So the same unaddressed group
  message is silently dropped on Signal and answered on WhatsApp. Pick one
  posture deliberately.
- **A second, undocumented producer of Telegram traffic exists**:
  `cloud-session-manager/src/telegram/` → `handle_cloud_channel_inbound`
  (:3963). It has **no Gate 1 at all** (:4114-4120). Gates 2/3 were recently
  fixed in its JS layer (`hmac.js:21-33`, `inbound-handler.js:106-155`).
- **Two stale comments assert behaviour the code contradicts** — fix or
  delete them while you're here: `personal_channels_service.py:4028-4051`
  (claims the cloud-session gate is "presently a no-op" — it isn't anymore),
  and `routes_wechat_official.py`'s docstring (says the route is "NOT
  registered" — `server.py:261,407` registers it).

---

## 6. DECIDED, DESIGN ONLY (not yet built): scope account-mode by audience

Empyralis's data model already has an `audience` field on agents
(`owner | external`, `resolve_agent_audience` in `fleet_tools.py`). Proposal,
agreed with the founder, not yet implemented:

```
audience = owner     -> MTProto/personal-account mode allowed.
                         This is what makes "text my friend who's
                         never talked to my bot before" work — a
                         real capability Bot API cannot do (Telegram
                         blocks bots from cold-DMing strangers).
                         This is an extension of the owner's own
                         identity, by their own choice.

audience = external   -> BOT IDENTITY ONLY. No exceptions. An agent
(deployed for a business,   deployed for a client/team should never
team, or client)            have visibility into the owner's personal
                             DMs. This is also the lower-blast-radius
                             mode: if a bot misbehaves, Telegram bans
                             the bot; if a personal account misbehaves,
                             Telegram bans the OWNER'S REAL ACCOUNT —
                             which is what actually happened here.

ALL THREE GATES (§4) APPLY REGARDLESS OF MODE.
Gate 2 is nearly redundant for bot-identity mode (a bot can't be
added to a chat without a human choosing to add it) — it is
LOAD-BEARING for MTProto mode, since a personal account is
already a member of everything it's ever joined.
```

This is a real, separate piece of work from §5 (which fixes the gates
themselves). Scope it after §5 ships and is verified working.

---

## 6a. New channels — priority is the founder's market, not raw count

OpenClaw has 145 entries under `extensions/`; Empyralis has roughly seven
messaging channels. Adding breadth is real value, but **only after §5 ships**
— a new channel added before the gates are uniform just adds another
unguarded inbound path.

**Priority should follow this platform's actual users, who skew heavily
Chinese and Southeast Asian** (the founder's own customer conversations, plus
the managed-agent-hosting market these users come from). That makes the
high-value additions:

Verified 2026-08-07 against OpenClaw's real tree (162 top-level entries under
`extensions/`, cross-checked two ways). Libraries and sizes are real:

- **Feishu / Lark** — official `@larksuiteoapi/node-sdk` v1.71.1. 180 files,
  ~2.6MB. The default work platform for Chinese businesses; where a business
  agent for a Chinese client actually lives.
- **QQ Bot** — official `@tencent-connect/qqbot-connector` v1.2.0, plus
  `silk-wasm`/`mpg123-decoder` for QQ's Silk voice codec. 179 files, ~1.6MB.
- **LINE** — official `@line/bot-sdk` v11.2.0. 70 files, ~756KB. Dominant in
  Japan, Taiwan, Thailand. **Smallest effort of the high-value set.**
- **Zalo** — two adapters: official Bot API (53 files, no SDK dep beyond
  `zod`) and **Zalo Personal** via unofficial reverse-engineered `zca-js`
  with QR login (62 files) — structurally the same pattern as Empyralis's
  own Baileys/WhatsApp personal integration. Dominant in Vietnam.
- **Microsoft Teams** — official `@microsoft/teams.api`+`.apps` v2.0.14,
  `@azure/identity`. 137 files, ~1.9MB. Largest of the set.

**IMPORTANT — Empyralis is AHEAD of OpenClaw on WeChat.** OpenClaw has **no
WeChat adapter of any variant** — confirmed absent from all 162 entries, by
two independent checks. Empyralis's WeChat Official Account implementation
(gateway `channels/wechat/`, 8 files + `wechat_official_service.py`) has no
upstream equivalent to port from. WeChat Work / 企业微信 would have to be
built from scratch, not adopted.

Lower priority for this user base: Matrix (376 files), Mattermost (139),
Google Chat (87), Nextcloud Talk, Synology Chat, Twitch, IRC, Nostr, Tlon.
Confirmed absent from OpenClaw too (so not adoption candidates): Viber,
Instagram DM, Facebook Messenger, RCS, KakaoTalk, DingTalk, Skype, Bluesky,
Mastodon, XMPP, RocketChat.

**Method for adding any of them:** clone OpenClaw, read
`extensions/<name>/src`, and port the protocol logic into Empyralis's own
`channels/<name>/` — the same approach already used successfully for
`mention_gating_service.py`. MIT license permits this outright; keep their
copyright notice. Do **not** vendor their monorepo or depend on their
unpublished `@openclaw/plugin-sdk`. Every new channel must pass through the
same centralized gates from §5a on day one — never ship a channel that
bypasses them "temporarily."

---

## 7. Explicitly out of scope / do not do

- **Do not build a "loop breaker" / rate-limit counter as the primary fix.**
  It was considered and correctly rejected by the founder: it's a clever
  patch around a system that's structurally wrong, not a fix. "We cannot
  make systems clever to solve issues when the product is already
  intelligent — the system itself must not be wrong." If Gates 1-3 are
  correctly built, there is no cascade to catch. (A rate-limit backstop MAY
  still be worth having eventually as defense-in-depth — OpenClaw has one,
  scoped only to bot↔bot loops, explicitly stated to not cover the
  human-cascade case this incident actually was — but it is not the
  solution and should not be built instead of §5.)
- **Do not fork or vendor OpenClaw's gateway/core.** See §3.
- **Do not touch channels Empyralis doesn't have yet (Matrix, LINE, etc.)
  until §5 and §6 are done.** New channel breadth is not what caused the
  incident and does not fix it.
- **Do not build any approval-prompt UI.** Standing product law, see §2.

---

## 7a. Approval boundary — READ THIS BEFORE DEPLOYING ANYTHING

**Build freely. Test freely. Do not deploy to production without the
founder's explicit approval.**

Steps 1-2 of §5 change live agent behaviour: mention gating flips on, group
policy tightens to allowlist. Agents that currently reply in groups will go
quiet until their chats are explicitly allowed. That is the correct and
intended outcome — and it is exactly why a human must choose the moment it
happens, not an agent mid-run.

Specifically:
- Merging to `main` — fine, after tests pass.
- Deploying to production (`empyralis.ai`) — **founder approval required.**
- Any migration against the production database — **founder approval
  required**, and see the role-ownership note in §8.
- Backfilling existing channel bindings to the new defaults — **founder
  approval required.** This silently changes the reach of agents that are
  already running for real users.

If in doubt, build it, show the diff and the test results, and wait.

---

## 8. Operating notes for whoever executes this

- One agent = one worktree = one branch. See `docs/AGENT-OPERATING-RULES.md`.
- Never `git stash` while other agents may be running — worktrees share one
  `.git`, a stash pop can pull in someone else's uncommitted work.
- Tests need a disposable Postgres database with "test" in its name, with
  `DATABASE_URL` passed explicitly — never inherited from `.env`. See
  `frontend/scripts/start-e2e-backend.sh` and CLAUDE.md's "Testing the UI"
  section.
- **If you apply a migration to production, apply it as the app's own
  database role, not as the Postgres superuser.** A superuser-applied
  migration leaves the new table owned by the wrong role; the app can't
  alter its own table on boot and crash-loops. This happened once already
  tonight during this same work session — costs ~5 minutes to fix
  (`ALTER TABLE ... OWNER TO <app_role>`) but is avoidable entirely by not
  making the mistake.
- Never weaken a test assertion to make it pass.
- Read `docs/OpenClaw.md` (still in this repo) before re-researching
  anything already written up there.
