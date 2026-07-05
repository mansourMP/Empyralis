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
import { ChevronRight } from "lucide-react";

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

type Crumb = { key: string; label: string; href: string; current: boolean };

export function Breadcrumbs({ workspaceId }: { workspaceId: string }) {
  const pathname = usePathname() || "";
  const { labels } = useContext(BreadcrumbLabelContext);

  const crumbs = useMemo<Crumb[]>(() => {
    const base = `/w/${encodeURIComponent(workspaceId)}`;
    // Everything after /w/{ws}
    const rest = pathname.startsWith(base) ? pathname.slice(base.length) : "";
    const segments = rest.split("/").filter(Boolean);

    // The "agents/{id}" pair inside a project is a routed agent detail — the
    // bare "agents" segment there is structural, not a page, so we fold it into
    // the agent crumb rather than rendering a dead "Agents" link mid-chain.
    const items: Crumb[] = [{ key: "home", label: "Home", href: `${base}/inbox`, current: segments.length === 0 }];
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
  }, [pathname, workspaceId, labels]);

  if (crumbs.length <= 1) {
    return (
      <nav className="fleet-breadcrumbs" aria-label="Breadcrumb">
        <span className="fleet-breadcrumb fleet-breadcrumb--current">{crumbs[0]?.label ?? "Home"}</span>
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
