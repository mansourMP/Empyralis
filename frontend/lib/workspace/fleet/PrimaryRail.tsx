"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, usePathname, useSelectedLayoutSegment } from "next/navigation";
import {
  BarChart3,
  Bot,
  ChevronRight,
  FolderKanban,
  Inbox,
  LogOut,
  MessagesSquare,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  Sun,
  type LucideIcon,
} from "lucide-react";

import { logout } from "@/lib/auth/auth-client";
import { useAccountShell } from "@/lib/shell/account-shell-context";
import { getInboxLastSeenAt, resolveAgentProjectId, useFleetAgents, useFleetProjects, useFleetWorkspace, useWorkspaceActivity } from "./fleet-data";
import { deriveStatus, findSageAgent } from "./fleet-presentation";
import { StatusDot } from "./fleet-indicators";
import { ProjectIcon } from "./fleet-project-identity";
import { FleetHelpButton } from "./FleetHelpButton";
import { SageLauncher } from "./SageLauncher";
import { CreditBalanceChip } from "./CreditBalanceChip";
import { SystemHealthButton } from "./SystemHealthButton";
import { BugReportButton } from "./BugReportButton";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";

import { RAIL_WIDTH, useResizableWidth } from "./fleet-preferences";
import type { FleetTheme, FleetSectionKey } from "./fleet-preferences";

type RailNavItem = { key: string; label: string; segment: string; icon: LucideIcon; chord: string };

// Rail vocabulary. `chord` is the second key of Linear-style "g then <key>".
//
// Every entry here is WORK — the things a teammate touches daily. Hardware
// used to sit at the bottom of this list and was removed in the 2026-07
// repositioning: which box an agent runs on is decided once, at setup, and
// then never thought about again, so it belongs in Settings (where it now
// renders as a real section — see settings/page.tsx and HardwareSection.tsx)
// rather than in the app's spine. The /w/{ws}/hardware route itself is still
// live, so every link, bookmark and redirect that pointed at it still
// resolves; it just isn't a standing destination any more.
const RAIL_ITEMS: RailNavItem[] = [
  { key: "inbox", label: "Inbox", segment: "inbox", icon: Inbox, chord: "i" },
  { key: "conversations", label: "Conversations", segment: "conversations", icon: MessagesSquare, chord: "c" },
  { key: "projects", label: "Projects", segment: "projects", icon: FolderKanban, chord: "p" },
  { key: "agents", label: "Agents", segment: "agents", icon: Bot, chord: "a" },
];

