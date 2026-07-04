# UI navigation model

One navigation system: the primary rail (`frontend/lib/workspace/fleet/PrimaryRail.tsx`).
It persists across every workspace route. There is no second rail — the old
per-destination workstation left panel was removed; per-agent drill-down
that used to live there is gone too.

## Workspace-wide vs per-agent

Memory, Channels, and Connectors exist in two places, deliberately:

- **Rail destination** (`/w/[id]/memory`, `/channels`, `/integrations`) —
  workspace-wide. Shows the resource across all agents, unfiltered by which
  agent you were just looking at.
- **Agent detail modal** (click any card in `/w/[id]/fleet`, including
  Sage) — the same resource scoped to one agent. Today Memory, Channels,
  Connectors, and Tools show an intentional "Soon" state in the modal —
  there is no per-agent-scoped API for these yet (only `agent-activity` and
  the agent record itself). Overview, Chat, and Model are wired to real
  data.

Don't build a third place for either. If a workspace-wide page needs an
agent filter, add it to the existing rail page rather than duplicating the
modal's content, and vice versa.

## Agent detail modal

`FleetAgentDetail.tsx` is a centered modal (~80vw), not a slide-in panel.
Internal left nav (Overview / Chat / Memory / Channels / Connectors / Tools
/ Model), not top tabs — mirrors the ChatGPT-settings pattern the rail
itself doesn't use (rail nav is vertical too, but this modal's nav is a
second, independent vertical list scoped to the modal only).
