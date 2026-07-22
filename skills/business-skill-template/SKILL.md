---
name: business-skill-template
description: Reference template showing the SKILL.md structure and the skill_write authoring workflow for a new business-specific skill. Disabled by default — read its body for the format, then author a real skill via skill_write rather than enabling this one directly.
skill_class: business
execution_mode: manual
action_class: read
permission_label: Template reference
enabled: false
---

# Business Skill Template

This skill is intentionally disabled — it exists only as a Level-2
reference for authoring new business skills, not something the agent runs
as its own task. Read this body for the format, then call `skill_write` to
create a real skill with the same shape.

## Frontmatter fields to fill in for a real business skill

- `name` — lowercase, hyphenated, must match the skill's own directory
  name.
- `description` — third person. State what the skill does AND when to use
  it. This is the only text shown to the agent before the skill is invoked
  (the Level 1 catalog listing) — make it specific enough that the agent
  can tell when this skill applies without reading further.
- `skill_class` — `business`, `system`, or `specialist_local`.
- `execution_mode` — `live` if it should run for real, `manual` if it
  should require a human in the loop.
- `action_class` — `read`, `write`, or `execute`.
- `connector_scopes` — the connector ids this skill touches, if any (for
  example `crm`, `email`).
- `permission_label` — the human label shown in the Skills/Tools tab.
- `requires_approval` — `true` if this skill should require owner approval
  before running.

## Body structure to fill in

1. **What this skill does** — one paragraph, plain language.
2. **Procedure** — the numbered steps the agent should follow when this
   skill is invoked: what to check first, what tool(s) to call, what to
   report back, and what "done" looks like.
3. **When NOT to use this skill** — the adjacent cases that look similar
   but should route elsewhere.
4. **Safety notes** — anything the agent must confirm with the user before
   acting, and any hard limits.

## How to actually create one

Call `skill_write` with `name`, `description`, and `body` (the Markdown
that follows the structure above). The new skill is written to the
workspace's skill directory, security-scanned, and installed — but it
starts **disabled and pending owner review**. It is not listed as available
and `skill_invoke` will not run it until the workspace owner reviews and
enables it. This mirrors Claude Code's own documented skill-authoring
guidance: a human reviews a newly authored skill before it is trusted,
rather than the agent silently activating its own new capability.
