"use client";

import { useSelectedLayoutSegment } from "next/navigation";
import type { ReactNode } from "react";

/**
 * Phase UC: If the active child segment is 'fleet', render children directly
 * (FleetHome has its own full-viewport layout). Otherwise, render the normal
 * workstation shell via `shellSlot`.
 */
export function FleetShellDecider({
  children,
  shellSlot,
}: {
  children: ReactNode;
  shellSlot: ReactNode;
}) {
  const segment = useSelectedLayoutSegment();

  // Fleet routes AND the workspace landing (null segment) render
  // without the workstation shell chrome — they get the fleet layout
  // (rail + content area). All other sub-routes use the workstation shell.
  if (segment === "fleet" || segment === null) {
    return <>{children}</>;
  }

  // All other routes: normal workstation shell
  return <>{shellSlot}</>;
}
