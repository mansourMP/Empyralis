# GOAL — close CHANNELS tonight, demo tomorrow

Three hours. One outcome: **the channels surface is finished** — every platform
has an honest, working setup path in the UI, nothing lies, nothing dead-ends —
so the demo can be recorded and the founder can go back to the rest of the
product.

Units, because getting this wrong already cost a day:
**PLATFORM** = a channel (Telegram, WhatsApp). **DOOR** = one way to connect
that platform (a bot, your own account, a QR). **PLUGIN** = the code that
provides a door. Never merge these into one number again.

---

## Already true (verified live today, not claimed)

| | evidence |
|---|---|
| Telegram in/out on the real account | ACK-1, ACK-2 exact-match, ~9s each, 0 failures |
| Command on real hardware, from Telegram | `whoami && hostname && date -u` → `root / ca2d629da650` |
| Command on real hardware, from the platform chat | `uname -a` → `Linux f88ab842a040`, exit 0 |
| Owner identity works on all 24 channel keys | envelope says "your owner", 22 tools, not "audience" |
| Sessions persist | 20 turns survive reload · 77 sessions · 40 traces |
| Chat renders properly | headings, tables, fenced code blocks |
| Connected channel = summary, not a form | Telegram panel: 0 editable inputs |
| WhatsApp QR renders from the real box | `web.login.start` ✓ 3133ms, rotates on expiry |
| Production is on today's code | `empyralis.ai` healthy, 41+ commits deployed |
| Engine | OpenClaw gateway for channels · Anthropic Agent SDK for turns |

---

## The three hours

### P0 — the surface must not lie (in flight)

1. **`unknown_link` over-promises.** iMessage, Signal, Twitch, Synology Chat
   and Tlon show the pill **"Scan a code"** when we do not yet know they can
   QR. iMessage is a local database; Signal is a device link. Pill must be
   neutral ("Set up") until the box says otherwise. DONE.
2. **Registry restored as DOORS, not a channel count.** DONE — `d07a86b7e`.
   Manifest is schema v2 and carries all **112** registry plugins as OFFERS,
   with their trust facts. The grid stays **27 platform cards**; an offer is
   never a card.

   **Correction to an earlier claim of mine:** I said 23 of these were doors
   onto platforms we already carry (WhatsApp 6, LINE 3, …). That number came
   from matching plugin LABELS, which is guessing. A plugin only declares
   `channels.<id>` once it is installed, so every offer sits at
   `plugin_absent` until a real box says otherwise. An honest unknown beats
   a confident wrong door. The real door count is unknown and will stay
   unknown until installation — by design.
3. **Trust on the door face.** Community plugins carry `isOfficial`,
   `verificationTier`, `scan_status`, install count in plain words —
   shown, never used to filter.

### P1 — every platform has an honest path

4. Sweep all 27 platform panels. Each must land on exactly one of:
   a real setup step · a QR · an honest "we cannot know until it is installed"
   · "could not reach this computer". **No dead ends, no mechanism words**
   (plugin / npm / package / binary) in customer copy.
5. `Weixin`, `WeCom`, `Yuanbao` currently show **"Unknown"**. Their plugin is
   not installed, and installing is possible — the pill should offer the
   action, not describe a void.
6. `Discord` and `Slack` show **"Not configured here"**. True (they are
   cloud connectors elsewhere) but it reads as broken. Say where.

### P2 — the founder's own hands (5 minutes, cannot be delegated)

7. **Scan the WhatsApp QR.** Channels → WhatsApp → Generate QR code →
   WhatsApp → Linked Devices → Link a device. This is the second independent
   proof of the pairing path.
8. **Decide on Telegram full account.** `telegram-userbot` (MTProto, replies
   as you) is not installed. Registry facts: community, `scan_status:
   suspicious`, no provenance, 9 installs, publisher `eldaruma`. It takes a
   real Telegram session on the box holding the working bot token.
   Recommendation: derive its shape on a throwaway profile first.

### P3 — only if P0–P2 are green

9. Production rehearsal on the founder's REAL account (everything proven
   today is on the local rig): agent placed on hardware, a channel connected,
   one message, one command, through `empyralis.ai`.

---

## Explicitly NOT doing tonight

- Anything involving the OLD gateway. Both gramjs runtimes and the Baileys
  runtime are deleted; `cloud-session-manager` is deleted. There is nothing to
  go back to and no reason to look.
- Building 24 bespoke setup screens. Steps come from OpenClaw's own
  `setupWizard` per channel; that is what makes this finishable in hours.
- Chasing the ~89 non-channel plugins. They are tools, not channels, and they
  never appear in the channel surface.

## Standing rules that caused today's mistakes

- **State the unit.** "27 platforms" and "112 plugins" are different things.
- **Name where you looked**, or do not claim absence.
- **Prove the click arrived** before calling a control dead — the browser
  harness mis-scales coordinates and swallows key events.
- **Verify, then report.** Not the other way round.
