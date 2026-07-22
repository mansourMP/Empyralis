# Thread History + Memory Audit — what makes an agent an agent

**Date:** 2026-07-22
**Scope:** every place a deployed Empyralis agent's past conversations or durable
facts are stored and read back — the SQL thread store, the per-agent JSONL
conversation memory, and the memory notebook/tree. Report-only: no backend code
touched. Verified against live code on `main`, including the in-flight
"canonical inbound envelope" wave (uncommitted at time of writing — see `git
diff --stat` note in Part 0). Grounded in
`docs/design/inbound-attribution-audit.md` (the sender/surface audit this
builds on), `docs/design/inbound-envelope-design.md` (the envelope spec being
built right now), `docs/PLATFORM-MAP.md` Part 27 (cross-agent memory
isolation), and `docs/OpenClaw.md`.

**Bottom line up front:** the attribution audit found three uncoordinated
history/memory systems. They are still three uncoordinated systems today — the
in-flight envelope wave fixes WHO the model is told it's talking to (a header
+ a code-gate), but it does **not** touch WHERE that turn is stored or WHETHER
a saved fact remembers who said it. Long-term memory (the notebook/tree layer)
has **zero** attribution capability anywhere in its schema — not partially
built, not planned in the current wave, genuinely absent. And one system (the
SQL thread store) is, per five separate code comments already shipped in this
repo, **not durable in the deployment mode the fallback code assumes** — which
means the channels that still depend on it alone (WeChat, Telegram-Hosted,
Slack, Discord-guild, GitHub) can lose conversational continuity on every
backend restart, independent of any attribution question.

---

## Part 0 — the in-flight envelope wave, precisely, so this doc doesn't relitigate it

`server_modules/inbound_envelope.py` (new, uncommitted) defines the canonical
`InboundEnvelope` the attribution audit found missing: `platform`, `surface`
(`OWNER_SELF_CHAT|DM|GROUP|BROADCAST_CHANNEL|CONSOLE|API|UNKNOWN`), `sender
{id, display_name, is_owner: True|False|None, is_bot}`, `chat {id, title}`,
`addressed`. Two functions matter beyond storage: `envelope_allows_owner_commands()`
(code-level gate: owner authority only on `{OWNER_SELF_CHAT, DM, CONSOLE}` with
`is_owner is True`) and `render_envelope_header()` (the one-line header now
prepended to every model-facing message at the `execute_sage_turn` chokepoint,
`server_modules/sage_turn_adapter.py:294-301`).

As of this pass it is wired into: Telegram-Personal/WhatsApp/Signal/iMessage
(`personal_channel_sage_bridge_service.py:272-437`, iMessage's `is_owner`
still tri-states to `None` — unverified — because the gateway-side fix from
the earlier audit hasn't landed), WeChat Official (`wechat_official_service.py:756-810`,
**plus** a real per-customer SQL-thread fix — see Part 1A), Telegram-Hosted
(`routes_sage_telegram_hosted.py:241-254,386-397`,
`sage_telegram_hosted_service.py:1492-1508`), Slack
(`connectors_actions.py:1169-1191`), Discord guild/group
(`discord_bot_runtime_service.py:586-608`) and Discord DM
(`discord_connector.py:1195-1210`), and console/web
(`sage_chat_api.py:169-190`, `agent_turn.py:1529-1568` →
`direct_chat_service.py:334-348`).

