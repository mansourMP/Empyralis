"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { isSettingsSection } from "./settings-sections";

/**
 * Breadcrumbs read the URL segment chain under /w/{ws} and render one crumb per
 * segment, each linking to its cumulative path. Static segments (Projects,
 * Agents, …) resolve from a fixed map; dynamic ids (a project id, an agent id)
 * resolve from a small registry that the routed pages populate via
 * useBreadcrumbLabel(id, name) so the chain shows real names, not raw ids.
 */

type LabelMap = Record<string, string>;
type BadgeMap = Record<string, ReactNode>;
type IconMap = Record<string, ReactNode>;

const BreadcrumbLabelContext = createContext<{
  labels: LabelMap;
  setLabel: (key: string, label: string) => void;
  badges: BadgeMap;
  setBadge: (key: string, badge: ReactNode) => void;
  icons: IconMap;
  setIcon: (key: string, icon: ReactNode) => void;
}>({ labels: {}, setLabel: () => {}, badges: {}, setBadge: () => {}, icons: {}, setIcon: () => {} });

export function BreadcrumbLabelProvider({ children }: { children: ReactNode }) {
  const [labels, setLabels] = useState<LabelMap>({});
  const [badges, setBadges] = useState<BadgeMap>({});
  const [icons, setIcons] = useState<IconMap>({});
  const setLabel = useCallback((key: string, label: string) => {
    setLabels((prev) => (prev[key] === label ? prev : { ...prev, [key]: label }));
  }, []);
  const setBadge = useCallback((key: string, badge: ReactNode) => {
    // Bail out on a referentially-stable no-op update (mirrors setLabel's
    // value bailout above) — badges are ReactNode, not primitives, so callers
    // must memoize their badge element; this is the second layer of defense
    // against a render loop (component re-renders → new badge element →
    // setBadge → context value changes → component re-renders → ...).
    setBadges((prev) => (prev[key] === badge ? prev : { ...prev, [key]: badge }));
  }, []);
  const setIcon = useCallback((key: string, icon: ReactNode) => {
    setIcons((prev) => (prev[key] === icon ? prev : { ...prev, [key]: icon }));
  }, []);
  const value = useMemo(
    () => ({ labels, setLabel, badges, setBadge, icons, setIcon }),
    [labels, setLabel, badges, setBadge, icons, setIcon],
  );
  return (
    <BreadcrumbLabelContext.Provider value={value}>
      {children}
    </BreadcrumbLabelContext.Provider>
  );
}

/** Register a real display name for a dynamic segment (id) so breadcrumbs and
 *  any other consumer can show it instead of the raw id. */
export function useBreadcrumbLabel(
  key: string | null | undefined,
  label: string | null | undefined,
) {
  const { setLabel } = useContext(BreadcrumbLabelContext);
  useEffect(() => {
    if (key && label) setLabel(key, label);
  }, [key, label, setLabel]);
}

/** Attach a small inline badge next to a breadcrumb segment's own label —
 *  e.g. Sage's "Operator" tag next to "Sage". For a status/role marker that
 *  belongs to the destination itself, not a second header block repeating
 *  the name (the contract violation this exists to avoid). Pass `null` to
 *  clear (e.g. on unmount) — a stale registration is otherwise harmless
 *  (unused keys just never render) but explicit clearing is cheap here. */
export function useBreadcrumbBadge(
  key: string | null | undefined,
  badge: ReactNode | null,
) {
  const { setBadge } = useContext(BreadcrumbLabelContext);
  useEffect(() => {
    if (!key) return;
    setBadge(key, badge);
    return () => setBadge(key, null);
  }, [key, badge, setBadge]);
}

/** Attach a small LEADING icon before a breadcrumb segment's own label —
 *  a project's icon+tint next to its crumb, so "Projects › {name}" carries
 *  the same visual identity the project shows everywhere else. Mirrors
 *  useBreadcrumbBadge exactly, just rendered before the text instead of
 *  after — same memoization contract (pass a stable/memoized element). */
export function useBreadcrumbIcon(
  key: string | null | undefined,
  icon: ReactNode | null,
) {
  const { setIcon } = useContext(BreadcrumbLabelContext);
  useEffect(() => {
    if (!key) return;
    setIcon(key, icon);
    return () => setIcon(key, null);
  }, [key, icon, setIcon]);
}

/**
 * A page's primary action (e.g. "New agent") renders on the breadcrumb line
 * itself, top-right — not in a separate title block below. Implemented as a
 * portal rather than page-driven context state: the routed page (a
 * descendant of the provider) would otherwise re-render itself every time it
 * registered a new action element, since a fresh JSX literal is a new object
 * every render — an infinite update loop. Portaling into a slot DOM node
 * (set once, on mount) sidesteps that entirely; nothing calls setState in a
 * loop.
 */
