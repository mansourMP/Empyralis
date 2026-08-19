"use client";

import { useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";
import { Menu } from "lucide-react";

import { BreadcrumbLabelProvider, Breadcrumbs, HeaderActionSlotProvider } from "./Breadcrumbs";

// Every tab of an agent's detail surface renders its own single minimal
// header now (AgentDetailHeader in FleetAgentDetail.tsx: back + sigil +
// name + status dot + a persistent Work control + one "⋯" menu for
// Sessions/Configure/Stop) — this shell's own breadcrumb topbar (chain
// text, mobile hamburger, the action-slot) would otherwise stack a SECOND
// header directly above the content, which is exactly the clutter the
// founder named: "a breadcrumb..., a Chat | Work tab strip, and a separate
// Configure button, all visible at once." This used to be scoped to the
// Chat route alone (2026-08-16) — widened 2026-08-19 to every route under
// an agent's detail surface, once AgentDetailHeader became the frame for
// all of them, not just Chat. `[tab]/page.tsx` is the deepest route this
// app has under `/agents/[agentId]/` (confirmed: no further nesting — a
// conversation is `?thread=`, a query param, never a path segment), so
// matching any single trailing segment is exactly "every tab, and nothing
// past an agent's own detail surface" — no hand-kept list of the eleven
// tab ids to go stale the next time one is added or renamed. No
// HeaderAction portals into the action slot on any of these routes
// anymore (Work's old Stop/Resume+Chat controls moved into the header
// itself), so nothing here loses a destination.
const AGENT_DETAIL_ROUTE = /^\/w\/[^/]+\/projects\/[^/]+\/agents\/[^/]+\/[^/]+$/;

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
  const pathname = usePathname() || "";
  const hideTopbarChrome = AGENT_DETAIL_ROUTE.test(pathname);

  return (
    <BreadcrumbLabelProvider>
      <HeaderActionSlotProvider slotEl={actionSlot}>
        <div className="fleet-shell-panel">
          <div className="fleet-shell-main">
            {!hideTopbarChrome && (
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
            )}
            <div className="fleet-shell-scroll">{children}</div>
          </div>
        </div>
      </HeaderActionSlotProvider>
    </BreadcrumbLabelProvider>
  );
}
