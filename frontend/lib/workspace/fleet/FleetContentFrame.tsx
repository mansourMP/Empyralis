"use client";

import type { ReactNode } from "react";

import { BreadcrumbLabelProvider, Breadcrumbs } from "./Breadcrumbs";

/**
 * The content frame for every route that lives directly inside the fleet shell
 * (inbox, projects, agents, hardware, billing, settings, and the routed agent
 * detail). Renders a breadcrumb topbar over a scrollable page area. The label
 * provider wraps both so a page can register a dynamic name (project/agent) that
 * the breadcrumb — its sibling — picks up.
 */
export function FleetContentFrame({
  workspaceId,
  children,
}: {
  workspaceId: string;
  children: ReactNode;
}) {
  return (
    <BreadcrumbLabelProvider>
      <div className="fleet-shell-main">
        <header className="fleet-shell-topbar">
          <Breadcrumbs workspaceId={workspaceId} />
        </header>
        <div className="fleet-shell-scroll">{children}</div>
      </div>
    </BreadcrumbLabelProvider>
  );
}