const HeaderActionSlotContext = createContext<HTMLDivElement | null>(null);

export function HeaderActionSlotProvider({
  slotEl,
  children,
}: {
  slotEl: HTMLDivElement | null;
  children: ReactNode;
}) {
  return (
    <HeaderActionSlotContext.Provider value={slotEl}>
      {children}
    </HeaderActionSlotContext.Provider>
  );
}

/** Render a page's primary action button into the breadcrumb-line slot.
 *  Renders nothing until the slot has mounted (or if a page doesn't use it). */
export function HeaderAction({ children }: { children: ReactNode }) {
  const slotEl = useContext(HeaderActionSlotContext);
  if (!slotEl) return null;
  return createPortal(children, slotEl);
}

export const STATIC_LABELS: Record<string, string> = {
  fleet: "Home",
  inbox: "Inbox",
  // humanize() would render this segment as "My Work" — right words, wrong
  // capitalization for a product that writes sentence case everywhere else
  // (see the rail label, which is the same string). Named explicitly so the
  // two cannot disagree.
  "my-work": "My work",
  conversations: "Conversations",
  projects: "Projects",
  // Every document in the workspace, one GitHub-shaped tree (see
  // document-tree.ts). Named explicitly so it can never drift from the rail
  // label, which is the same word.
  context: "Context",
  agents: "Agents",
  // /agents/work — the workspace work ledger (WorkLedgerView.tsx), one
  // segment below Agents. Distinct from the AGENT_DETAIL_TABS "work" entry
  // just below (…/agents/{agentId}/work, a per-agent tab alias): that fold
  // check requires 3+ segments, this route has 2, so the two can never
  // collide. Named explicitly so this crumb — also this page's real <h1> —
  // never depends on humanize()'s generic capitalization by coincidence.
  work: "Work",
  hardware: "Hardware",
  billing: "Usage",
  settings: "Settings",
  // Settings' own [section] children (settings/[section]/page.tsx,
  // SettingsShell.tsx) — named here so the breadcrumb's current crumb (now
  // this page's <h1>, see the MAN-145 title-dedup note below) reads the
  // actual active section instead of falling through to humanize()'s
  // generic capitalization, which happens to produce the same words today
  // (except "shortcuts") but shouldn't be relied on by coincidence.
  account: "Account",
  workspace: "Workspace",
  connections: "Connections",
  shortcuts: "Keyboard shortcuts",
  overview: "Overview",
  chat: "Chat",
  memory: "Memory",
  channels: "Channels",
  // "Apps" is the apps/MCP picker; "Tools" is the built-in capability
  // toggles. Two different sections, so two different words — see
  // FleetAgentDetail.tsx's TABS for the founder's own wording on it.
  connectors: "Apps",
  tools: "Tools",
  model: "Model",
  // MAN-149 operator instrument (app/(account)/w/[workspaceId]/operator/
  // activation) — live, unlinked (not a PrimaryRail destination, same
  // treatment /context and /conversations already have). Named explicitly
  // so this crumb — also the page's real <h1> — never depends on
  // humanize()'s generic capitalization by coincidence.
  operator: "Operator",
  activation: "Activation",
};

