---
name: telegram-bot
description: Sends a message through the workspace's connected Telegram bot. Use when the user asks you to message someone on Telegram, reply in a Telegram conversation, or notify a Telegram chat.
skill_class: system
execution_mode: live
action_class: write
connector_scopes:
  - telegram
permission_label: Telegram messaging
enabled: true
---

# Telegram Bot

## What this skill does

Wraps the workspace's Telegram send-message tool behind a single skill
entry point, so "send this on Telegram" can be satisfied via `skill_invoke`
as well as the direct `telegram_bot__send_message` tool.

## Procedure

1. Call `skill_invoke` with `skill_id="telegram-bot"` and `args` containing
   the message text (and a chat id only if it targets a chat other than the
   one already bound to this conversation), e.g.
   `{"goal": "Reminder: standup moved to 10am"}`.
2. The skill dispatches to `telegram_bot__send_message` under the hood.
3. Confirm to the user that the message was sent, or surface the error
   plainly if the Telegram connector is not connected for this workspace.

## When NOT to use this skill

- For Slack, Discord, WhatsApp, or email, use that channel's own tool
  (`slack__send_message`, `discord_bot__send_message`,
  `whatsapp_twilio__send_message`, or an email connector) instead — this
  skill only reaches Telegram.

## Safety notes

- Only send messages the user actually asked for — never message a
  Telegram chat proactively without a clear instruction.
- If no Telegram connector is configured for the workspace, this call fails
  cleanly rather than silently doing nothing — report the failure, don't
  claim the message was sent.
