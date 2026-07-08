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
import { ChevronRight } from "lucide-react";

import { useFleetWorkspace } from "./fleet-data";

/**
 * Breadcrumbs read the URL segment chain under /w/{ws} and render one crumb per
 * segment, each linking to its cumulative path. Static segments (Projects,
 * Agents, …) resolve from a fixed map; dynamic ids (a project id, an agent id)
 * resolve from a small registry that the routed pages populate via
 * useBreadcrumbLabel(id, name) so the chain shows real names, not raw ids.
 */

type LabelMap = Record<string, string>;
type BadgeMap = Record<string, ReactNode>;

const BreadcrumbLabelContext = createContext<{
  labels: LabelMap;
  setLabel: (key: string, label: string) => void;
  badges: BadgeMap;
  setBadge: (key: string, badge: ReactNode) => void;
}>({ labels: {}, setLabel: () => {}, badges: {}, setBadge: () => {} });

export function BreadcrumbLabelProvider({ children }: { children: ReactNode }) {
  const [labels, setLabels] = useState<LabelMap>({});
  const [badges, setBadges] = useState<BadgeMap>({});
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
  const value = useMemo(() => ({ labels, setLabel, badges, setBadge }), [labels, setLabel, badges, setBadge]);
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
  projects: "Projects",
  agents: "Agents",
  hardware: "Hardware",
  billing: "Billing",
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

type Crumb = { key: string; label: string; href: string; current: boolean; pending: boolean; badge: ReactNode };

export function Breadcrumbs({ workspaceId }: { workspaceId: string }) {
  const pathname = usePathname() || "";
  const { labels, badges } = useContext(BreadcrumbLabelContext);
  const { workspace } = useFleetWorkspace(workspaceId);

  const crumbs = useMemo<Crumb[]>(() => {
    const base = `/w/${encodeURIComponent(workspaceId)}`;
    // Everything after /w/{ws}
    const rest = pathname.startsWith(base) ? pathname.slice(base.length) : "";
    const segments = rest.split("/").filter(Boolean);

    // Root crumb: the WORKSPACE itself (its real name, not "Home" — this isn't
    // the inbox), linking to the default landing (the flat agents list). The
    // "agents/{id}" pair inside a project is a routed agent detail — the bare
    // "agents" segment there is structural, not a page, so we fold it into the
    // agent crumb rather than rendering a dead "Agents" link mid-chain.
    // The backend echoes the raw workspace id back as `name` for a workspace
    // that was never given a real one (see fleet_workspace in routes_fleet.py)
    // — treat that echo the same as "no name" rather than rendering the id.
    const hasRealName = Boolean(workspace?.name) && workspace!.name !== workspaceId;
    const items: Crumb[] = [{
      key: "workspace-root",
      label: hasRealName ? workspace!.name : "Workspace",
      href: `${base}/agents`,
      current: segments.length === 0,
      pending: false,
      badge: null,
    }];
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
      });
    });
    return items;
  }, [pathname, workspaceId, labels, badges, workspace?.name]);

  if (crumbs.length <= 1) {
    return (
      <nav className="fleet-breadcrumbs" aria-label="Breadcrumb">
        <span className="fleet-breadcrumb fleet-breadcrumb--current">{crumbs[0]?.label ?? "Workspace"}</span>
      </nav>
    );
  }

  return (
    <nav className="fleet-breadcrumbs" aria-label="Breadcrumb">
      {crumbs.map((c, i) => (
        <span key={c.key} className="fleet-breadcrumb-seg">
          {i > 0 && <ChevronRight size={13} strokeWidth={1.75} className="fleet-breadcrumb-sep" aria-hidden />}
          {c.pending ? (
            <span className={`fleet-breadcrumb${c.current ? " fleet-breadcrumb--current" : ""}`} aria-label="Loading name…">
              <span className="fleet-breadcrumb-skeleton" aria-hidden />
            </span>
          ) : c.current ? (
            <span className="fleet-breadcrumb fleet-breadcrumb--current" aria-current="page">{c.label}</span>
          ) : (
            <Link className="fleet-breadcrumb" href={c.href}>{c.label}</Link>
          )}
          {c.badge}
        </span>
      ))}
    </nav>
  );
}
