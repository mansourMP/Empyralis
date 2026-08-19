"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, usePathname, useSelectedLayoutSegment } from "next/navigation";
import {
  BarChart3,
  ChevronLeft,
  ChevronRight,
  Keyboard,
  LogOut,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings,
  Sun,
} from "lucide-react";

import { logout } from "@/lib/auth/auth-client";
import { useAccountShell } from "@/lib/shell/account-shell-context";
import { useRevealedEmail } from "@/lib/shell/use-revealed-email";
import { getInboxLastSeenAt, resolveAgentProjectId, useFleetAgents, useFleetProjects, useFleetWorkspace, useFleetWorkspaceTasks, useWorkspaceActivity } from "./fleet-data";
import { deriveStatus, findSageAgent } from "./fleet-presentation";
import { ProjectIcon } from "./fleet-project-identity";
import { planAgentCountShape } from "./agent-count-shape";
import { myWorkBadgeCount } from "./my-work";
import { useOwnAccountId } from "./members-data";
import { visibleRailItems } from "./primary-rail-nav";
import {
  projectAgentsSpaceLinks,
  railSpaceFromPathname,
  settingsSpaceLinks,
  spaceBackHref,
  workspaceAgentsSpaceLinks,
  type RailSpaceLink,
} from "./primary-rail-space";
import { projectAgentsSpaceIsActive } from "./project-agents-rail-shape";
import { workspaceAgentsSpaceIsActive } from "./workspace-agents-rail-shape";
import { activeProjectIdFromPathname } from "./primary-rail-project-mode";
import { AgentSigil, StatusDot } from "./fleet-indicators";
import { rememberLastViewedAgent } from "./AgentsList";
import { SageLauncher } from "./SageLauncher";
import { CreditBalanceChip } from "./CreditBalanceChip";
import { SystemHealthButton } from "./SystemHealthButton";
import { BugReportButton } from "./BugReportButton";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";

import { RAIL_WIDTH, useResizableWidth } from "./fleet-preferences";
import type { FleetTheme, FleetSectionKey } from "./fleet-preferences";