function humanize(segment: string): string {
  return segment
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// Backend ids are always an opaque prefix + a hex/uuid tail (ainstall_<hex16>,
// project_<hex16>, plain uuids, …) — never real words. humanize() on one of
// these prints the raw id back at the user in Title Case ("Ainstall A1b2c3…",
// "Project 94bf1234…"), which is exactly the "raw IDs on first paint" bug: the
// owning page hasn't registered the real name yet (useBreadcrumbLabel fires
// once its own data fetch resolves). Recognize the shape and show a neutral
// loading placeholder instead of guessing English out of hex digits.
function looksLikeOpaqueId(segment: string): boolean {
  return /_[0-9a-f]{6,}$/i.test(segment) || /^[0-9a-f]{8}-?[0-9a-f-]{4,}$/i.test(segment);
}

type Crumb = { key: string; label: string; href: string; current: boolean; pending: boolean; badge: ReactNode; icon: ReactNode };

export function Breadcrumbs({ workspaceId }: { workspaceId: string }) {
  const pathname = usePathname() || "";
  const { labels, badges, icons } = useContext(BreadcrumbLabelContext);

  const crumbs = useMemo<Crumb[]>(() => {
    const base = `/w/${encodeURIComponent(workspaceId)}`;
    // Everything after /w/{ws}
    const rest = pathname.startsWith(base) ? pathname.slice(base.length) : "";
    let segments = rest.split("/").filter(Boolean);

    // "/fleet" used to be a legacy alias of the bare workspace root
    // (rendering the now-deleted FleetHome component) and is already
    // intercepted by next.config.ts's LEGACY_REDIRECTS (-> /agents) before
    // the router ever sees it, so this branch is unreachable in production
    // — kept only because a stray direct hit during local dev should still
    // fold cleanly rather than draw a dead "fleet" crumb.
    if (segments.length === 1 && segments[0] === "fleet") segments = [];

    // Agent detail's own sub-tab (…/projects/{id}/agents/{agentId}/{tab} —
    // AgentDetailPage's VALID_TABS) is a URL segment, unlike the project
    // detail page's Overview/Agents/Tasks (client-side state, never in the
    // path) — mirrored here rather than imported, since VALID_TABS lives in
    // a route page.tsx, not an importable module. MAN-145 title-dedup
    // follow-up: without folding this, the breadcrumb's current crumb (now
    // the page's <h1>, see below) would be the generic tab word
    // ("Overview") instead of the agent's own name — correct term-for-term
    // ("exactly one visible h1"), but the one heading on the page would no
    // longer say WHICH agent, which defeats the point of it being a
    // heading at all. Folded the same way "agents"/"tasks" already are
    // (dropped from the chain entirely, not just skipped as current) so
    // the agent's own crumb — one segment back — becomes the last, current
    // one instead.
    const AGENT_DETAIL_TABS = new Set([
      "overview", "work", "channels", "connectors", "tools", "capabilities", "hardware", "model", "skills", "memory", "chat",
    ]);
    const lastSeg = segments[segments.length - 1];
    const isAgentDetailTrailingTab =
      segments.length >= 3 &&
      AGENT_DETAIL_TABS.has(lastSeg) &&
      segments[segments.length - 3] === "agents";
    // …/projects/{id}/agents, …/tasks and …/documents with NOTHING after
    // them are the project detail page's own Agents/Tasks/Documents views (a
    // real route each now, not client state — see ProjectDetailPage's `view`).
    // "documents" was missing from both this list and the structural-child
    // list below until 2026-08-18, so a project's Documents tab was the one
    // of its three peers whose page <h1> renamed itself to the generic word
    // "Documents" instead of staying the project's own name.
    // Folded the same way the agent-tab case above is: the project's own
    // crumb, one segment back, is what should read as current on all three
    // of its views (Overview/Agents/Tasks), same as MAN-145 already decided
    // for the agent detail page's own sub-tabs — a customer switching
    // between them shouldn't watch the page's one real <h1> rename itself
    // to the generic word "Tasks" or "Agents" on every click.
    const isProjectViewTrailingSegment =
      segments.length >= 3 &&
      (lastSeg === "agents" || lastSeg === "tasks" || lastSeg === "documents") &&
      segments[segments.length - 3] === "projects";
    const effectiveLastIndex =
      isAgentDetailTrailingTab || isProjectViewTrailingSegment ? segments.length - 2 : segments.length - 1;

    // No synthetic workspace-root crumb — crumbs start at the section
    // (Projects, Agents, Inbox, …). The "agents/{id}" pair inside a project
    // is a routed agent detail — the bare "agents" segment there is
    // structural, not a page, so it folds into the agent crumb rather than
    // rendering a dead "Agents" link mid-chain.
    const items: Crumb[] = [];
    let acc = base;
    segments.forEach((seg, i) => {
      acc += `/${seg}`;
      const prev = segments[i - 1];
      // Skip the structural "agents"/"tasks"/"documents" segment that sits
      // between a project id and a child id (…/projects/{id}/agents/{agentId},
      // …/tasks/{taskId}, …/documents/{documentId} — none of those bare paths
      // is a distinct page, so crumbing one would draw a dead link mid-chain),
      // AND the same segment when it's the project's own Agents/Tasks/Documents
      // view and therefore trailing with nothing after it
      // (isProjectViewTrailingSegment above, which the effectiveLastIndex
      // adjustment already accounts for by making the PROJECT crumb current
      // instead).
      const isStructuralChild =
        (seg === "agents" || seg === "tasks" || seg === "documents") &&
        prev !== undefined &&
        segments[i - 2] === "projects";
      if (isStructuralChild) return;
      // The "settings" segment ahead of a real section is structural too
      // (2026-08-16): the rail's Settings space is the section picker now
      // (primary-rail-space.ts), so a "Settings" parent crumb — which also
      // rendered as the mobile "‹ Settings" back control — would be a second
      // picker door pointing at a bare redirect. The section's own crumb,
      // one segment on, is the page's current h1 instead.
      const isSettingsParent =
        seg === "settings" && i === segments.length - 2 && isSettingsSection(segments[i + 1] || "");
      if (isSettingsParent) return;
      // Drop the trailing tab segment itself (see isAgentDetailTrailingTab
      // above) — the agent's own crumb one step back becomes current.
      if (i === segments.length - 1 && isAgentDetailTrailingTab) return;
      const registered = labels[seg] || STATIC_LABELS[seg];
      const pending = !registered && looksLikeOpaqueId(seg);
      const label = registered || (pending ? "" : humanize(seg));
      items.push({
        key: `${seg}-${i}`,
        label,
        href: acc,
        current: i === effectiveLastIndex,
        pending,
        badge: badges[seg] ?? null,
        icon: icons[seg] ?? null,
      });
    });
    return items;
  }, [pathname, workspaceId, labels, badges, icons]);

  // The bare /w/{id} route (no section segment) never renders content of
  // its own any more — it redirects into /projects before anything paints
  // — so there's nothing to crumb there once the root crumb is gone.
  if (crumbs.length === 0) return null;

  // Mobile-collapsed shape: the immediate parent (one level up — a "‹ Back"
  // affordance, same idea as the Inbox mobile back button) plus the current
  // (last) crumb. A one-deep chain (a top-level section like a bare
  // "Projects" list) has no parent to go back to, so it's current-page-only.
  const current = crumbs[crumbs.length - 1];
  const parent = crumbs.length > 1 ? crumbs[crumbs.length - 2] : null;

  return (
    <nav className="fleet-breadcrumbs" aria-label="Breadcrumb">
      <span className="fleet-breadcrumb-mobile">
        {parent && (
          <Link href={parent.href} className="fleet-breadcrumb-mobile-back" aria-label={`Back to ${parent.label}`}>
            <ChevronLeft size={16} strokeWidth={2} aria-hidden />
            <span className="fleet-breadcrumb-mobile-back-label">{parent.label}</span>
          </Link>
        )}
        {/* MAN-145 title-dedup follow-up: the mobile-collapsed current-page
            label IS the page's <h1> now — see the desktop chain's matching
            comment below for the full rationale. No font-size fix needed
            here (unlike the desktop h1 below): the 768px media query already
            sets `.fleet-breadcrumb-mobile-current` to an explicit 15px, which
            — being an author rule — already beats the UA h1 default with no
            help from this component. */}
        {current.pending ? (
          <h1 className="fleet-breadcrumb-mobile-current" aria-label="Loading name…">
            <span className="fleet-breadcrumb-skeleton" aria-hidden />
          </h1>
        ) : (
          <h1 className="fleet-breadcrumb-mobile-current" aria-current="page">
            {current.icon}{current.label}
          </h1>
        )}
        {current.badge}
      </span>

      {crumbs.map((c, i) => (
        <span key={c.key} className="fleet-breadcrumb-seg">
          {i > 0 && <ChevronRight size={13} strokeWidth={1.75} className="fleet-breadcrumb-sep" aria-hidden />}
          {c.current ? (
            // MAN-145 title-dedup follow-up: every routed page used to render
            // its own name three times — the tab strip, this breadcrumb's own
            // current-page crumb, AND a separate `.fleet-header`/<h1> block
            // below it (added for real heading structure, but nobody accounted
            // for the breadcrumb already BEING the page title). The breadcrumb
            // is the better survivor: it already carries context a bare title
            // never did (the "· 3"/"· 10 agents" badge, a project's own icon
            // via useBreadcrumbIcon) and it's genuinely the page's identity,
            // not chrome bolted next to it. So the LAST crumb — and only the
            // last one; "Projects" in "Projects › General" stays a plain link
            // crumb — IS the page's one real <h1> now, and every routed page's
            // own `.fleet-header`/<h1> block is gone (see each page.tsx).
            //
            // `style={{ fontSize: "inherit" }}`: nothing here (`.fleet-breadcrumb`
            // itself has no font-size of its own — it inherits `.fleet-breadcrumbs`'
            // var(--text-sm)) — inheritance is CSS's fallback ONLY when no rule
            // for that property targets the element itself, and the browser's
            // own UA stylesheet DOES supply an explicit h1 font-size (scaled
            // for nesting inside a <nav>) that would otherwise win over
            // inheriting from an ancestor. This one inline declaration
            // restores the exact pixel size the old plain <span> always had —
            // nothing else about `.fleet-breadcrumb`'s look changes.
            c.pending ? (
              <h1 className="fleet-breadcrumb fleet-breadcrumb--current" style={{ fontSize: "inherit" }} aria-label="Loading name…">
                <span className="fleet-breadcrumb-skeleton" aria-hidden />
              </h1>
            ) : (
              <h1 className="fleet-breadcrumb fleet-breadcrumb--current" style={{ fontSize: "inherit" }} aria-current="page">
                {c.icon}{c.label}
              </h1>
            )
          ) : c.pending ? (
            <span className="fleet-breadcrumb" aria-label="Loading name…">
              <span className="fleet-breadcrumb-skeleton" aria-hidden />
            </span>
          ) : (
            <Link className="fleet-breadcrumb" href={c.href}>{c.icon}{c.label}</Link>
          )}
          {c.badge}
        </span>
      ))}
    </nav>
  );
}
