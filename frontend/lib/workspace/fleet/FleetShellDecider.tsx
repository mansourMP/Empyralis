"use client";

import { useSelectedLayoutSegment } from "next/navigation";
import type { ReactNode } from "react";

import { FleetContentFrame } from "./FleetContentFrame";
import { RAIL_ITEMS } from "./primary-rail-nav";

/**
 * Phase 7A: the fleet shell owns the whole workspace surface. Routes in
 * SHELL_SEGMENTS (and the workspace landing, null segment) render directly
 * inside the shell's breadcrumb content frame. Everything else still falls
 * through to the legacy workstation chrome (`shellSlot`) until it's redirected
 * away in the legacy-route sweep.
 *
 * THIS LIST FAILS SILENTLY AND INVISIBLY, which is why half of it is now
 * DERIVED. A segment missing here does not error: the decider swaps in
 * `shellSlot` (null in production — see the workspace layout's own comment),
 * so the route renders the rail and the topbar and NOTHING ELSE. Caught
 * live on 2026-08-15 the first time a new rail destination was added: the
 * "My work" row linked correctly, the URL resolved, `next build` listed the
 * route, and the page was blank, with no error anywhere. The same trap had
 * already fired once before for `sage` — see its note below, which describes
 * this exact symptom being mistaken for a redirect bug.
 *
 * So every RAIL destination is taken from the rail's own list rather than
 * transcribed, and a future rail row is covered the day it lands. Only
 * segments that are NOT rail destinations are authored here, each with a
 * reason for existing.
 */
const NON_RAIL_SHELL_SEGMENTS = [
  // Reachable and live, deliberately unlinked from the rail (project-as-spine
  // nav) — several next.config LEGACY_REDIRECTS still point AT /agents.
  "conversations",
  "agents",
  "fleet",
  // Reached from Settings / the account menu rather than the rail.
  "hardware",
  "billing",
  // "sage" itself no longer has real shell content (the page is just a
  // redirect() stub to /agents), but it still has to be a recognized segment
  // here — otherwise this decider swaps in `shellSlot` (null) instead of
  // `children`, and the page's own redirect never gets to render/fire at all,
  // leaving the user stuck on a blank /sage with no navigation. Removing this
  // was the actual bug behind that, not the redirect logic itself.
  "sage",
];

const SHELL_SEGMENTS = new Set([
  ...RAIL_ITEMS.map((item) => item.segment),
  ...NON_RAIL_SHELL_SEGMENTS,
]);

export function FleetShellDecider({
  workspaceId,
  children,
  shellSlot,
}: {
  workspaceId: string;
  children: ReactNode;
  shellSlot: ReactNode;
}) {
  const segment = useSelectedLayoutSegment();

  if (segment === null || SHELL_SEGMENTS.has(segment)) {
    return <FleetContentFrame workspaceId={workspaceId}>{children}</FleetContentFrame>;
  }

  // Legacy chrome keeps the plain floating panel it always had — the box
  // used to be rendered one level up in FleetShell and moved down into both
  // branches here (originally so the fleet branch's now-removed tab strip
  // could sit above it rather than inside it, MAN-127). Left split even
  // though the strip is gone: this branch is dead today (every route is in
  // SHELL_SEGMENTS, see the workspace layout's comment) and not this
  // change's concern.
  return <div className="fleet-shell-panel">{shellSlot}</div>;
}
