# Canonical Inbound Envelope — design

**The rule:** the agent must never have to *infer* who is talking and from where —
the platform *tells* it, structurally, on every inbound message. Attribution is a
fact the harness supplies, not a conclusion the model reaches. (This is exactly how
Claude Code frames everything reaching its model: `[SYSTEM NOTIFICATION — NOT USER
INPUT]`, `<system-reminder>`, local-command caveats. We adopt the same discipline.)

Grounding: `docs/design/inbound-attribution-audit.md` (the per-channel audit that
found there was NO canonical envelope — signals computed then collapsed to prose or
discarded), `docs/OpenClaw.md` (reference implementation), founder rulings
(channel-behavior: self-chat = owner command channel; groups = see-and-decide with
`[SILENT]`, never mention-gated).

## The envelope (server_modules/inbound_envelope.py)

```
InboundEnvelope
  platform        "telegram" | "whatsapp" | "signal" | "imessage" | "wechat"
                  | "slack" | "discord" | "console" | "api" | ...
  surface         OWNER_SELF_CHAT | DM | GROUP | BROADCAST_CHANNEL | CONSOLE | API | UNKNOWN
  sender          { id, display_name, is_owner: True|False|None(unverified), is_bot }
  chat            { id, title }              # group/channel name, e.g. "Family"
  addressed       True|False|None            # was the agent mentioned / replied-to?
```

Every channel constructs one; `NormalizedSageTurn.envelope` carries it; the single
chokepoint `sage_turn_adapter.execute_sage_turn()` renders it into the context
window as a one-line header prepended to the message content:

```
[Telegram · group “Family” · from Aruzhan — NOT your owner · you were not addressed — observe; reply only if clearly addressed or truly helpful; otherwise reply exactly [SILENT]]
[Telegram · your owner Mansur · talking to you directly]
[Slack · DM · from Dana — NOT your owner]
[Console · your owner Mansur]
```

One line, deterministic shape, token-lean. It persists into thread history (good:
per-turn provenance is visible in the Work tab too).

## Policy — enforced in CODE, stated in the header

- **Owner commands** are honored ONLY when `sender.is_owner is True` AND surface in
  {OWNER_SELF_CHAT, DM, CONSOLE} — `envelope_allows_owner_commands()`. A group can
  never be the owner. This makes the recurring "family group treated as owner" bug
  structurally impossible, not prompt-discouraged.
- **Groups/channels:** see-and-decide per the standing ruling — the agent sees every
  message, the header states the policy, `[SILENT]` suppresses outbound
  (channel_adapter.filter_outbound_reply already enforces it).
- **Unverified sender** (`is_owner=None`): treated as NOT owner everywhere.

## Per-channel construction (the wiring)

| Channel | Signals source | Notes |
|---|---|---|
| Telegram/WhatsApp/Signal personal | bridge already computes is_owner/is_group/chat_label | replace ad-hoc prose prefixes with the envelope |
| iMessage | **fix owner detection** (audit: always False) then same as above |
| Telegram hosted | 1 chat = 1 workspace ⇒ DM, owner implicit |
| WeChat official | **add surface**; every sender is a customer (is_owner False); **fix the shared "sage-main" thread → per-customer threads** |
| Slack / Discord | surface computed today then discarded — keep it; no owner concept ⇒ is_owner False |
| Console / web (agent_turn path + AgentChat) | CONSOLE surface, sender = authenticated account, is_owner True |

## Non-goals (follow-ups)
- Attribution-aware memory save-filters + UI (task #37) rides on the envelope next.
- Per-agent configurable group reply-policy UI (default stays see-and-decide).
