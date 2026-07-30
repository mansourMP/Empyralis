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
 * `heading={false}` is still passed to HardwareSection — the breadcrumb
 * already says "Hardware" on this line, and HardwareSection's own internal
 * "Hardware" title (the one it also renders inside Settings, at 12px/muted,
 * a SECTION style) would just repeat that a second time in the page body.
 * What was actually missing (MAN-145 item 4) was a real page <h1> — the
 * breadcrumb is a `<nav>` landmark, not a heading, so a page whose only
 * "Hardware" was a breadcrumb crumb had zero heading roles for a screen
 * reader's H-key/rotor navigation. This header block gives it one, at the
 * same `.fleet-title`/`.fleet-subtitle` weight FleetHome's "Your fleet" uses
 * for the same job — no new type style, just the existing page-title pair
 * applied here too. The subtitle copy is HardwareSection's own (the one its
 * `heading={true}` branch shows inside Settings) so the two mounts describe
 * hardware identically; it doubles as MAN-102 empty-state instruction here,
 * since this route is the one a fresh, computer-less workspace actually
 * lands on.
 */

import { useParams } from "next/navigation";

import { HardwareSection } from "@/lib/workspace/fleet/HardwareSection";

export default function HardwarePage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");

  return (
    <main className="fleet-content fleet-content--wide">
      <div className="fleet-header">
        <div>
          <h1 className="fleet-title">Hardware</h1>
          <p className="fleet-subtitle">
            The computers your agents run on — a cloud server provisioned here, or your own machine
            connected over SSH. Set up once; each agent then picks which box it runs on from its own
            Hardware tab.
          </p>
        </div>
      </div>
      <HardwareSection workspaceId={workspaceId} heading={false} />
    </main>
  );
}
