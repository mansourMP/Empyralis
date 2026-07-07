"use client";

import { useState, type ReactNode } from "react";

import { BreadcrumbLabelProvider, Breadcrumbs, HeaderActionSlotProvider } from "./Breadcrumbs";

/**
 * The content frame for every route that lives directly inside the fleet shell
 * (inbox, projects, agents, hardware, billing, settings, and the routed agent
 * detail). Renders a breadcrumb topbar over a scrollable page area. The label
 * provider wraps both so a page can register a dynamic name (project/agent) that
 * the breadcrumb — its sibling — picks up. The action-slot div is the portal
 * target a page's <HeaderAction> renders its primary button into.
 */
export function FleetContentFrame({
  workspaceId,
  children,
}: {
  workspaceId: string;
  children: ReactNode;
}) {
  const [actionSlot, setActionSlot] = useState<HTMLDivElement | null>(null);

  return (
    <BreadcrumbLabelProvider>
      <HeaderActionSlotProvider slotEl={actionSlot}>
        <div className="fleet-shell-main">
          <header className="fleet-shell-topbar">
            <Breadcrumbs workspaceId={workspaceId} />
            <div className="fleet-topbar-action" ref={setActionSlot} />
          </header>
          <div className="fleet-shell-scroll">{children}</div>
        </div>
      </HeaderActionSlotProvider>
    </BreadcrumbLabelProvider>
  );
}
