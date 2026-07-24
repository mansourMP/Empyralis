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
  the owner-facing agent) — the same resource scoped to one agent. Channels and Connectors
  are real platform-icon grids backed by live data (see below). Memory and
  Tools are wired to real per-agent APIs. Overview, Chat, and Model are
  real too.

Don't build a third place for either. If a workspace-wide page needs an
agent filter, add it to the existing rail page rather than duplicating the
modal's content, and vice versa.

## Agent detail modal

`FleetAgentDetail.tsx` is a centered modal (~80vw), not a slide-in panel.
Internal left nav (Overview / Chat / Memory / Channels / Connectors / Tools
/ Model), not top tabs — mirrors the ChatGPT-settings pattern the rail
itself doesn't use (rail nav is vertical too, but this modal's nav is a
second, independent vertical list scoped to the modal only).

## Channels / Connectors data source

`server_modules/routes_fleet.py`'s `fleet_agent_channels` and
`fleet_agent_connectors` endpoints used to call
`connection_catalog_service.catalog_items(surface="channels" | "apps")`.
`"channels"` is not a real surface value anywhere in the catalog (only
`agent_computer/applications/apps/sage/studio` are) — that call always
returned an empty list, which is why the old Channels tab hard-coded two
static cards instead of rendering data. Both endpoints now call
`status_items(workspace_id=..., surface=...)` instead — the same function
`/api/connections/status` (the working workspace-wide grid) uses — filtered
to the relevant `lane` server-side. This is also where `connected`,
`requires_gateway`, `gateway_count`, and `health_status` come from; there's
no need to hand-roll vault-id membership checks again.

Icons for both grids are a **client-side lookup by id**
(`frontend/lib/workspace/fleet/fleet-icons.ts`) — the backend catalog has no
image field at all. If a new channel/connector platform gets added to the
catalog, add its icon path there too or it'll fall back to a letter avatar.

## GatewayPairPanel

`frontend/lib/gateway/GatewayPairPanel.tsx` — self-contained pairing flow
(own state, `{workspaceId, onPaired?, compact?, defaultPlatform?}`), mounted
in two places: the agent modal's Channels tab (Gateway-required platforms)
and the Hardware page's "This Mac" card (as a fallback when the tray app
isn't detected — the tray-detection and "Other Computers" QR-code flows
were already solid and weren't touched). It is intentionally simpler than
the Hardware page's own pairing-intent flow (no QR code, no custom access
policy) — that fuller flow stays where it already worked well.

All mutating fetches in the fleet UI must go through
`buildCookieAuthHeaders()` from `@/lib/auth/csrf` — raw `fetch()` calls
silently 403 on POST (double-submit CSRF cookie/header check). The
pre-existing hosted-Telegram pairing button had this exact bug before this
pass — it looked fine in a code read but any real click failed.
