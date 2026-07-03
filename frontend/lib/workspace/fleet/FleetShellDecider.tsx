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

  // Fleet routes get their own layout — no workstation shell chrome
  if (segment === "fleet") {
    return <>{children}</>;
  }

  // All other routes: normal workstation shell
  return <>{shellSlot}</>;
}
