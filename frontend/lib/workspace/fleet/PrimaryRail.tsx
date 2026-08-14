"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, usePathname, useSelectedLayoutSegment } from "next/navigation";
import {
  ArrowLeft,
  BarChart3,
  ChevronRight,
  LogOut,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  Sun,
} from "lucide-react";

import { logout } from "@/lib/auth/auth-client";
import { useAccountShell } from "@/lib/shell/account-shell-context";
import { getInboxLastSeenAt, useFleetAgents, useFleetProjects, useFleetWorkspace, useWorkspaceActivity } from "./fleet-data";
import { deriveStatus, findSageAgent } from "./fleet-presentation";
import { ProjectIcon } from "./fleet-project-identity";
import { AgentSigil, StatusDot } from "./fleet-indicators";
import { rememberLastViewedAgent } from "./AgentsList";
import { planAgentCountShape } from "./agent-count-shape";
import { visibleRailItems } from "./primary-rail-nav";
import { activeProjectIdFromPathname } from "./primary-rail-project-mode";
import { FleetHelpButton } from "./FleetHelpButton";
import { SageLauncher } from "./SageLauncher";
import { CreditBalanceChip } from "./CreditBalanceChip";
import { SystemHealthButton } from "./SystemHealthButton";
import { BugReportButton } from "./BugReportButton";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";

import { RAIL_WIDTH, useResizableWidth } from "./fleet-preferences";
import type { FleetTheme, FleetSectionKey } from "./fleet-preferences";

// Rail vocabulary lives in primary-rail-nav.ts now — a pure, dependency-light
// module a plain test can import directly (see primary-rail-nav.test.ts).
// Project-as-spine nav (CLAUDE.md, 2026-08-13): Conversations and Agents are
// GONE from the rail, not reordered — both used to aggregate across every
// project's agents, which is exactly the boundary an agent belonging to its
// project must not be reached past. An agent now lives inside the project it
// belongs to; see project-agents-rail-shape.ts for the compact rail that
// replaces the old flat Agents table there. Hardware left the rail in the
// 2026-07 repositioning for an unrelated reason (set-once config, not a
// project-scoping one) and lives in Settings now — the /w/{ws}/hardware
// route is still live, so every link/bookmark/redirect that pointed at it
// still resolves.

const RAIL_ICON = 16;
const CONTROL_ICON = 16;

// SUPERSEDED, 2026-08-14 — the rail used to nest an open project's own
// Tasks/Documents/Agents SECTIONS beneath its row (see git history for the
// PROJECT_SECTIONS table this replaced). The founder looked at that shipped
// nesting against his own live screenshot and asked for two modes instead of
// one rail that grows a branch: opening a project swaps the rail's nav
// content entirely — Inbox and Projects gone, that project's AGENT NAMES in
// their place, one Back control at the top. See
// primary-rail-project-mode.ts for the pure boundary check
// (activeProjectIdFromPathname) and the "project mode" render branch below.
// Tasks/Documents are still reached exactly as before — the project's own
// tab strip at its bare index (ProjectDetailPage) — this only changes what
// the RAIL shows, never what the project page itself renders.

// 4 decimals, matching AgentsList/billing/project detail's cost formatters —
// real per-turn costs are fractions of a cent, and this line sits directly
// above the Agents list row that already shows the honest, unrounded figure.
// Two decimals silently rounded any realistic per-turn spend to "$0.00",
// contradicting the very row beneath it (Truth Map, 2026-07-10). Zero is a
// different case, not just a smaller number: there's no fraction-of-a-cent
// precision to defend, so "$0.0000" only ever read as leaked debug output
// (MAN-145) — an em dash, this codebase's existing convention for "nothing
// to show" (see AgentsList/CreditsPanel/TaskDetailView), says the same thing
// honestly.
const money = (n: number) => (n === 0 ? "—" : `$${n.toFixed(4)}`);

