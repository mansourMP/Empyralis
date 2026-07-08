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
  "projects",
  "agents",
  "hardware",
  "billing",
  "settings",
  "fleet",
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

  return <>{shellSlot}</>;
}
