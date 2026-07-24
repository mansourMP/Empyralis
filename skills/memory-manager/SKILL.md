---
name: memory-manager
description: Reads and returns the agent's full current durable memory (MEMORY.md and memory/*.md facts) for a workspace, and explains the memory read/write procedure. Use this when a full snapshot of everything remembered about the user is needed, or as a reference for the memory procedure — for a single targeted fact, use memory_search and memory_get instead.
skill_class: system
execution_mode: live
action_class: write
connector_scopes:
  - memory
permission_label: Memory scope
enabled: true
---

# Memory Manager

## What this skill does

Loads the full current memory snapshot for the calling workspace — every
durable fact saved about the user so far — and returns it as a single
artifact. It is the "give me everything you remember" tool, not a targeted
lookup.

## Procedure

1. Call `skill_invoke` with `skill_id="memory-manager"`.
2. The skill loads the workspace's memory notebook (`MEMORY.md` plus any
   `memory/*.md` daily notes) and returns it as the reply.
3. Read the returned text before answering questions like "what do you know
   about me" or "what have we discussed" — never answer those from
   assumption or from what you recall being in context earlier in the
   conversation; always re-check the live notebook.
4. If the memory notebook is empty, say so plainly ("I don't have anything
   saved yet") rather than inventing facts.

## When NOT to use this skill

- For a single targeted fact lookup, call `memory_search` then `memory_get`
  instead — this skill returns everything, which costs more context.
- To save a new fact, call `memory_write` with `path="MEMORY.md"` and
  `mode="append"` directly — this skill does not accept new facts as input.

## Fallback behavior

If the live memory snapshot cannot be loaded for any reason, this skill
falls back to dispatching the `memory_update` tool so the caller still gets
a usable result instead of a hard failure.

## Safety notes

- Memory content can include sensitive personal facts — never repeat it
  outside the conversation it was requested in, and never send it to a
  channel or recipient the user has not asked for.
