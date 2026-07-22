---
name: vision-monitor
description: Monitors a physical space using camera snapshots from a connected device and reports what is visible or what changed. Use when the user asks Sage to watch a room, check a camera, or describe what a connected camera currently sees.
skill_class: system
execution_mode: live
action_class: read
connector_scopes:
  - vision
permission_label: Camera access
enabled: true
---

# Vision Monitor

## What this skill does

Reads a snapshot from a connected camera/vision source and reports on what
is visible, for workspaces with a paired device that exposes camera access.

## Procedure

1. Call `skill_invoke` with `skill_id="vision-monitor"` and `args`
   describing what to check for, e.g.
   `{"goal": "is anyone in the living room right now"}`.
2. If no camera/vision runtime is connected for this workspace, relay that
   limitation to the user plainly instead of guessing or fabricating an
   observation.
3. When a vision source is available, prefer summarizing what changed since
   the last check over re-describing the entire scene each time, unless the
   user explicitly asks for a full description.

## Safety notes

- This skill only reads camera state — it never stores or forwards footage
  without the user's explicit request.
- Treat camera access as sensitive: confirm with the user before enabling
  ongoing monitoring of a space they have not already agreed to have
  watched.
