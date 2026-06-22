# Messaging Thread Contract

Status: Active contract, partial runtime integration
Owner: Platform
Last verified: 2026-06-07
Source of truth: `server_modules/personal_channel_thread_command_service.py`

Messaging channels are remote-control lanes into canonical Sage threads. They
are not the source of truth for memory, history, approvals, or runs.

## Supported Commands

The personal-channel thread command contract recognizes:

- `/new [title]` - request a new Sage thread for this messaging lane.
- `/threads` - request a bounded list of Sage threads.
- `/use <thread-id>` - request this messaging lane to use an existing Sage
  thread id.
- `/status` - show the active Sage thread mapping for this messaging lane.
- `/help` - show the command list.

Commands are parsed by `parse_thread_command()`. The parser is intentionally
strict: plain messages such as `new thread please` are normal Sage prompts, not
control commands.

## Safety Contract

Thread commands do not bypass the personal-channel approval model.

- `dispatch_allowed` is false for parsed thread commands.
- The command contract requires owner context.
- Informational command replies must be routed through the same safe outbound
  lane as other personal-channel replies.
- External sends remain pending approval unless a future explicit trusted rule
  exists.

## Canonical Thread Key

Personal channel runtime context should map messages through a stable key:

```text
{channel_key}:{gateway_id}:{remote_jid}:{thread_alias}
```

The helper `canonical_channel_thread_key()` sanitizes each part so Telegram
personal and future personal channels can share the same mapping shape.
(WhatsApp personal is not a supported channel — see channel ground truth decisions.)

## Product Meaning

On mobile and messaging surfaces, users should not manage separate “Telegram
history” or “iMessage history.” They should see Sage threads. Messaging simply
selects or creates the Sage thread used for that conversation.
