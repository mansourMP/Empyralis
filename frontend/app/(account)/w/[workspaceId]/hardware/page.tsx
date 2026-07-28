"use client";

/**
 * Standalone Hardware route.
 *
 * Hardware moved OUT of the primary rail (2026-07) and into Settings — it is a
 * setup concern, not a daily one. Its home is now a section on
 * /w/{workspaceId}/settings; this route stays live and renders the exact same
 * component so nothing that points here breaks:
 *
 *   · bookmarks and the /gateway + /gateway-activity legacy redirects
 *     (next.config.ts) that both land on /hardware,
 *   · every in-app "Go to Hardware" CTA (gateway-box-picker.tsx's empty state,
 *     FleetHome's status strip, the command palette, chat-pane notices),
 *   · the machine detail route's own back link (hardware/[gatewayId]),
 *   · the backend's VPS OAuth callback, which redirects the browser to
 *     /w/{ws}/hardware?vps_oauth=… (routes_gateway.py) and cannot be changed
 *     from the frontend.
 *
 * `heading={false}` because the breadcrumb already says "Hardware" here — the
 * in-Settings mount renders the section title instead.
 */

import { useParams } from "next/navigation";

import { HardwareSection } from "@/lib/workspace/fleet/HardwareSection";

export default function HardwarePage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");

  return (
    <main className="fleet-content fleet-content--wide">
      <HardwareSection workspaceId={workspaceId} heading={false} />
    </main>
  );
}
