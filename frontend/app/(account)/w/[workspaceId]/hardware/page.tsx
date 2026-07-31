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
 * already says "Hardware" on this line (and IS the page's <h1> now, see
 * Breadcrumbs.tsx's MAN-145 title-dedup follow-up), so HardwareSection's own
 * internal "Hardware" h2 (the one it also renders inside Settings, at
 * 12px/muted, a SECTION style) would just repeat that a second time in the
 * page body — this route only ever has the one section, so unlike Settings
 * (several differently-named sections, "Hardware" among them) that second
 * "Hardware" would read as the exact stutter this whole pass exists to cut.
 * The onboarding subtitle survives as plain instructional copy (no heading
 * of its own) directly under the breadcrumb — MAN-102 empty-state
 * instruction for the fresh, computer-less workspace that actually lands on
 * this route, same copy HardwareSection's own `heading={true}` branch shows
 * inside Settings so the two mounts still describe hardware identically.
 */

import { useParams } from "next/navigation";

import { HardwareSection } from "@/lib/workspace/fleet/HardwareSection";

export default function HardwarePage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");

  return (
    <main className="fleet-content fleet-content--wide">
      <p className="fleet-subtitle" style={{ marginBottom: 24 }}>
        The computers your agents run on — a cloud server provisioned here, or your own machine
        connected over SSH. Set up once; each agent then picks which box it runs on from its own
        Hardware tab.
      </p>
      <HardwareSection workspaceId={workspaceId} heading={false} />
    </main>
  );
}