**Not wired, confirmed by grep:** GitHub (`connectors_actions.py`'s
`github_events_webhook`, no `envelope` reference anywhere), and a second,
dormant Discord DM path (`connectors_actions.py:1591-1620`, the
"forward-compatible... not reachable via the standard Discord Interactions
endpoint" branch) which still hardcodes `thread_id="sage-main"` with no
envelope — same bug class as the pre-fix WeChat, just currently unreachable.

**What this wave does NOT do** (confirmed by reading every diffed file, not
just the design doc's own "non-goals" note): it does not change **where** a
turn is stored (Part 1's three systems, unchanged), and it does not thread the
envelope into **either** durable-memory system — `agent_conversation_memory.append_turn()`
has an unused `metadata` parameter that stays unused after this wave (Part 1B),
and `memory_write`/`memory_search` (Part 1C) take no sender-identity parameter
at all, wave or no wave. This matters for Part 3: the envelope is now computed
on nearly every turn, which makes wiring it into memory strictly additive
follow-on work, not a redesign.

---

## Part 1 — the three systems, precisely

### System A — SQL thread store (`agent_threads` / `agent_turns`)

**Tables** (`server_modules/control_plane_repository.py:453-498`):

```
agent_threads(id, tenant_id, workspace_id, owner_user_id, channel, title,
              status, metadata jsonb, created_at, updated_at, last_turn_at)
agent_turns(id, tenant_id, workspace_id, thread_id, session_id, request_id,
            role, status, content, run_id, actor jsonb, approvals jsonb,
            interventions jsonb, metadata jsonb, created_at, updated_at)
```

`agent_turns.actor` is JSONB and looks like an attribution field, but every
live writer populates it with the **agent's** identity, never the inbound
sender's: `actor={"user_id": actor_user_id or "sage", "name": actor_email or
"sage"}` / `actor={"user_id": _spec_install_id, "name": agent_label}`
(`sage_agent_runtime_service.py:4208-4217, 4305-4314`, five call sites total,
lines 4203-5122). There is no column or JSONB key anywhere in this table that
ever holds "which human sent the inbound message this turn is replying to."

**Thread-id derivation** (`sage_turn_adapter.py:159-177`, unchanged by the
envelope wave — confirmed: the wave adds `envelope=` as a parameter but the
thread-id `if/else` block itself has no `_envelope` reference):

```python
if not resolved_thread_id and resolved_channel_origin:
    _spec_agent_id = getattr(specialist_context, "agent_install_id", "")
    if _spec_agent_id:
        resolved_thread_id = agent_sender_thread_id(_spec_agent_id, resolved_sender_id)
        #   -> f"agent:{agent_id}:{sender_id}"  (sage_command_dispatcher.py:190-207)
    else:
        resolved_thread_id = await get_active_thread(workspace_id, channel_origin)
        #   -> workspace.channel_active_threads[channel] or "sage-main"
if not resolved_thread_id:
    resolved_thread_id = "sage-main"
```

`agent_sender_thread_id()` is genuinely per-(agent, sender) — this is exactly
the "room = counterpart" keying the target model wants, but it **only fires
when a specialist agent is bound to the channel**. The common case — running
as Sage/master with no specialist bound — always falls through to
`get_active_thread()`, which reads a single `workspace.channel_active_threads[channel_origin]`
pointer that defaults to the literal string `"sage-main"` for every channel
that hasn't set an override. **No channel sets an override by default.**
Practically: Slack, Discord-guild, GitHub, and (per Part 0) WeChat/Telegram-Hosted
whenever no specialist is bound, all still write into the same SQL thread
`"sage-main"` for a given workspace — this is unchanged by the envelope wave,
which threads `envelope=` through as a **new, separate** parameter alongside
this untouched thread-id logic (`sage_turn_adapter.py:189-193` sets `_envelope`
right after the thread-id block, doesn't feed it back into it).

**Durability — the finding that matters most for "is this system live":**
five separate code comments across the codebase, written independently over
time (not one author's opinion, corroborated by `runtime_database_url()` /
`sqlite_fallback_allowed()` logic in `server_modules/db.py:34-49`, which
allows a silent SQLite fallback unless `EMPYRALIS_DEPLOY_ENV`/`ORION_ENV`/`ENV`/`NODE_ENV`
resolves to a recognized production token — a token
`scripts/install-agent-computer.sh` does not appear to set, per grep — DATABASE_URL
availability is the actual live/dead switch, not re-traced end-to-end here)
state that this table is dead under that fallback mode:

- `sage_agent_runtime_service.py:4020-4023` — *"The control-plane thread store
  the bundle reads from is dead under SQLite-fallback prod (Postgres-only
  schema → tables never created → in-memory-only turns wiped on every
  restart)"*
- `personal_channel_sage_bridge_service.py:686-688`, `routes_conversations.py:9-12`,
  `sage_agent_runtime_service.py:4803-4804, 4929` — same claim, same root
  cause, cited as the explicit reason `agent_conversation_memory` (System B)
  was built.

This is not re-verified against live production topology in this pass (that
would need the actual deployed `DATABASE_URL` state, out of scope for a
code-only audit) — but it is not a stray comment either: it is the stated,
repeated design rationale for System B's existence, written by whoever built
it, and it directly implies that **every channel still depending on System A
alone for conversational continuity — WeChat, Telegram-Hosted, Slack,
Discord-guild, GitHub — inherits that risk**, independent of any attribution
question.

**Where it's read into a turn:** `sage_agent_runtime_service.py:4025-4028` —
`prior_messages = channel_prior_messages if channel_prior_messages is not None
else instruction_bundle.prior_messages` (the latter sourced from System A).
Only the personal-channel bridge ever supplies `channel_prior_messages`
(confirmed by grep, unchanged since the attribution audit) — so **every other
channel's actual conversational memory is System A**, not System B.

### System B — `agent_conversation_memory` (per-agent JSONL)

**File layout** (`server_modules/agent_conversation_memory.py:1-24, 72-79`):
`$EMPYRALIS_STATE_HOME/conversations/<workspace>/<agent>/<conversation>.jsonl`
— one physical file per (workspace, agent, conversation_key), append-only,
`fsync`'d per line (`append_turn`, lines 191-226), tailed on read
(`load_recent_turns`, lines 153-188, default window `DEFAULT_RECENT_TURNS=20`,
hard cap `MAX_TURNS_RETAINED=400`).

**Key derivation** — the ONLY caller is
`personal_channel_sage_bridge_service._build_unified_sage_personal_reply_async`
(lines 683-821):

```python
_owner_unified_key = f"owner:direct:{agent_id or '_sage'}"          # _owner_unified_conversation_key(), :206-218
_is_owner_direct_dm = is_owner and not is_group
_mem_key = _owner_unified_key if _is_owner_direct_dm else f"{surface_channel}:{remote_jid}"   # :709-714
```

This is genuinely the target shape already, for the channels that reach it:
**a DM from a verified owner is ONE key regardless of which personal channel
it came from** (Telegram/WhatsApp/Signal/iMessage-when-fixed all collapse to
`owner:direct:<agent_id>`) — real cross-channel continuity, not an accident.
**Everyone else — a non-owner DM, or ANY group even with the owner present —
gets `f"{surface_channel}:{remote_jid}"`**, i.e. per-(channel, room) exactly
like the target model's "a group is a room" rule already asks for. Groups are
never folded into the owner-unified key (`_is_owner_direct_dm` requires
`not is_group`, line 709) — correct by construction.

**Attribution inside System B — the gap, precisely:** `append_turn()` accepts
an optional `metadata: Optional[Dict[str, Any]] = None` parameter
(`agent_conversation_memory.py:191-198`) that would be the natural home for
`{sender_id, sender_name, is_owner, platform}`. **No caller in the codebase
passes it** — confirmed by grep across all three `append_turn(` call sites in
`personal_channel_sage_bridge_service.py` (lines 769, 787, 815), none of which
pass `metadata=`, even in the post-envelope-wave diff where an `InboundEnvelope`
is now constructed one function above (`_build_personal_channel_envelope`,
same file, lines ~272-330) and sits unused for this purpose. The only
attribution that survives into a GROUP-silo record is a hand-built text prefix
— `f"{push_name}: {raw_text}"` (line 763-767) — untyped, spoofable-by-display-name,
and **`load_recent_turns()` doesn't even read the `metadata` field back**
(lines 173-188 only extract `role`/`content` from each JSON line) — so wiring
attribution through here requires touching both the write side (3 call sites)
and the read side (1 function), not just adding a parameter.

**A fourth, dormant, differently-scoped system worth naming so it isn't
confused with System B:** `deployed_agent_conversation_memory` (Postgres
table, `control_plane_repository.py:1072-1087`, keyed by `(tenant_id,
workspace_id, deployed_agent_id, channel_key, external_user_id)` — already
per-counterpart!). This backs the **Deployed/Studio agents** product surface,
which `docs/PLATFORM-MAP.md` (~line 1818) states explicitly is **frozen, not
live**: *"the live-channel delivery... have been deleted as dead code... It
stays as a dormant, fully-built reference implementation."* Not part of the
live three-system tangle, but its schema is a useful existence proof that
per-counterpart keying with a real `external_user_id` column was already
designed once in this codebase — for a different, currently-inactive product
line.

### System C — memory notebook/tree (`memory_write` / `memory_search` / `memory_read` / `memory_list`)

**Two backends, both keyed by `(workspace_id, agent_install_id)` only**
(`docs/PLATFORM-MAP.md` Part 27.1, re-confirmed by direct file read this
pass):

1. **File/notebook layer** — markdown under
   `agent_workspace_context_dir(workspace_id, agent_install_id)`
   (`workspace_context.py:222`), `MEMORY.md` is the capped index (loaded every
   turn, first ~200 lines/25KB — `sage_instruction_compiler_service.py:298-314`),
   topic files loaded on demand via the `memory_search`/`memory_get` tools.
   Tools: `memory_read`/`memory_write`/`memory_list`
   (`agent_memory_tools.py:112-260`) — **none of the three take a sender
   identity, a channel, or an `is_owner` flag as a parameter.** `memory_write`'s
   only "who" concept is `actor = agent_install_id or agent_id` — again the
   agent's own identity, logged to the activity ledger
   (`agent_memory_tools.py:201-210`), never the human who prompted the write.
2. **SQLite layer** — `memory_entries` table, one physically separate `.db`
   file per `(workspace_id, agent_install_id)` (`agent_memory.py:268-274`):
   `key TEXT PRIMARY KEY, content TEXT, created_at REAL, updated_at REAL` —
   **no sender/source column exists in the schema at all**, so there is
   nothing to backfill without a migration.

**Verified isolation, verified gap.** Part 27's own security audit proves
agent-vs-agent isolation holds here (physically separate files/DBs, path
traversal defended, one real cross-agent leak found and fixed in
`skills_service.py`). That is a different axis from what this doc is
auditing: Part 27 asks "can agent A read agent B's memory" (answer: no, fixed).
This doc asks "does agent A's OWN memory know which of its many counterparts
(owner, a customer, a group member) told it a given fact" (answer: there is no
mechanism for that question to even be asked — the schema has no place to put
the answer). A fact written to `MEMORY.md` from a customer's WeChat message and
a fact the owner stated in Telegram self-chat are stored identically, with
identical trust weight, and nothing downstream (not `memory_search`, not the
`MemoryTab.tsx` frontend at `frontend/lib/workspace/fleet/tabs/MemoryTab.tsx`
— confirmed by grep, zero occurrences of `sender`/`attribution`/`is_owner`/`counterpart`
anywhere in that 371-line file) can tell them apart.

**Where System C is read into a turn:** `MEMORY.md`'s capped content is
injected unconditionally by `sage_instruction_compiler_service.py:298-314`
(every turn, every channel — this part IS universal, unlike Systems A/B).
Full-file/topic detail and semantic search are read on-demand only when the
model calls `memory_search`/`memory_get` mid-turn
(`sage_instruction_compiler_service.py:460, 469` — the system prompt
instructs the model to call these; `skills_service.py`'s dispatcher is the
backing implementation, `agent_install_id`-scoped since the Part 27 fix).

---

## Part 2 — the target model

### 2.1 Design principles (restated, not re-litigated — these are the
founder's standing rulings this model must satisfy)

- One agent = one durable identity. Attribution is supplied by the platform,
  never inferred by the model (`docs/design/inbound-envelope-design.md:3-7`,
  now code, not just a rule).
- The owner's DMs are ONE continuous conversation across every channel they
  use (`project_cross_channel_and_multiagent_requirements` ruling). A group
  is a room, never folded into that thread, even when the owner is in it.
- Groups: see-and-decide, not mention-gated; `[SILENT]` suppresses output,
  never suppresses perception (`project_channel_behavior_rulings`).
- Memory must be attribution-aware with save filters — this is an explicitly
  named follow-up task (task #37), not yet started anywhere in the codebase
  (confirmed: zero hits for any save-filter concept in `server_modules/` or
  `frontend/`).

### 2.2 Unified conversation-history key — generalize what System B already
proves works, stop relying on System A's collapse

System B's own key derivation (Part 1B) is **already** the shape the target
model wants for the personal-channel family. The recommendation is not a new
design — it's applying that same rule universally, using the `InboundEnvelope`
that (per Part 0) is now computed on nearly every channel's turn already:

```
counterpart_key(envelope, agent_id):
  if envelope.sender.is_owner is True and envelope.surface in
     {OWNER_SELF_CHAT, DM, CONSOLE}:
        -> "owner:direct:<agent_id>"                      # cross-channel, matches System B today
  elif envelope.surface in {GROUP, BROADCAST_CHANNEL}:
        -> "room:<platform>:<chat.id>"                     # a Slack channel ≠ a Telegram group; never merge across platforms
  else:  # DM from a non-owner, or CONSOLE/API from a non-owner
        -> "dm:<platform>:<sender.id>"
```

This single function should become the ONE thread/conversation-key resolver
for BOTH System A's `thread_id` (replacing the `get_active_thread()` →
`"sage-main"` fallback whenever an `envelope` is present — `sage_turn_adapter.py:168-177`
is the exact spot) and System B's `conversation_key` (replacing
`personal_channel_sage_bridge_service.py:709-714`'s local, personal-channel-only
version). Two call sites become one shared function; the "sage-main collapse"
for Slack/Discord-guild/GitHub closes as a side effect of routing their
already-computed envelopes (Part 0 confirms Slack/Discord already build one)
through it, with no new signal needed.

**Which store should be canonical?** Given Part 1A's durability finding,
System B (fsync'd JSONL, proven durable across restarts) should be the
conversational-memory source of truth for every channel, not just the
personal-channel family — System A keeps its role as the Work-tab-facing
audit/display record (title, status, approvals, interventions — real UI value
System B doesn't replicate) but should stop being anyone's only copy of what
was said. Concretely: extend `agent_conversation_memory.append_turn`/`load_recent_turns`
calls to Slack, Discord, WeChat, Telegram-Hosted, and GitHub the same way
`personal_channel_sage_bridge_service.py` already does — the plumbing
(`channel_prior_messages` parameter through `execute_sage_turn` →
`handle_sage_chat`) already exists and is channel-agnostic; only the callers
are personal-channel-specific today.

### 2.3 Long-term memory with attribution + save filters

The `InboundEnvelope` already carries everything needed
(`sender.id/display_name/is_owner`, `platform`, `chat.id/title`) — it simply
never reaches System C today. Recommended shape, additive to the existing
schema (no breaking change):

- **`memory_write`** gains an optional `source: Optional[Dict]` parameter
  (sender_id, sender_name, is_owner, platform, chat_id/title, turn timestamp)
  — threaded from the same envelope the model's context header already
  renders, so this is "pass the object one hop further," not new plumbing.
  Persisted as a YAML-frontmatter-style block per entry in the notebook files
  (mirrors Claude Code's own precedent — see 2.4) and as a new column/JSON
  field on `memory_entries` (a real migration, but one column).
- **Save filters**, the other half of task #37: a policy check before ANY
  `memory_write` actually persists — e.g. "only the owner's stated facts
  auto-save to MEMORY.md; a customer's or group member's statement requires
  either explicit owner confirmation or lands in a lower-trust, clearly
  labeled section." This is the memory-layer mirror of the header's own
  owner/non-owner distinction — right now NOTHING enforces it; a customer
  chatting with a WeChat-bound agent and an owner talking in Telegram
  self-chat have equal write access to the same `MEMORY.md`, gated only by
  the model's own judgment (never a code-level gate, unlike
  `envelope_allows_owner_commands()` for commands).
- **UI**: `MemoryTab.tsx` should render the `source` on each entry/topic file
  (small "via Telegram · owner" / "via WeChat · customer #4471" chip) — the
  read-side half of "own $, own subscription... it works," per the founder's
  cofounder-rigor standard: a memory the owner can't audit for WHO said it
  isn't trustworthy long-term memory, it's an unlabeled rumor mill.

### 2.4 Comparison — OpenClaw, Claude Code, ChatGPT (fresh, cited)

| System | Session/history keying | Cross-channel continuity | Attribution mechanism |
|---|---|---|---|
| **OpenClaw** | `agent:<agentId>:<sessionKey>`, sessionKey derived per-channel (`channel:accountId:chatId:threadId`); flat JSON store per agent (`docs/OpenClaw.md` STEP 8, lines 13-25) | **None by design** — "Sessions are NOT cross-channel — each channel has its own session key derivation" (`docs/OpenClaw.md:25`) | None found in the reference material studied — session keying has no owner/customer distinction at all |
| **Claude Code** (`code.claude.com/docs/en/memory`, fetched live 2026-07-22) | Auto memory: one directory per git repo (`~/.claude/projects/<project>/memory/`), `MEMORY.md` index (first 200 lines/25KB) + topic files, machine-local, shared across worktrees of the same repo | N/A — single-user, single-machine tool by construction; no multi-party concept exists to unify | **None** — the closest thing is a per-entry `modified` ISO-8601 timestamp added in v2.1.214 (freshness, not authorship); there is no second party whose identity would need recording, because Claude Code has exactly one user talking to it |
| **ChatGPT** (per current public documentation, searched 2026-07-22) | Two layers: discrete "saved memories" (user-editable list) + "reference chat history" (implicit recall across past chats); both toggleable independently in Settings → Personalization → Memory | Continuous by design within one user's account — again single-tenant per account, not a multi-party inbox | **None** — same reason as Claude Code: one account, one memory owner, no "who told me this" question to answer |
| **Empyralis today** | Three systems (Part 1), only System B does real per-counterpart keying, only for the personal-channel family | **Ahead of all three references** where it works — `owner:direct:<agent_id>` is real cross-channel unification neither OpenClaw nor (structurally can't apply to) Claude Code/ChatGPT has, because Empyralis agents are genuinely multi-party (owner + customers + group members) in a way none of these three references are | **None**, same as all three references — but for a materially different reason: the references don't need it (single party), Empyralis does (many parties) and doesn't have it yet |
| **Empyralis target (2.2/2.3)** | One resolver, all channels, `(agent, counterpart-kind, counterpart-key)` | Preserves the existing owner-unified win | `source` on every memory write, save filters gating who gets to persist a fact automatically |

The honest framing for the founder: **attribution is not a "catch up to
OpenClaw/Claude Code/ChatGPT" gap** — none of those three systems solves it,
because none of them faces it (each is single-party or explicitly
per-channel-isolated). It's a problem specific to Empyralis's actual shape —
one agent identity serving an owner AND a stream of external
customers/group-members through the same memory — and has to be designed
here, not copied.

---

## Part 3 — gap list, ordered

Respects what Part 0 already shipped; does not re-list anything the envelope
wave already closes.

1. **Wire the InboundEnvelope into `agent_conversation_memory.append_turn`'s
   existing `metadata` parameter** (and make `load_recent_turns` read it back).
   Lowest-effort item on this list — the parameter already exists
   (`agent_conversation_memory.py:198`), the envelope is already constructed
   at every call site that would use it
   (`personal_channel_sage_bridge_service.py:272-330`) — this is a ~10-line
   change per call site (3 write sites, 1 read site) that turns System B's
   group-chat text-prefix hack into real structured attribution, and gives
   System C something to eventually source from.
2. **Give `memory_write`/`memory_read`/`memory_list`/the `memory_entries`
   schema a `source` field.** The highest-value, currently-zero-coverage gap:
   long-term memory has no attribution mechanism at all, structurally, not
   as a bug. Needs a small migration for the SQLite column and a frontmatter
   convention for the notebook files (§2.3). Task #37's first half.
3. **Build the save-filter gate** (§2.3, second half of task #37): a
   code-level check before a memory write persists, keyed off
   `envelope.sender.is_owner`/`surface` the same way
   `envelope_allows_owner_commands()` already gates commands — right now
   ANY sender's statement can end up in `MEMORY.md` purely by the model's own
   judgment, with no enforced floor.
4. **Close the Slack/Discord-guild/GitHub SQL-thread collapse** by routing
   their already-built envelopes (Part 0 confirms Slack and Discord both
   construct one today) through the unified key resolver (§2.2) instead of
   `get_active_thread()`'s `"sage-main"` default. GitHub needs an envelope
   built first (currently has none — Part 0). This is the one item
   `docs/design/inbound-envelope-design.md`'s own "non-goals" section didn't
   defer — it's a direct, in-scope consequence of finishing the wave's own
   `sage_turn_adapter.py` chokepoint work, not a new feature.
5. **Extend `agent_conversation_memory` (System B) to every channel**, not
   just the personal-channel family — WeChat and Telegram-Hosted in
   particular currently have NO durable conversational memory of their own;
   they depend entirely on System A, which (Part 1A) multiple existing code
   comments state is not durable under the SQLite-fallback deployment path.
   This is a correctness/reliability gap independent of attribution: a WeChat
   customer's context can vanish on a backend restart today.
6. **Fix iMessage's `is_owner` at the gateway** (`imsg-imessage-runtime.ts`
   never sets `is_self_chat`) so its envelope's tri-state `None` becomes a
   real `True`/`False` — everything in Parts 2.2/2.3 that keys off
   `sender.is_owner is True` silently under-serves iMessage until this
   lands; it's a single upstream fix the whole rest of this list benefits
   from, already scoped by the original attribution audit (§1) and
   acknowledged, not yet fixed, in `inbound-envelope-design.md:57`.
7. **Wire GitHub into the envelope** (currently zero references) and **delete
   or fix the dormant `thread_id="sage-main"` Discord-DM branch**
   (`connectors_actions.py:1591-1620`) — small, but it's the same class of
   cross-sender collapse bug the WeChat fix (Part 0) just closed elsewhere,
   left open in a path that's merely unreachable today, not provably
   unreachable forever.
8. **Surface attribution in `MemoryTab.tsx`** (§2.3) — UI follow-on to #2,
   blocked on it, not urgent until the backend field exists.
9. **Consolidate the three separate owner-identity mechanisms** the original
   attribution audit flagged as a standing inconsistency risk
   (`command_registry.py`'s `identity_links`,
   `personal_channels_service._channel_owner_linked_id`, Discord's `/pair`) —
   not proven to have diverged, but three independent sources of truth for
   "is this the owner" is itself a latent risk the envelope's `sender.is_owner`
   should arguably become the single consumer-facing answer for, once all
   three are confirmed to feed it consistently.

---

## Appendix: key files

- `server_modules/inbound_envelope.py` — the new canonical envelope (uncommitted)
- `server_modules/sage_turn_adapter.py:159-177` — System A's thread-id derivation (unchanged by the envelope wave)
- `server_modules/sage_command_dispatcher.py:190-243` — `agent_sender_thread_id`, `get_active_thread`
- `server_modules/control_plane_repository.py:453-498` — `agent_threads`/`agent_turns` schema
- `server_modules/agent_conversation_memory.py` — System B, full module (283 lines read)
- `server_modules/personal_channel_sage_bridge_service.py:206-437,683-821` — envelope construction + System B key derivation + write sites
- `server_modules/agent_memory.py:259-330` — System C's SQLite `memory_entries` layer
- `server_modules/agent_memory_tools.py:112-260` — `memory_read`/`memory_write`/`memory_list` tool implementations
- `server_modules/sage_instruction_compiler_service.py:298-314,460,469` — where MEMORY.md and memory_search/get get read into a turn
- `server_modules/control_plane_repository.py:1072-1087` — `deployed_agent_conversation_memory` (dormant, different product surface)
- `frontend/lib/workspace/fleet/tabs/MemoryTab.tsx` — memory UI, currently attribution-blind
- `docs/PLATFORM-MAP.md:3122-3312` — Part 27, cross-agent memory isolation (agent-vs-agent, not sender-vs-sender)
