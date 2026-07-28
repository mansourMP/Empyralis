"use client";

import { useSelectedLayoutSegment } from "next/navigation";
import type { ReactNode } from "react";

import { FleetContentFrame } from "./FleetContentFrame";

/**
 * Phase 7A: the fleet shell owns the whole workspace surface. Routes in
 * SHELL_SEGMENTS (and the workspace landing, null segment) render directly
 * inside the shell's breadcrumb content frame. Everything else still falls
 * through to the legacy workstation chrome (`shellSlot`) until it's redirected
 * away in the legacy-route sweep.
 */
const SHELL_SEGMENTS = new Set([
  "inbox",
  "conversations",
  "projects",
  "agents",
  "hardware",
  "billing",
  "settings",
  "fleet",
  // "sage" itself no longer has real shell content (the page is just a
  // redirect() stub to /agents), but it still has to be a recognized segment
  // here — otherwise this decider swaps in `shellSlot` (null) instead of
  // `children`, and the page's own redirect never gets to render/fire at all,
  // leaving the user stuck on a blank /sage with no navigation. Removing this
  // was the actual bug behind that, not the redirect logic itself.
  "sage",
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

  // Legacy chrome has no tab strip, so it keeps the plain floating panel it
  // always had — the box used to be rendered one level up in FleetShell, and
  // moved down into the two branches when the fleet branch grew a strip that
  // has to sit OUTSIDE the panel (MAN-127).
  return <div className="fleet-shell-panel">{shellSlot}</div>;
}
