"use client";

import { useState, type ReactNode } from "react";
import { Menu } from "lucide-react";

import { BreadcrumbLabelProvider, Breadcrumbs, HeaderActionSlotProvider } from "./Breadcrumbs";

/**
 * The content frame for every route that lives directly inside the fleet shell
 * (inbox, projects, agents, hardware, billing, settings, and the routed agent
 * detail). Renders a breadcrumb topbar over a scrollable page area. The label
 * provider wraps both so a page can register a dynamic name (project/agent/
 * task) that the breadcrumb picks up. The action-slot div is the portal
 * target a page's <HeaderAction> renders its primary button into.
 *
 * NO TAB STRIP HERE (removed, MAN-127-reversal). The app used to render its
 * own Safari/VS-Code-style row of open-view tabs directly above this panel —
 * a second tab bar competing with the real browser's own, for navigation the
 * primary rail's real `<a>` links already cover: a plain click goes there, and
 * ⌘/Ctrl-click and middle-click already get genuine browser new-tab behaviour
 * for free because the rail is real links, not JS-driven routing. The removed
 * strip persisted its own array of {path, title, history} per open view in
 * localStorage and kept a PER-TAB back/forward stack (‹ / ›) — real
 * complexity, but it was answering a question ("what have I got open, and
 * where was I in each") the browser's own tab bar already answers one layer
 * up. Per CLAUDE.md ("a surface must earn its place" / "if something can live
 * one level down, it should"): this one didn't. Same fix applies to every
 * board/list row that used to stamp `data-tab-href` for that strip's
 * modifier-click interceptor to read — see AgentsList/AgentsBoard/
 * AgentsGroupedList/TasksList/TasksBoard/TasksGroupedList, whose rows now
 * just navigate in place on ANY click; the real `<a>` links elsewhere (rail,
 * breadcrumb, task parent/subtask links) were never routed through the
 * strip's interceptor and are unaffected.
 *
 * The panel div lives here (and in FleetShellDecider's legacy branch) rather
 * than in FleetShell so each branch owns its own box; with the strip gone
 * the two branches render the same shape, but the split still lets the
 * legacy branch (dead code today — see FleetShellDecider) diverge without
 * disturbing this one.
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
      </HeaderActionSlotProvider>
    </BreadcrumbLabelProvider>
  );
}