// Rail vocabulary lives in primary-rail-nav.ts — a pure, dependency-light
// module a plain test imports directly (primary-rail-nav.test.ts). Read that
// file's header for the full shape and the two earlier versions it replaces;
// the one line that decides everything here is the founder's:
//
//     THE RAIL IS PLACES YOU GO. THE PAGE IS THINGS YOU DO.
//
// So this rail is FLAT by default: Inbox, My work, the project list,
// "+ New project", with Settings pinned at the foot. Opening a project
// changes the PAGE — its Tasks/Documents/Agents/People tabs — and never the
// rail. Nothing was removed to get here (founder: "tasks agents and
// documents must not disappear"), and Settings gained a row here while
// keeping its account-menu entry.
//
// ONE refinement on top of that (founder, 2026-08-16): "the rail is where
// you pick; the content is what you picked." Inside a surface whose content
// used to carry its OWN second nav column — a SPACE — the rail swaps its
// flat list for that space's pick-list, with a "‹ Back" real-link row on
// top. See primary-rail-space.ts for the whole rule and the boundary with
// the 2026-08-15 flat decision (merely opening a project is NOT a space).
//
// Hardware left the rail in the 2026-07 repositioning for an unrelated
// reason (set-once config) and lives in Settings — the /w/{ws}/hardware
// route is still live, so every link/bookmark/redirect that pointed at it
// still resolves.

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
 * Persistent primary rail — the app's spine, and PROJECTS is the spine of
 * the spine (CLAUDE.md, 2026-08-13 navigation decision). A populated
 * workspace header (U3-G) replaces the static product wordmark; Projects is
 * a real, collapsible sub-list of the workspace's own data (Linear's
 * "Teams" treatment — genuine containers you navigate into); a quiet footer
 * line reports the fleet's pulse above the account block. Billing lives in
 * the account menu instead — it's a look-up-occasionally screen, not a nav
 * destination. Keyboard: `j`/`k` move a highlight, Enter opens it; `g` then
 * a section key jumps directly (g i inbox, g m my work, g p projects,
 * g a agents, g c context) — the Linear muscle-memory model, and it covers
 * the pinned footer row too so pinning is a position, not a demotion.
 *
 * Conversations is GONE from this rail (see primary-rail-nav.ts) — it still
 * aggregates across every project's agents, exactly the boundary "an agent
 * belongs to its project and works only there" says a nav surface must not
 * reach past.
 *
 * Agents CAME BACK 2026-08-19 — a founder reversal on this one point (see
 * primary-rail-nav.ts's own history for the exact words), not a re-opening
 * of that boundary. It is a top-level row again, but it does not aggregate
 * into the content area the way the pre-2026-08-13 shape did: pressing it
 * morphs THIS rail into the workspace-agents space — every real agent,
 * across every project — the same mechanism Settings already uses
 * (primary-rail-space.ts, gated by workspace-agents-rail-shape.ts). A
 * project's own Agents tab is untouched and still opens the older,
 * project-scoped twin of this same mechanism (project-agents) — the two
 * are independent, see primary-rail-space.ts's header for why they stay
 * that way rather than merging.
 *
 * COUNTS ARE NON-ZERO ONLY. Inbox and My work each show a number when they
 * have one and nothing when they don't — a zero badge is noise, and it is
 * also the one number nobody needs, since an empty surface says so itself
 * the moment you open it.
 */
export function PrimaryRail({
  workspaceId,
  ownerDisplayName,
  ownerEmailObfuscated = "",
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
  ownerDisplayName?: string;
  // XOR-obfuscated, not display-ready — see ssr-safe-email.ts. Decoded
  // below, client-side only, via useRevealedEmail.
  ownerEmailObfuscated?: string;
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

  // My work's count — the SAME function the page itself partitions with
  // (my-work.ts), against the SAME workspace-wide fetch, so the badge and
  // the page can never disagree about what "mine" means. Both readers share
  // one polled resource (useFleetWorkspaceTasks' cache key), so having the
  // rail show a number costs no extra request while the page is open.
  const { tasks: workspaceTasks } = useFleetWorkspaceTasks(workspaceId);
  const myAccountId = useOwnAccountId();
  const myWorkCount = useMemo(
    () => myWorkBadgeCount(workspaceTasks, myAccountId),
    [workspaceTasks, myAccountId],
  );

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

  // ── Rail space (2026-08-16) ──────────────────────────────────────────────
  // "The rail is where you pick; the content is what you picked." Inside a
  // space the flat list above is not rendered at all — the space's own
  // pick-list takes its place, with a "‹ Back" real-link row on top. See
  // primary-rail-space.ts for which pathnames are spaces and why.
  const space = useMemo(() => railSpaceFromPathname(pathname), [pathname]);
  // The project-agents space lists THIS project's agents, out of the same
  // useFleetAgents fetch this component already holds — no second fetch.
  // And because the rail itself persists across every navigation, switching
  // agents never remounts or re-fetches this list and never loses its
  // scroll position — the job the old in-content agents/layout.tsx split
  // used to do, now a property of the rail being the rail.
  const spaceProjectAgents = useMemo(
    () =>
      space?.kind === "project-agents"
        ? allAgents.filter((a) => (a.project_id || "").trim() === space.projectId)
        : [],
    [space, allAgents],
  );
  // The workspace-agents space lists EVERY real agent in the workspace —
  // `agents` (declared above: allAgents with Sage/the Operator already
  // filtered out, the same exclusion every other agent surface here makes)
  // is exactly that list. Each row needs a resolved project id to build its
  // href (resolveAgentProjectId — the same fallback every other agent link
  // in this app already goes through), so that resolution happens here, not
  // inside primary-rail-space.ts, which stays free of fleet-data.
  const spaceWorkspaceAgents = useMemo(
    () =>
      space?.kind === "workspace-agents"
        ? agents.map((a) => ({ agent_id: a.agent_id, label: a.label, project_id: resolveAgentProjectId(a.project_id, projects) }))
        : [],
    [space, agents, projects],
  );
  // Whether the agents space actually morphs the rail is the SAME predicate
  // ProjectDetailPage (project-agents) / the workspace Agents page
  // (workspace-agents) calls to decide whether to hide its own picking
  // surface — never a second rule, and never two rules that happen to
  // agree today. Below the gate the rail simply stays flat.
  const effectiveSpace =
    space?.kind === "project-agents"
      ? projectAgentsSpaceIsActive(true, spaceProjectAgents.length)
        ? space
        : null
      : space?.kind === "workspace-agents"
      ? workspaceAgentsSpaceIsActive(true, agents.length)
        ? space
        : null
      : space;
  // Where Settings' Back returns to: the last pathname seen OUTSIDE any
  // space. A ref, not state — it only ever changes alongside a pathname
  // change, which re-renders this component anyway. Never valid across
  // workspaces or from inside a space (spaceBackHref enforces both); a
  // direct load into a space leaves it null and Back falls back to the
  // workspace root. The project-agents space ignores it by design — its
  // Back is always the owning project.
  const lastOutsideSpaceRef = useRef<string | null>(null);
  useEffect(() => {
    if (railSpaceFromPathname(pathname) === null) {
      lastOutsideSpaceRef.current = pathname;
    }
  }, [pathname]);
  const spaceLinks: RailSpaceLink[] | null = useMemo(() => {
    if (!effectiveSpace) return null;
    if (effectiveSpace.kind === "settings") return settingsSpaceLinks(effectiveSpace);
    if (effectiveSpace.kind === "workspace-agents") return workspaceAgentsSpaceLinks(effectiveSpace, spaceWorkspaceAgents);
    return projectAgentsSpaceLinks(effectiveSpace, spaceProjectAgents);
  }, [effectiveSpace, spaceProjectAgents, spaceWorkspaceAgents]);
  const spaceBack = effectiveSpace ? spaceBackHref(effectiveSpace, lastOutsideSpaceRef.current) : null;
  const spaceProject =
    effectiveSpace?.kind === "project-agents"
      ? projects.find((p) => p.id === effectiveSpace.projectId)
      : null;
  const spaceTitle = effectiveSpace
    ? effectiveSpace.kind === "settings"
      ? "Settings"
      : effectiveSpace.kind === "workspace-agents"
      ? "Agents"
      : spaceProject?.name || "Agents"
    : null;

  const railHrefFor = useCallback(
    (item: { segment: string }) => `/w/${encodeURIComponent(workspaceId)}/${item.segment}`,
    [workspaceId],
  );
  const railCountFor = useCallback(
    (key: string): string | null => {
      // Non-zero only — a zero badge is noise (see this component's header).
      if (key === "inbox") return inboxUnreadCount > 0 ? inboxUnreadLabel : null;
      if (key === "my-work") return myWorkCount > 0 ? String(myWorkCount) : null;
      return null;
    },
    [inboxUnreadCount, inboxUnreadLabel, myWorkCount],
  );

  const projectsExpanded = sections?.projects ?? true;

  // Which project row reads as current. This is ALL that is left of the rail
  // knowing a project is open — the 2026-08-14 "project mode" that swapped
  // the whole rail for the project's agent names is gone (see
  // primary-rail-nav.ts's header for the three shapes and why this one is
  // flat). activeProjectIdFromPathname still answers "am I inside a project"
  // exactly as before, including from a specific agent's own chat page, so a
  // reader deep inside a project still sees which one they are in.
  const activeProjectId = useMemo(() => activeProjectIdFromPathname(pathname), [pathname]);
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
      // Inside a space the flat list is not on screen, so the j/k cursor has
      // nothing to move over. The g-chords above still fire — they navigate
      // out of the space, exactly like pressing Back and then a row.
      if (effectiveSpace) return;
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
  }, [focusIdx, router, workspaceId, railItems, railHrefFor, effectiveSpace]);

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

      {/* ── The rail's one branch: a space, or the flat list ──────────────
          Inside a space (Settings; a project's Agents section at 2+ agents
          — primary-rail-space.ts) the rail IS that space's pick-list: a
          "‹ Back" real-link row, the space's name, then its destinations,
          active row marked exactly like the flat rows. Everywhere else:
          Inbox · My work · Projects (with its flat project sub-list) ·
          "+ New project" — merely opening a project changes the page,
          never the rail. */}
      {effectiveSpace && spaceLinks && spaceBack ? (
        <nav className="fleet-rail-nav" aria-label={spaceTitle ?? undefined}>
          <Link
            href={spaceBack}
            className="fleet-rail-item fleet-rail-space-back"
            title={effectiveCollapsed ? "Back" : undefined}
            aria-label="Back"
          >
            <span className="fleet-rail-item-icon">
              <ChevronLeft size={RAIL_ICON} strokeWidth={1.75} />
            </span>
            {!effectiveCollapsed && <span className="fleet-rail-item-label">Back</span>}
          </Link>
          {/* The heading is DELIBERATELY absent for the workspace-agents
              space (founder, 2026-08-19: "i dont want this in this left rail
              after i open this agents"). The rail rows ARE the agents, and a
              grey "AGENTS" label above a list of agents restates what the
              list already says. `spaceTitle` still feeds the <nav>'s
              aria-label above, so a screen reader keeps the name it needs —
              removing the visible heading must not remove the accessible
              one. */}
          {!effectiveCollapsed && spaceTitle && effectiveSpace.kind !== "workspace-agents" && (
            <div className="fleet-rail-space-title">{spaceTitle}</div>
          )}
          {spaceLinks.map((link) => {
            const Icon = link.icon;
            // An agent row carries the agent's own identity (sigil + live
            // status dot) instead of a generic icon — the same rendering
            // the old in-content list used, now living where picking lives.
            // workspace-agents looks the row up in the full, already
            // Sage-excluded `agents` list (not spaceWorkspaceAgents, which
            // only carries the fields workspaceAgentsSpaceLinks needs to
            // build an href — AgentSigil/StatusDot need the real record).
            const agent =
              effectiveSpace.kind === "project-agents"
                ? spaceProjectAgents.find((a) => a.agent_id === link.key)
                : effectiveSpace.kind === "workspace-agents"
                ? agents.find((a) => a.agent_id === link.key)
                : undefined;
            return (
              <Link
                key={link.key}
                href={link.href}
                title={effectiveCollapsed ? link.label : undefined}
                aria-label={link.label}
                aria-current={link.active ? "page" : undefined}
                className={`fleet-rail-item${link.active ? " fleet-rail-item--active" : ""}`}
                onClick={agent ? () => rememberLastViewedAgent(agent.agent_id) : undefined}
              >
                <span className="fleet-rail-item-icon">
                  {agent ? (
                    <AgentSigil seed={agent.agent_id} size={RAIL_ICON} />
                  ) : Icon ? (
                    <Icon size={RAIL_ICON} strokeWidth={1.75} />
                  ) : null}
                </span>
                {!effectiveCollapsed && <span className="fleet-rail-item-label">{link.label}</span>}
                {!effectiveCollapsed && agent && (
                  <span className="fleet-rail-space-status">
                    <StatusDot
                      tone={deriveStatus(agent.hardware_status || "unknown", Boolean(agent.stopped?.active), Boolean(agent.current_run_id)).tone}
                      size={7}
                    />
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
      ) : (
      <nav className="fleet-rail-nav">
        {railItems.map((item, idx) => {
          const Icon = item.icon;
          const active = segment === item.segment;
          const focused = focusIdx === idx;
          const isProjects = item.key === "projects";
          const count = railCountFor(item.key);
          const showProjectsSubnav = isProjects && !effectiveCollapsed && projects.length > 0;
          const expanded = projectsExpanded;
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
                  {/* Count and chord share one right-hand slot and never
                      show at once: a count means there is something to go
                      look at, which matters more in that moment than a
                      shortcut reminder. `count` is null unless it is
                      non-zero (railCountFor), so a quiet surface shows its
                      chord rather than a "0". */}
                  {!effectiveCollapsed && count ? (
                    <span className="fleet-rail-item-count">{count}</span>
                  ) : !effectiveCollapsed ? (
                    <kbd className="fleet-rail-item-chord" aria-hidden="true">G {item.chord.toUpperCase()}</kbd>
                  ) : null}
                </Link>
                {showProjectsSubnav && (
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
              {/* FLAT. Every project is one row and it has no children —
                  that asymmetry (Agents in the rail, Tasks and Documents in
                  the page) is exactly what read as broken. The active row is
                  marked so a reader deep inside a project still knows which
                  one they are in; everything ABOUT the project is one click
                  away in its own tabs. */}
              {showProjectsSubnav && projectsExpanded && (
                <div className="fleet-rail-subnav">
                  {projects.map((p) => {
                    const projectHref = `${hrefFor("projects")}/${encodeURIComponent(p.id)}`;
                    const isActiveProject = p.id === activeProjectId;
                    return (
                      <Link
                        key={p.id}
                        href={projectHref}
                        aria-current={isActiveProject ? "page" : undefined}
                        className={`fleet-rail-subitem${isActiveProject ? " fleet-rail-subitem--active" : ""}`}
                      >
                        <ProjectIcon icon={p.icon} tint={p.tint} size={18} glyphSize={11} />
                        <span className="fleet-rail-subitem-label">{p.name}</span>
                      </Link>
                    );
                  })}
                  {/* A real link, not a button that opens a dialog from the
                      rail: `?new=1` is the hand-off projects/page.tsx already
                      consumes (the command palette's own "New project" action
                      uses the identical URL), so this reuses that one composer
                      instead of standing up a second one — and being an <a>
                      means ⌘-click opens the composer in a new tab like any
                      other navigation. Neutral, never accent-filled: the
                      Projects page's own "New project" is that view's one
                      accent action, and two filled buttons in one view is a
                      bug. */}
                  <Link href={`${hrefFor("projects")}?new=1`} className="fleet-rail-subitem fleet-rail-subitem--new">
                    <span className="fleet-rail-subitem-newicon">
                      <Plus size={13} strokeWidth={2} aria-hidden="true" />
                    </span>
                    <span className="fleet-rail-subitem-label">New project</span>
                  </Link>
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
          people look at daily"). Theme lives in the account menu below;
          shortcuts are a routed Settings page the menu links to
          (settings/shortcuts, 2026-08-16). */}
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
        ownerDisplayName={ownerDisplayName}
        ownerEmailObfuscated={ownerEmailObfuscated}
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
  ownerDisplayName,
  ownerEmailObfuscated,
  ownerRole,
  collapsed,
  theme,
  onToggleTheme,
}: {
  workspaceId: string;
  ownerDisplayName?: string;
  ownerEmailObfuscated: string;
  ownerRole: string;
  collapsed: boolean;
  theme: FleetTheme;
  onToggleTheme: () => void;
}) {
  const { actions: accountShellActions } = useAccountShell();
  // 2026-08-14 — ownerEmailObfuscated is never display-ready (see
  // ssr-safe-email.ts); this is the one decode point for the rail's owner
  // row. It resolves to null until a post-hydration effect runs, so every
  // text derived from it below must have a safe non-email fallback for
  // that window — ownerRole ("Owner"/"Member"/…), never blank, never the
  // obfuscated string itself.
  const resolvedOwnerEmail = useRevealedEmail(ownerEmailObfuscated);
  const ownerNameText = ownerDisplayName || resolvedOwnerEmail || ownerRole;
  const ownerRoleLineText = resolvedOwnerEmail || ownerRole;
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
          {/* A plain link, not an accordion — the shortcut reference is a
              real routed Settings page now (settings/shortcuts, 2026-08-16),
              so this row navigates like every other row here instead of
              expanding a sub-list inside a popover. */}
          <Link
            className="fleet-rail-account-popover-row"
            href={`${settingsHref}/shortcuts`}
            role="menuitem"
            onClick={() => setOpen(false)}
          >
            <Keyboard size={14} strokeWidth={1.75} />
            Keyboard shortcuts
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
        aria-label={`Account menu — ${ownerNameText}`}
        onClick={() => { setOpen((v) => !v); setError(null); }}
      >
        <div className="fleet-rail-owner-avatar">{(ownerNameText || "O").charAt(0).toUpperCase()}</div>
        {!collapsed && (
          <div className="fleet-rail-owner-text">
            <div className="fleet-rail-owner-name">{ownerNameText}</div>
            <div className="fleet-rail-owner-role">{ownerRoleLineText}</div>
          </div>
        )}
      </button>
    </div>
  );
}
