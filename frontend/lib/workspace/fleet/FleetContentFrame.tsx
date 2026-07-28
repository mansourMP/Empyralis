"use client";

import { useState, type ReactNode } from "react";
import { Menu } from "lucide-react";

import { BreadcrumbLabelProvider, Breadcrumbs, HeaderActionSlotProvider } from "./Breadcrumbs";
import { FleetTabStrip, FleetTabsProvider } from "./FleetTabs";

/**
 * The content frame for every route that lives directly inside the fleet shell
 * (inbox, projects, agents, hardware, billing, settings, and the routed agent
 * detail). Renders a tab strip and a breadcrumb topbar over a scrollable page
 * area. The label provider wraps all three so a page can register a dynamic
 * name (project/agent/task) that the breadcrumb AND its tab both pick up. The
 * action-slot div is the portal target a page's <HeaderAction> renders its
 * primary button into.
 *
 * WHERE THE TAB STRIP SITS (MAN-127 FIX 1). ABOVE the floating content panel
 * and OUTSIDE it, on the bare canvas — not inside the panel as chrome bolted
 * onto the page. The panel below therefore starts with a clean top edge and
 * holds nothing but content, which is the "a window containing a page" read
 * Linear has and the "a page with a toolbar stuck on it" read we had.
 *
 * It is still scoped to the MAIN-CONTENT COLUMN, never the full window width:
 * `.fleet-shell-column` is the flex child sitting to the RIGHT of the primary
 * rail, so the strip starts where the content starts. Tabs swap main-content
 * views only — the rail is neither covered nor scrolled by them, which is the
 * property that made putting the strip inside the panel tempting in the first
 * place. This layout keeps it and gains the clean panel edge.
 *
 * The panel div lives here (and in FleetShellDecider's legacy branch) rather
 * than in FleetShell, because only THIS branch has a strip above it to sit
 * under.
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
      <FleetTabsProvider workspaceId={workspaceId}>
        <HeaderActionSlotProvider slotEl={actionSlot}>
          <div className="fleet-shell-column">
            <FleetTabStrip />
            <div className="fleet-shell-panel">
              <div className="fleet-shell-main">
                <header className="fleet-shell-topbar">
                  <button
                    type="button"
                    className="fleet-topbar-menu-btn"
                    onClick={() => window.dispatchEvent(new Event("fleet:toggle-mobile-nav"))}
                    aria-label="Open menu"
                  >
                    <Menu size={18} strokeWidth={1.75} />
                  </button>
                  <Breadcrumbs workspaceId={workspaceId} />
                  <div className="fleet-topbar-action" ref={setActionSlot} />
                </header>
                <div className="fleet-shell-scroll">{children}</div>
              </div>
            </div>
          </div>
        </HeaderActionSlotProvider>
      </FleetTabsProvider>
    </BreadcrumbLabelProvider>
  );
}
