"use client";

import { useEffect } from "react";

import { FleetSurfaceError } from "@/lib/workspace/fleet/fleet-states";

/**
 * Route error boundary for everything under /w/{workspaceId} (Hardware,
 * Agents, Projects, agent detail, …). Next.js wraps this segment's page
 * content — not the parent layout — in a React error boundary keyed off
 * this file, so a render crash anywhere in a routed page used to take the
 * whole screen to a blank white page with no way back short of a hard
 * reload (the reported "Hardware blank page"). FleetShell's rail/topbar
 * (rendered by layout.tsx, one level up — outside this boundary) stays
 * mounted around the fallback below, so the user can still navigate
 * elsewhere even if Retry doesn't clear the error.
 */
export default function WorkspaceRouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[workspace route error]", error);
  }, [error]);

  return (
    <FleetSurfaceError
      title="Something went wrong"
      message="This page hit an unexpected error and couldn't finish loading."
      onRetry={reset}
    />
  );
}
