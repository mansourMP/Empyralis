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

const STATIC_LABELS: Record<string, string> = {
  fleet: "Home",
  inbox: "Inbox",
  conversations: "Conversations",
  projects: "Projects",
  agents: "Agents",
  hardware: "Hardware",
  billing: "Usage",
  settings: "Settings",
  overview: "Overview",
  chat: "Chat",
  memory: "Memory",
  channels: "Channels",
  connectors: "Connectors",
  tools: "Tools",
  model: "Model",
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
    const segments = rest.split("/").filter(Boolean);

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
      // Skip the structural "agents" segment that sits between a project id and
      // an agent id (…/projects/{id}/agents/{agentId}); its own path 404s.
      const isStructuralAgents =
        seg === "agents" && prev !== undefined && segments[i - 2] === "projects";
      if (isStructuralAgents) return;
      const registered = labels[seg] || STATIC_LABELS[seg];
      const pending = !registered && looksLikeOpaqueId(seg);
      const label = registered || (pending ? "" : humanize(seg));
      items.push({
        key: `${seg}-${i}`,
        label,
        href: acc,
        current: i === segments.length - 1,
        pending,
        badge: badges[seg] ?? null,
        icon: icons[seg] ?? null,
      });
    });
    return items;
  }, [pathname, workspaceId, labels, badges, icons]);

  // The workspace landing page (bare /w/{id}, no section segment) has its own
  // page heading (FleetHome's "Your fleet") — nothing to crumb there once the
  // root crumb is gone.
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
            {parent.label}
          </Link>
        )}
        {current.pending ? (
          <span className="fleet-breadcrumb-mobile-current" aria-label="Loading name…">
            <span className="fleet-breadcrumb-skeleton" aria-hidden />
          </span>
        ) : (
          <span className="fleet-breadcrumb-mobile-current" aria-current="page">
            {current.icon}{current.label}
          </span>
        )}
        {current.badge}
      </span>

      {crumbs.map((c, i) => (
        <span key={c.key} className="fleet-breadcrumb-seg">
          {i > 0 && <ChevronRight size={13} strokeWidth={1.75} className="fleet-breadcrumb-sep" aria-hidden />}
          {c.pending ? (
            <span className={`fleet-breadcrumb${c.current ? " fleet-breadcrumb--current" : ""}`} aria-label="Loading name…">
              <span className="fleet-breadcrumb-skeleton" aria-hidden />
            </span>
          ) : c.current ? (
            <span className="fleet-breadcrumb fleet-breadcrumb--current" aria-current="page">
              {c.icon}{c.label}
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