/**
 * Persistent primary rail — the app's spine, and PROJECTS is the spine of
 * the spine (CLAUDE.md, 2026-08-13 navigation decision). A populated
 * workspace header (U3-G) replaces the static product wordmark; Projects is
 * a real, collapsible sub-list of the workspace's own data (Linear's
 * "Teams" treatment — genuine containers you navigate into); a quiet footer
 * line reports the fleet's pulse above the account block. Billing lives in
 * the account menu instead — it's a look-up-occasionally screen, not a nav
 * destination. Keyboard: `j`/`k` move a highlight, Enter opens it; `g` then
 * a section key jumps directly (g i inbox, g p projects) — the Linear
 * muscle-memory model.
 *
 * Conversations and Agents are GONE from this rail (see primary-rail-nav.ts),
 * not merely folded into the Projects sub-list — both used to aggregate
 * across every project's agents, which is exactly the boundary "an agent
 * belongs to its project and works only there" says a nav surface must not
 * reach past. Agents are entities that live INSIDE the project that owns
 * them, reached by opening that project's own Agents section — see
 * project-agents-rail-shape.ts and ProjectAgentsRail.tsx for the compact
 * list that replaces the old flat, workspace-wide Agents table there.
 */
export function PrimaryRail({
  workspaceId,
  ownerName = "Owner",
  ownerEmail = "",
  ownerRole = "Owner",
  collapsed,
  onToggleCollapsed,
  theme,
  onToggleTheme,
  sections,
  onToggleSection,
  mobileOpen = false,
  onCloseMobile,
  sageOpen,
  onOpenSage,
  onCloseSage,
}: {
  workspaceId: string;
  ownerName?: string;
  ownerEmail?: string;
  ownerRole?: string;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  theme: FleetTheme;
  onToggleTheme: () => void;
  sections?: Record<FleetSectionKey, boolean>;
  onToggleSection?: (key: FleetSectionKey) => void;
  mobileOpen?: boolean;
  onCloseMobile?: () => void;
  sageOpen: boolean;
  onOpenSage: () => void;
  onCloseSage: () => void;
}) {
  const router = useRouter();
  const pathname = usePathname() || "";
  const { projects } = useFleetProjects(workspaceId);
  const { workspace } = useFleetWorkspace(workspaceId);
  const { agents: allAgents } = useFleetAgents(workspaceId);

  // The desktop icon-only rail collapse is a persisted preference that has
  // nothing to do with the mobile drawer — forcing it off while the drawer
  // is open means a phone visitor always sees full labels regardless of
  // what a prior desktop session left in localStorage. Safe without a
  // matchMedia check: mobileOpen can only ever become true via the
  // hamburger button, which CSS hides entirely above the 768px drawer
  // breakpoint (see fleet-theme.css), so effectiveCollapsed only differs
  // from collapsed in exactly the narrow-viewport case that should ignore it.
  const effectiveCollapsed = collapsed && !mobileOpen;

  // ── Rail resize ─────────────────────────────────────────────────────────
  // Two different gestures share one edge, and keeping them distinct is the
  // whole point: DRAGGING is never animated (it must track the pointer 1:1 —
  // easing a drag is just lag), while COLLAPSE/EXPAND is animated, because
  // there the width is changing on its own and the motion explains that the
  // rail shrank rather than vanished. useResizableWidth writes the live width
  // straight to --rail-w during a drag and adds `.is-resizing` to .fleet-root,
  // which kills the transition for the duration.
  const lastWidthRef = useRef<number>(RAIL_WIDTH.def);
  // Founder feedback: dragging an OPEN rail left used to be allowed to show
  // widths all the way down to RAIL_WIDTH.collapsed (56px) before snapping
  // shut on release — which meant every width between that floor and
  // RAIL_WIDTH.min (200px) was a real, visible, mid-drag state: full nav
  // labels rendered into a box too narrow for them, clipped and overlapping.
  // The rail already has a dedicated, discoverable way to get out of the
  // way (the "Toggle rail" collapse button below) — drag has no business
  // reproducing that at a worse fidelity. So this no longer collapses on a
  // release past the minimum at all: the OPEN-rail drag floor is
  // RAIL_WIDTH.min itself, and handleRelease below only still special-cases
  // the opposite gesture (reopening an already-collapsed rail by dragging
  // its handle), which never passes through that degenerate zone in the
  // first place — it starts at 56px and grows, rather than starting wide
  // and being dragged down into it.
  const handleRelease = useCallback(
    (raw: number) => {
      if (collapsed) {
        // Dragging right, out of a collapsed rail: past the restore
        // threshold it reopens at whatever width the pointer settled on.
        if (raw > RAIL_WIDTH.restoreAt) {
          onToggleCollapsed();
          return Math.max(raw, RAIL_WIDTH.min);
        }
        return lastWidthRef.current;
      }
      return raw;
    },
    [collapsed, onToggleCollapsed],
  );

  const rail = useResizableWidth({
    storageKey: RAIL_WIDTH.key,
    defaultWidth: RAIL_WIDTH.def,
    minWidth: RAIL_WIDTH.min,
    maxWidth: RAIL_WIDTH.max,
    cssVar: "--rail-w",
    edge: "right",
    // Collapsed rail (56px) dragged right needs to visibly grow from that
    // floor to reach RAIL_WIDTH.restoreAt below — an OPEN rail dragged left
    // must never show anything narrower than RAIL_WIDTH.min (200px, chosen
    // as the floor that still keeps every rail label — "Conversations",
    // the longest row — on one legible line; see RAIL_WIDTH's own comment).
    // collapsed can change between renders, so this is read fresh each
    // pointerdown rather than fixed at mount.
    dragFloor: collapsed ? RAIL_WIDTH.collapsed : RAIL_WIDTH.min,
    onRelease: handleRelease,
  });
  useEffect(() => {
    lastWidthRef.current = rail.width;
  }, [rail.width]);

  const onResizerKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLElement>) => {
      if (event.key === "Enter") {
        event.preventDefault();
        onToggleCollapsed();
        return;
      }
      rail.separatorProps.onKeyDown(event);
    },
    [onToggleCollapsed, rail.separatorProps],
  );

  // Close the drawer on every navigation, regardless of which link/button
  // triggered it (rail item, project/agent subitem, account-menu row) —
  // cheaper and more robust than wiring onCloseMobile into each handler.
  useEffect(() => {
    onCloseMobile?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  // Read after mount (not during render) — same localStorage-hydration
  // timing useFleetPreferences already uses, avoiding an SSR/hydration
  // mismatch. Re-read whenever the Inbox page itself stamps a fresh value
  // (see the "storage" listener below) so the badge clears without a reload.
  const [lastSeenAt, setLastSeenAt] = useState<string | null>(null);
  useEffect(() => {
    setLastSeenAt(getInboxLastSeenAt(workspaceId));
    const onStorage = () => setLastSeenAt(getInboxLastSeenAt(workspaceId));
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", onStorage);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", onStorage);
    };
  }, [workspaceId]);

  const INBOX_BADGE_LIMIT = 100;
  // Backend-computed, not client-filtered: a genuine count of what's new
  // since the last visit, not the fetch page size dressed up as one (U3-H —
  // this used to read a suspicious, round "50" for a never-visited reader
  // with any real backlog, since the old version just returned however many
  // of the newest N events happened to be unseen).
  const { events: inboxNewEvents } = useWorkspaceActivity(workspaceId, INBOX_BADGE_LIMIT, lastSeenAt);
  const inboxUnreadCount = inboxNewEvents.length;
  const inboxUnreadLabel = inboxUnreadCount >= INBOX_BADGE_LIMIT ? `${INBOX_BADGE_LIMIT}+` : String(inboxUnreadCount);

  // Sage is the Operator, not a listed worker — the same exclusion every
  // other agent surface (AgentsList, command palette, Projects table) makes.
  const sageAgent = useMemo(() => findSageAgent(allAgents), [allAgents]);
  const agents = useMemo(
    () => (sageAgent ? allAgents.filter((a) => a.agent_id !== sageAgent.agent_id) : allAgents),
    [allAgents, sageAgent],
  );

  // MAN-317 — the count decides the rail's shape, never a tier check. See
  // agent-count-shape.ts. "none": hide every surface that aggregates agents
  // (nothing to aggregate) — today that's Inbox alone; Conversations and
  // Agents no longer live here at all (project-as-spine nav), so there is no
  // solo-agent redirect to compute for this rail any more either.
  const agentCountMode = useMemo(() => planAgentCountShape(agents.length), [agents.length]);

  // Reversible for free: this is recomputed from the live agent count on
  // every render, so the moment a second real agent exists,
  // `agentCountMode` stops being "none" and the full rail reappears on its
  // own — no setting anywhere to flip.
  const railItems = useMemo(
    () => visibleRailItems(agentCountMode === "none"),
    [agentCountMode],
  );
  const railHrefFor = useCallback(
    (item: { segment: string }) => `/w/${encodeURIComponent(workspaceId)}/${item.segment}`,
    [workspaceId],
  );

  const projectsExpanded = sections?.projects ?? true;

  // The rail's two-mode switch (2026-08-14) — see primary-rail-project-mode.ts.
  // Any route under /projects/{id}, including a specific agent's own chat
  // page, puts the rail in project mode.
  const activeProjectId = useMemo(() => activeProjectIdFromPathname(pathname), [pathname]);
  const projectMode = Boolean(activeProjectId);
  const activeProject = useMemo(
    () => projects.find((p) => p.id === activeProjectId) || null,
    [projects, activeProjectId],
  );
  // The open project's own agents, in project mode — same filter
  // agents/layout.tsx already applies for the compact ProjectAgentsRail this
  // supersedes at the primary-rail level (that component's own doc comment
  // now points here). `agents` above already excludes the Operator install.
  const projectAgents = useMemo(
    () => (activeProjectId ? agents.filter((a) => (a.project_id || "").trim() === activeProjectId) : []),
    [agents, activeProjectId],
  );
  // "…/agents/{agentId}/…" → {agentId}, so the row for whichever agent's own
  // page is open highlights — same match ProjectAgentsRail.tsx already uses.
  const activeAgentId = useMemo(() => {
    const m = pathname.match(/\/agents\/([^/]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }, [pathname]);
  const segment = useSelectedLayoutSegment();

  // Footer pulse — the exact same status tones the Agents table itself
  // derives from, and the same workspace-usage fetch every other "spend
  // today" figure in this UI already reads (see agents/page.tsx) — no new
  // endpoint, no new meaning for "today".
  const statusTones = useMemo(
    () => agents.map((a) => deriveStatus(a.hardware_status || "unknown", Boolean(a.stopped?.active), Boolean(a.current_run_id)).tone),
    [agents],
  );
  const workingCount = statusTones.filter((t) => t === "working").length;
  const stoppedCount = statusTones.filter((t) => t === "stopped").length;
  const [spendToday, setSpendToday] = useState(0);
  useEffect(() => {
    let cancelled = false;
    fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=workspace&period=day`, { credentials: "include" })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        let sum = 0;
        for (const a of d?.by_agent || []) sum += Number(a?.usd_cost) || 0;
        setSpendToday(sum);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId]);

  const [focusIdx, setFocusIdx] = useState(-1);
  const gPendingRef = useRef(false);
  const gTimer = useRef<number | null>(null);

  // THE KEYBOARD CURSOR MUST BE TRANSIENT, and it was permanent.
  //
  // focusIdx is set ONLY by the j/k rail navigation below, and it drew
  // `.fleet-rail-item--focus` — a 1px accent ring. Nothing ever cleared it:
  // not clicking a rail item, not navigating, not using the mouse at all. So
  // one stray `j` or `k` (easy to hit — the rail listens globally whenever
  // you are not typing in a field) parked a purple outline on a rail item
  // for the rest of the session. The founder reported it twice as "this
  // purple thing I always have on the ui".
  //
  // Cleared on any pointer interaction and on every route change: a
  // keyboard cursor means "where the KEYBOARD is", so the moment the person
  // reaches for the mouse or actually goes somewhere, it has nothing left to
  // point at. j/k still work exactly as before and still show the ring while
  // they are being used, which is the one case it exists for.
  useEffect(() => {
    setFocusIdx(-1);
  }, [pathname]);

  useEffect(() => {
    const clear = () => setFocusIdx((i) => (i < 0 ? i : -1));
    window.addEventListener("pointerdown", clear, true);
    return () => window.removeEventListener("pointerdown", clear, true);
  }, []);

  const hrefFor = (seg: string) => `/w/${encodeURIComponent(workspaceId)}/${seg}`;

  // Keyboard navigation. Ignored while typing or when a modifier is held (so
  // ⌘K and browser shortcuts are untouched).
  useEffect(() => {
    const isTyping = () => {
      const el = document.activeElement as HTMLElement | null;
      if (!el) return false;
      return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable;
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping()) return;
      const key = e.key.toLowerCase();

      if (gPendingRef.current) {
        gPendingRef.current = false;
        // Only a currently-VISIBLE row's chord fires — "g i" is a dead
        // shortcut at agent-count-shape.ts's "none" mode, the same as the
        // row itself not being on screen to click.
        const item = railItems.find((i) => i.chord === key);
        if (item) {
          e.preventDefault();
          router.push(railHrefFor(item));
        }
        return;
      }
      if (key === "g") {
        gPendingRef.current = true;
        if (gTimer.current) window.clearTimeout(gTimer.current);
        gTimer.current = window.setTimeout(() => { gPendingRef.current = false; }, 1200);
        return;
      }
      if (key === "j") {
        e.preventDefault();
        setFocusIdx((i) => Math.min(railItems.length - 1, i + 1));
      } else if (key === "k") {
        e.preventDefault();
        setFocusIdx((i) => (i < 0 ? railItems.length - 1 : Math.max(0, i - 1)));
      } else if (key === "enter" && focusIdx >= 0) {
        e.preventDefault();
        router.push(railHrefFor(railItems[focusIdx]));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusIdx, router, workspaceId, railItems, railHrefFor]);

  // Same custom-event mechanism as fleet:open-sage (FleetShell.tsx) — no
  // prop-drilling a setter from FleetCommandPalette back down into the rail.
  const openCommandPalette = () => window.dispatchEvent(new Event("fleet:open-command-palette"));

  // The backend echoes the raw workspace id back as `name` for a workspace
  // that was never given a real one — same guard Breadcrumbs used to apply.
  const hasRealWorkspaceName = Boolean(workspace?.name) && workspace!.name !== workspaceId;
  const workspaceName = hasRealWorkspaceName ? workspace!.name : "Empyralis";

  return (
    <aside
      ref={rail.elRef as React.RefObject<HTMLElement>}
      className={`fleet-rail${effectiveCollapsed ? " fleet-rail--collapsed" : ""}${mobileOpen ? " fleet-rail--mobile-open" : ""}`}
    >
      {/* Resize handle. 8px hit area straddling the rail's trailing edge; the
          hairline inside it is invisible until you hover, focus or drag it,
          so the edge is discoverable without drawing a permanent line down
          the middle of the app. Enter toggles collapse, which makes the same
          control serve both gestures from the keyboard. */}
      <div
        {...rail.separatorProps}
        onKeyDown={onResizerKeyDown}
        className="fleet-rail-resizer"
        aria-label="Resize sidebar"
        title="Drag to resize · Enter to collapse"
      />
      <div className="fleet-rail-header">
        <div className="fleet-rail-header-top">
          {/* Truncates hard at the rail's 220px width (long real names —
              "Legibility Verify Two's Workspace" — otherwise overflow into
              the collapse toggle). A native title tooltip is the cheapest
              fix that doesn't touch the rail's width contract: hover (or a
              long-press on touch) reads the full name instead of losing it
              to an ellipsis with no recovery route. Also the switcher's
              trigger (MAN-108 Phase 1 bug 4) — every workspace the signed-in
              user belongs to, one click away. */}
          <WorkspaceSwitcher workspaceId={workspaceId} workspaceName={workspaceName} collapsed={effectiveCollapsed} />
          <button
            type="button"
            className="fleet-rail-control-btn fleet-rail-control-btn--collapse fleet-rail-collapse-top"
            onClick={onToggleCollapsed}
            title={collapsed ? "Expand" : "Collapse"}
            aria-label="Toggle rail"
          >
            {collapsed ? <PanelLeftOpen size={CONTROL_ICON} strokeWidth={1.75} /> : <PanelLeftClose size={CONTROL_ICON} strokeWidth={1.75} />}
          </button>
        </div>
        {!effectiveCollapsed && (
          <div className="fleet-rail-quick-actions">
            <button
              type="button"
              className="fleet-rail-search-btn"
              aria-label="Search"
              onClick={openCommandPalette}
            >
              <Search size={13} strokeWidth={1.75} aria-hidden="true" />
              <span>Search</span>
              {/* Hidden from the accessible name — a sighted-only shortcut
                  hint, not part of what the button is called. */}
              <kbd aria-hidden="true">⌘K</kbd>
            </button>
          </div>
        )}
      </div>

      {projectMode ? (
        // ── Project mode ──────────────────────────────────────────────────
        // Founder: opening a project switches the rail ENTIRELY. Inbox and
        // Projects are gone; this project's agent names take their place;
        // one Back control at the top returns to normal (Inbox + Projects)
        // mode. Back links at the projects LIST (not workspace home) — the
        // instant the pathname no longer carries /projects/{id}, activeProjectId
        // goes null and this branch stops rendering on its own; nothing here
        // "closes" project mode, the URL leaving it is what does.
        <nav className="fleet-rail-nav fleet-rail-nav--project" aria-label={activeProject ? `${activeProject.name} agents` : "Project agents"}>
          <Link
            href={hrefFor("projects")}
            className="fleet-rail-item fleet-rail-back"
            aria-label="Back to Inbox and Projects"
            title={effectiveCollapsed ? "Back" : undefined}
          >
            <span className="fleet-rail-item-icon">
              <ArrowLeft size={RAIL_ICON} strokeWidth={1.75} />
            </span>
            {!effectiveCollapsed && <span className="fleet-rail-item-label">Back</span>}
          </Link>

          {!effectiveCollapsed && (
            <Link href={hrefFor(`projects/${encodeURIComponent(activeProjectId || "")}`)} className="fleet-rail-project-header">
              <ProjectIcon icon={activeProject?.icon} tint={activeProject?.tint} size={20} glyphSize={12} />
              <span className="fleet-rail-project-header-name">{activeProject?.name || "Project"}</span>
            </Link>
          )}

          <div className="fleet-agents-rail-list fleet-rail-project-agents">
            {projectAgents.length === 0 ? (
              !effectiveCollapsed && <div className="fleet-rail-project-agents-empty">No agents in this project yet.</div>
            ) : (
              projectAgents.map((a) => {
                const active = a.agent_id === activeAgentId;
                const tone = deriveStatus(a.hardware_status || "unknown", Boolean(a.stopped?.active), Boolean(a.current_run_id)).tone;
                return (
                  <Link
                    key={a.agent_id}
                    href={hrefFor(`projects/${encodeURIComponent(activeProjectId || "")}/agents/${encodeURIComponent(a.agent_id)}/chat`)}
                    aria-current={active ? "page" : undefined}
                    title={effectiveCollapsed ? (a.label || "Unnamed agent") : undefined}
                    className={`fleet-agents-rail-row${active ? " fleet-agents-rail-row--active" : ""}`}
                    onClick={() => rememberLastViewedAgent(a.agent_id)}
                  >
                    <AgentSigil seed={a.agent_id} size={20} />
                    {!effectiveCollapsed && <span className="fleet-agents-rail-row-label">{a.label || "Unnamed agent"}</span>}
                    {!effectiveCollapsed && <StatusDot tone={tone} size={7} />}
                  </Link>
                );
              })
            )}
          </div>
        </nav>
      ) : (
        // ── Normal mode: Inbox + Projects ────────────────────────────────
        <nav className="fleet-rail-nav">
          {railItems.map((item, idx) => {
            const Icon = item.icon;
            const active = segment === item.segment;
            const focused = focusIdx === idx;
            const isProjects = item.key === "projects";
            const isInbox = item.key === "inbox";
            const showProjectsSubnav = isProjects && !effectiveCollapsed && projects.length > 0;
            const expanded = projectsExpanded;
            const showToggle = showProjectsSubnav;
            return (
              <div key={item.key} className="fleet-rail-nav-group">
                <div className="fleet-rail-item-row">
                  {/* Real <a href> (MAN-145 item 6), not a router.push() button —
                      a plain left-click still behaves exactly like the old
                      onClick (Next's Link does a client-side transition, same
                      as router.push), but ⌘/Ctrl-click, middle-click, and
                      right-click now get real browser behaviour for free,
                      since they're native <a> semantics Link doesn't override.
                      That's also why this is safe against the in-app tab strip
                      (FleetTabs.tsx): its modifier-click interception is
                      explicitly scoped to `.fleet-shell-main` and skips the
                      rail on purpose ("the rail keeps native browser
                      behaviour" — see FleetTabs.tsx's resolveHref) — there is
                      nothing here for it to conflict with. */}
                  <Link
                    href={railHrefFor(item)}
                    title={effectiveCollapsed ? item.label : undefined}
                    aria-label={item.label}
                    aria-current={active ? "page" : undefined}
                    className={`fleet-rail-item${active ? " fleet-rail-item--active" : ""}${focused ? " fleet-rail-item--focus" : ""}`}
                  >
                    <span className="fleet-rail-item-icon">
                      <Icon size={RAIL_ICON} strokeWidth={1.75} />
                    </span>
                    {!effectiveCollapsed && <span className="fleet-rail-item-label">{item.label}</span>}
                    {!effectiveCollapsed && isInbox && inboxUnreadCount > 0 ? (
                      <span className="fleet-rail-item-count">{inboxUnreadLabel}</span>
                    ) : !effectiveCollapsed ? (
                      <kbd className="fleet-rail-item-chord" aria-hidden="true">G {item.chord.toUpperCase()}</kbd>
                    ) : null}
                  </Link>
                  {showToggle && (
                    <button
                      type="button"
                      className={`fleet-rail-subnav-toggle${expanded ? " is-expanded" : ""}`}
                      onClick={() => onToggleSection?.("projects")}
                      aria-expanded={expanded}
                      aria-label={expanded ? `Collapse ${item.label.toLowerCase()}` : `Expand ${item.label.toLowerCase()}`}
                    >
                      <ChevronRight size={13} strokeWidth={2} />
                    </button>
                  )}
                </div>
                {/* Every project is a plain link now — drilling into one
                    switches the rail to project mode above rather than
                    expanding a nested section list inline (see
                    primary-rail-project-mode.ts's module comment for what
                    this replaced and why). */}
                {showProjectsSubnav && projectsExpanded && (
                  <div className="fleet-rail-subnav">
                    {projects.map((p) => {
                      const projectHref = `${hrefFor("projects")}/${encodeURIComponent(p.id)}`;
                      return (
                        <Link
                          key={p.id}
                          href={projectHref}
                          className="fleet-rail-subitem"
                        >
                          <ProjectIcon icon={p.icon} tint={p.tint} size={18} glyphSize={11} />
                          <span className="fleet-rail-subitem-label">{p.name}</span>
                        </Link>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </nav>
      )}

      {/* Ask AI — moved here (2026-07) from a floating bottom-right corner
          spot that, on mobile, sat directly on top of the chat composer's
          Send button and blocked it. Deliberately does NOT close the mobile
          drawer when the panel opens: it's a DOM descendant of .fleet-rail,
          so on mobile it inherits the rail's own z-index:70 (above the
          scrim's 65) automatically without needing to out-rank it itself.
          Closing the drawer on the same click would instead be actively
          wrong — .fleet-rail's slide animation is a `transform`, which
          reparents any position:fixed descendant's containing block to the
          rail itself, so a closing (translating-away) drawer would carry a
          just-opened fixed panel off-screen with it. Leaving the drawer open
          and letting the panel render within it sidesteps that entirely. */}
      <div className="fleet-rail-utility">
        <SageLauncher workspaceId={workspaceId} open={sageOpen} onOpen={onOpenSage} onClose={onCloseSage} />
      </div>

      {/* System health + Bug report only — Theme and Keyboard shortcuts used
          to sit here too, but a display toggle and a static shortcuts
          reference are each set-once/looked-up-occasionally, not something
          that earns equal billing with live paired-computer/gateway status
          in the rail's permanent daily row (CLAUDE.md: "most configuration
          is set once and does not deserve equal billing with the things
          people look at daily"). Both moved into the account menu below
          (2026-08) as additional popover rows. */}
      <div className="fleet-rail-controls">
        <SystemHealthButton workspaceId={workspaceId} />
        <BugReportButton workspaceId={workspaceId} />
      </div>

      {!effectiveCollapsed && (
        <div className="fleet-rail-pulse">
          {workingCount} working · {stoppedCount} stopped · {money(spendToday)} today
        </div>
      )}

      <CreditBalanceChip workspaceId={workspaceId} collapsed={effectiveCollapsed} />

      <AccountMenu
        workspaceId={workspaceId}
        ownerName={ownerName}
        ownerEmail={ownerEmail}
        ownerRole={ownerRole}
        collapsed={effectiveCollapsed}
        theme={theme}
        onToggleTheme={onToggleTheme}
      />
    </aside>
  );
}

// ── Account menu (owner block + popover: Settings / Credits / Log out) ─────

function AccountMenu({
  workspaceId,
  ownerName,
  ownerEmail,
  ownerRole,
  collapsed,
  theme,
  onToggleTheme,
}: {
  workspaceId: string;
  ownerName: string;
  ownerEmail: string;
  ownerRole: string;
  collapsed: boolean;
  theme: FleetTheme;
  onToggleTheme: () => void;
}) {
  const { actions: accountShellActions } = useAccountShell();
  const [open, setOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  const settingsHref = `/w/${encodeURIComponent(workspaceId)}/settings`;
  const usageHref = `/w/${encodeURIComponent(workspaceId)}/billing`;

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (ref.current?.contains(event.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const handleLogout = async () => {
    setLoggingOut(true);
    setError(null);
    try {
      await logout();
      accountShellActions.clearSession();
      window.location.replace("/login");
    } catch {
      setError("Logout could not finish.");
      setLoggingOut(false);
    }
  };

  return (
    <div className="fleet-rail-owner" ref={ref}>
      {open && (
        // The popover is taller than the gap above it and visually overlaps
        // the theme/collapse controls row — without this backdrop, a click
        // meant to dismiss the menu lands ON that row (or worse, on "Log
        // out" underneath), firing its action instead of just closing.
        // The backdrop absorbs that first click; the popover itself sits
        // above it via z-index and stays clickable.
        <div className="fleet-rail-popover-backdrop" onClick={() => setOpen(false)} />
      )}
      {open && (
        <div className="fleet-rail-account-popover" role="menu" aria-label="Account menu">
          <Link className="fleet-rail-account-popover-row" href={settingsHref} role="menuitem" onClick={() => setOpen(false)}>
            <Settings size={14} strokeWidth={1.75} />
            Settings
          </Link>
          <Link className="fleet-rail-account-popover-row" href={usageHref} role="menuitem" onClick={() => setOpen(false)}>
            <BarChart3 size={14} strokeWidth={1.75} />
            Usage
          </Link>
          <button
            type="button"
            className="fleet-rail-account-popover-row"
            role="menuitem"
            onClick={onToggleTheme}
          >
            {theme === "dark" ? <Sun size={14} strokeWidth={1.75} /> : <Moon size={14} strokeWidth={1.75} />}
            {theme === "dark" ? "Switch to light" : "Switch to dark"}
          </button>
          <FleetHelpButton />
          <button
            type="button"
            className="fleet-rail-account-popover-row"
            role="menuitem"
            disabled={loggingOut}
            onClick={() => { void handleLogout(); }}
          >
            <LogOut size={14} strokeWidth={1.75} />
            {loggingOut ? "Signing out…" : "Log out"}
          </button>
          {error && <div className="fleet-rail-account-error">{error}</div>}
        </div>
      )}
      <button
        type="button"
        className="fleet-rail-owner-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        // Same unnamed-button pattern as the rail's other icon+text controls
        // (MAN-145 item 5) — found while re-verifying the a11y tree after
        // that pass, not in the original list, but the identical bug.
        aria-label={`Account menu — ${ownerName}`}
        onClick={() => { setOpen((v) => !v); setError(null); }}
      >
        <div className="fleet-rail-owner-avatar">{(ownerName || "O").charAt(0).toUpperCase()}</div>
        {!collapsed && (
          <div className="fleet-rail-owner-text">
            <div className="fleet-rail-owner-name">{ownerName}</div>
            <div className="fleet-rail-owner-role">{ownerEmail || ownerRole}</div>
          </div>
        )}
      </button>
    </div>
  );
}
