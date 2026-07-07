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

const BreadcrumbLabelContext = createContext<{
  labels: LabelMap;
  setLabel: (key: string, label: string) => void;
}>({ labels: {}, setLabel: () => {} });

export function BreadcrumbLabelProvider({ children }: { children: ReactNode }) {
  const [labels, setLabels] = useState<LabelMap>({});
  const setLabel = useCallback((key: string, label: string) => {
    setLabels((prev) => (prev[key] === label ? prev : { ...prev, [key]: label }));
  }, []);
  const value = useMemo(() => ({ labels, setLabel }), [labels, setLabel]);
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
  sage: "Sage",
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

type Crumb = { key: string; label: string; href: string; current: boolean };

export function Breadcrumbs({ workspaceId }: { workspaceId: string }) {
  const pathname = usePathname() || "";
  const { labels } = useContext(BreadcrumbLabelContext);
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
      const label = labels[seg] || STATIC_LABELS[seg] || humanize(seg);
      items.push({
        key: `${seg}-${i}`,
        label,
        href: acc,
        current: i === segments.length - 1,
      });
    });
    return items;
  }, [pathname, workspaceId, labels, workspace?.name]);

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
          {c.current ? (
            <span className="fleet-breadcrumb fleet-breadcrumb--current" aria-current="page">{c.label}</span>
          ) : (
            <Link className="fleet-breadcrumb" href={c.href}>{c.label}</Link>
          )}
        </span>
      ))}
    </nav>
  );
}