const RAIL_ICON = 16;
const CONTROL_ICON = 16;

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
 * Persistent primary rail — the app's spine. A populated workspace header
 * (U3-G) replaces the static product wordmark; Projects AND Agents are both
 * real, collapsible sub-lists of the workspace's own data (Linear's "Teams"
 * treatment, same pattern for both — see the shared showSubnav logic
 * below); a quiet footer line reports the fleet's pulse above the account
 * block. Billing lives in the account menu instead — it's a look-up-
 * occasionally screen, not a nav destination. Keyboard: `j`/`k` move a
 * highlight, Enter opens it; `g` then a section key jumps directly (g i
 * inbox, g c conversations, g p projects, g a agents) — the Linear
 * muscle-memory model.
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
      // Dragging left past the minimum is how you collapse — the rail snaps
      // shut on release rather than being pinned at an unusable 200px.
      if (raw < RAIL_WIDTH.min) {
        onToggleCollapsed();
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
    // Let the drag *show* widths down to the collapsed size so the snap
    // reads as a destination, not a wall.
    dragFloor: RAIL_WIDTH.collapsed,
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

  const projectsExpanded = sections?.projects ?? true;
  const agentsExpanded = sections?.agents ?? true;

  // "/w/{ws}/projects/{id}[/...]" → {id}, so the matching rail row highlights
  // whether you're on the project's own page or one of its agents' pages.
  const activeProjectId = useMemo(() => {
    const m = pathname.match(/\/projects\/([^/]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }, [pathname]);
  // Same idea for a single agent — "/agents/{agentId}" appears under a
  // project's own path (…/projects/{p}/agents/{a}/{tab}), the one route an
  // agent detail ever renders at.
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
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=workspace&period=day`, { credentials: "include" })
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
        const item = RAIL_ITEMS.find((i) => i.chord === key);
        if (item) {
          e.preventDefault();
          router.push(hrefFor(item.segment));
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
        setFocusIdx((i) => Math.min(RAIL_ITEMS.length - 1, i + 1));
      } else if (key === "k") {
        e.preventDefault();
        setFocusIdx((i) => (i < 0 ? RAIL_ITEMS.length - 1 : Math.max(0, i - 1)));
      } else if (key === "enter" && focusIdx >= 0) {
        e.preventDefault();
        router.push(hrefFor(RAIL_ITEMS[focusIdx].segment));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusIdx, router, workspaceId]);

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

      <nav className="fleet-rail-nav">
        {RAIL_ITEMS.map((item, idx) => {
          const Icon = item.icon;
          const active = segment === item.segment;
          const focused = focusIdx === idx;
          const isProjects = item.key === "projects";
          const isAgents = item.key === "agents";
          const isInbox = item.key === "inbox";
          const showProjectsSubnav = isProjects && !effectiveCollapsed && projects.length > 0;
          const showAgentsSubnav = isAgents && !effectiveCollapsed && agents.length > 0;
          const expanded = isProjects ? projectsExpanded : agentsExpanded;
          const showToggle = showProjectsSubnav || showAgentsSubnav;
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
                  href={hrefFor(item.segment)}
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
                    onClick={() => onToggleSection?.(isProjects ? "projects" : "agents")}
                    aria-expanded={expanded}
                    aria-label={expanded ? `Collapse ${item.label.toLowerCase()}` : `Expand ${item.label.toLowerCase()}`}
                  >
                    <ChevronRight size={13} strokeWidth={2} />
                  </button>
                )}
              </div>
              {showProjectsSubnav && projectsExpanded && (
                <div className="fleet-rail-subnav">
                  {projects.map((p) => {
                    const projectActive = activeProjectId === p.id;
                    return (
                      <Link
                        key={p.id}
                        href={`${hrefFor("projects")}/${encodeURIComponent(p.id)}`}
                        className={`fleet-rail-subitem${projectActive ? " fleet-rail-subitem--active" : ""}`}
                      >
                        <ProjectIcon icon={p.icon} tint={p.tint} size={18} glyphSize={11} />
                        <span className="fleet-rail-subitem-label">{p.name}</span>
                      </Link>
                    );
                  })}
                </div>
              )}
              {showAgentsSubnav && agentsExpanded && (
                <div className="fleet-rail-subnav">
                  {agents.map((a) => {
                    const agentActive = activeAgentId === a.agent_id;
                    const st = deriveStatus(a.hardware_status || "unknown", Boolean(a.stopped?.active), Boolean(a.current_run_id));
                    return (
                      <Link
                        key={a.agent_id}
                        href={`${hrefFor("projects")}/${encodeURIComponent(resolveAgentProjectId(a.project_id, projects))}/agents/${encodeURIComponent(a.agent_id)}/overview`}
                        className={`fleet-rail-subitem${agentActive ? " fleet-rail-subitem--active" : ""}`}
                      >
                        <StatusDot tone={st.tone} size={7} />
                        <span className="fleet-rail-subitem-label">{a.label || "Unnamed agent"}</span>
                      </Link>
                    );
                  })}
                  {/* Flat list is the right call at this scale. Once a fleet
                      runs into dozens of agents the answer is favorites/
                      pinning, not a taller list — deliberately not built
                      here (U3-G scope: presence, not triage tooling). */}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      {/* Ask AI + Shortcuts — moved here (2026-07) from a floating
          bottom-right corner pair that, on mobile, sat directly on top of
          the chat composer's Send button and blocked it. Deliberately does
          NOT close the mobile drawer when either panel opens: both panels
          are DOM descendants of .fleet-rail, so on mobile they inherit the
          rail's own z-index:70 (above the scrim's 65) automatically without
          needing to out-rank it themselves. Closing the drawer on the same
          click would instead be actively wrong — .fleet-rail's slide
          animation is a `transform`, which reparents any position:fixed
          descendant's containing block to the rail itself, so a closing
          (translating-away) drawer would carry a just-opened fixed panel
          off-screen with it. Leaving the drawer open and letting the panel
          render within it sidesteps that entirely. */}
      <div className="fleet-rail-utility">
        <SageLauncher workspaceId={workspaceId} open={sageOpen} onOpen={onOpenSage} onClose={onCloseSage} />
      </div>

      <div className="fleet-rail-controls">
        <button
          type="button"
          className="fleet-rail-control-btn"
          onClick={onToggleTheme}
          title={theme === "dark" ? "Switch to light" : "Switch to dark"}
          aria-label="Toggle theme"
        >
          {theme === "dark" ? <Sun size={CONTROL_ICON} strokeWidth={1.75} /> : <Moon size={CONTROL_ICON} strokeWidth={1.75} />}
        </button>
        <SystemHealthButton workspaceId={workspaceId} />
        <BugReportButton workspaceId={workspaceId} />
        <FleetHelpButton asControl />
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
}: {
  workspaceId: string;
  ownerName: string;
  ownerEmail: string;
  ownerRole: string;
  collapsed: boolean;
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
