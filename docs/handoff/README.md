# Engineering Handoff — 2026-07-02

Two prompts for Claude Code sessions. Do NOT run these in your current session — they are designed to be handed to a fresh session (or a team) with full context and no token pressure.

## Files

1. **`01-graphify-platform-map.md`** — Exhaustive platform mapping prompt. Produces `PLATFORM-MAP.md`: complete file map, subsystem connection traces, god objects, import cycles, dead code audit, channel truth table, MCP truth table, current-vs-target gap analysis. Think of this as "what IS the platform, exactly, in excruciating detail."

2. **`02-engineering-review.md`** — Engineering team audit prompt. Produces `ENGINEERING-REVIEW.md`: business viability assessment, architecture health, what to delete, what to build, security review, agent/channel/UI/memory/hardware model assessment, and an honest answer to "should this continue?" Think of this as "is this the RIGHT platform, and what do we do about it?"

## How to use

Open a fresh Claude Code session. Load one prompt at a time (they're each large enough to fill a context window). Say:

> Read `docs/handoff/01-graphify-platform-map.md` and follow the instructions exactly.

When that session produces its output at `docs/handoff/PLATFORM-MAP.md`, start a new session and say:

> Read `docs/handoff/02-engineering-review.md` and follow the instructions exactly. The platform map is at `docs/handoff/PLATFORM-MAP.md`.

## What these are NOT

- NOT build plans (no timelines, no deadlines, no sprints)
- NOT scope-cutting exercises (every removal is named; owner has veto)
- NOT flattery (the owner explicitly asked for direct/harsh feedback)

## Companion documents an engineer should also read

- `docs/PLATFORM.md` — current architecture doc (the baseline)
- Linear documents (linked from both prompts) — product shape, principles, decisions
- Memory files in `.claude/projects/-Users-mansur-empyralis/memory/` — platform vision, strategy, verified status
