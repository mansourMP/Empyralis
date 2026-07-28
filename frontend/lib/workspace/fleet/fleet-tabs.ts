/**
 * The tab model for the MAIN CONTENT AREA — Safari/VS-Code style, scoped to
 * the content panel only. The primary rail is deliberately NOT part of this:
 * it is the app's spine and stays fixed while tabs swap what is inside the
 * panel to its right.
 *
 * A TAB IS A VIEW ONTO A ROUTE, NOT A REPLACEMENT FOR ROUTING. Every tab
 * holds a real in-app pathname; the URL bar always tells the truth, deep
 * links work, and browser back/forward keep working because navigation still
 * goes through the Next router. The tab list is a second, persisted index of
 * "views I have open" layered on top of that — never a substitute for it.
 *
 * Pure module on purpose (no React): the path→title/kind derivation and the
 * localStorage round-trip are the two things worth being able to reason about
 * (and reuse) without a component tree.
 */

import { STATIC_LABELS } from "./Breadcrumbs";
import { taskShortId } from "./task-status";

export type FleetTabKind =
  | "home"
  | "inbox"
  | "conversations"
  | "projects"
  | "project"
  | "agents"
  | "agent"
  | "task"
  | "hardware"
  | "billing"
  | "settings"
  | "other";

export type FleetTab = {
  /** Stable across reloads; only ever generated here. */
  id: string;
  /**
   * An in-app URL under /w/{workspaceId} — a pathname PLUS its query string
   * when the view has one. Never an absolute URL.
   *
   * The query is part of what a tab remembers on purpose: a project's
   * status/channel/sort filters live there (see the project page's
   * updateFilters, which writes them with router.replace), so a tab that
   * stored only the pathname came back unfiltered after a reload. IDENTITY
   * is still the pathname alone — see samePath — so changing a filter
   * updates the tab you are in rather than opening a second one.
   */
  path: string;
  title: string;
  kind: FleetTabKind;
};

export type FleetTabsState = {
  tabs: FleetTab[];
  activeId: string;
};

/** Bumped if the persisted shape ever changes — a stale v1 blob is then
 *  ignored rather than half-read into a newer model. Deliberately NOT bumped
 *  when `path` grew to carry a query string: a v1 blob's pathname-only tabs
 *  are a valid subset of the newer shape, and bumping would have thrown away
 *  every reader's open tabs to fix a filter that only some of them use. */
const STORAGE_VERSION = 1;

export function tabsStorageKey(workspaceId: string): string {
  return `fleet:tabs:v${STORAGE_VERSION}:${workspaceId}`;
}

let idCounter = 0;
export function newTabId(): string {
  idCounter += 1;
  return `tab_${Date.now().toString(36)}_${idCounter.toString(36)}`;
}

export function workspaceBase(workspaceId: string): string {
  return `/w/${encodeURIComponent(workspaceId)}`;
}

/**
 * Where a brand-new tab lands, and where closing the last tab lands.
 *
 * Projects, NOT the workspace root: `/w/:workspaceId` is a 307 in
 * next.config.ts (→ /agents), and pushing a redirecting URL costs a server
 * round trip that can tear the client tree down mid-navigation — which is
 * exactly what swallowed the quick-switcher the first version of this opened
 * on a new tab. Projects is a real, immediate page and is the natural root of
 * the thing tabs are mostly used on (projects and their tasks).
 */
export function defaultTabPath(workspaceId: string): string {
  return `${workspaceBase(workspaceId)}/projects`;
}

/** The pathname half of a tab url — what the tab IS, stripped of the view
 *  state (`?status=done&sort=name`) and of any fragment. Everything that
 *  answers "which view is this" goes through here; everything that answers
 *  "what exactly should the router show" uses the full url. */
export function pathnameOf(url: string): string {
  const cut = url.search(/[?#]/);
  return cut < 0 ? url : url.slice(0, cut);
}

function humanize(segment: string): string {
  return segment.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export type PathDescription = {
  kind: FleetTabKind;
  title: string;
  /** True when the title came from a real name (a static section label or a
   *  registered id→name), false when it is a placeholder derived from the
   *  raw path. Only a RESOLVED title is allowed to overwrite a title a caller
   *  supplied explicitly — otherwise ⌘-clicking a task card would replace the
   *  task's real title with its six-character short id. */
  resolved: boolean;
};

/**
 * What does this url show, and what should its tab be called?
 * `labels` is the breadcrumb registry (id → real name), so a tab and its
 * breadcrumb can never disagree about what a project or agent is called.
 *
 * Takes a full tab url (query and all) and describes its PATHNAME: a filter
 * changes what a view lists, never what the view is called.
 */
export function describePath(
  url: string,
  workspaceId: string,
  labels: Record<string, string> = {},
): PathDescription {
  const base = workspaceBase(workspaceId);
  const pathname = pathnameOf(url);
  const rest = pathname.startsWith(base) ? pathname.slice(base.length) : "";
  const segments = rest.split("/").filter(Boolean).map((s) => decodeURIComponent(s));

  if (segments.length === 0) return { kind: "home", title: "Home", resolved: true };

  const [first, second, third, fourth] = segments;

  if (first === "projects") {
    if (!second) return { kind: "projects", title: "Projects", resolved: true };
    if (third === "tasks" && fourth) {
      const named = labels[fourth];
      return { kind: "task", title: named || taskShortId(fourth), resolved: Boolean(named) };
    }
    if (third === "agents" && fourth) {
      const named = labels[fourth];
      return { kind: "agent", title: named || "Agent", resolved: Boolean(named) };
    }
    const named = labels[second];
    return { kind: "project", title: named || "Project", resolved: Boolean(named) };
  }

  if (first === "agents") {
    if (!second) return { kind: "agents", title: "Agents", resolved: true };
    const named = labels[second];
    return { kind: "agent", title: named || "Agent", resolved: Boolean(named) };
  }

  if (first === "hardware" && second) {
    const named = labels[second];
    return { kind: "hardware", title: named || "Gateway", resolved: Boolean(named) };
  }

  const staticLabel = STATIC_LABELS[first];
  if (staticLabel) {
    const kind = (["inbox", "conversations", "hardware", "billing", "settings"] as const).find(
      (k) => k === first,
    );
    return { kind: kind || "other", title: staticLabel, resolved: true };
  }

  return { kind: "other", title: humanize(segments[segments.length - 1] || "Page"), resolved: false };
}

export function makeTab(
  path: string,
  workspaceId: string,
  labels: Record<string, string> = {},
  explicitTitle?: string,
): FleetTab {
  const described = describePath(path, workspaceId, labels);
  return {
    id: newTabId(),
    path,
    title: (explicitTitle || "").trim() || described.title,
    kind: described.kind,
  };
}

/** Two urls are "the same view" when their pathnames match. Query strings
 *  (a project's status/sort filters) deliberately do NOT open a second tab —
 *  they are a view's own state, not a different view. The tab still STORES
 *  the query (see FleetTab.path); it just isn't part of tab identity. */
export function samePath(a: string, b: string): boolean {
  return pathnameOf(a) === pathnameOf(b);
}

export function readStoredTabs(workspaceId: string): FleetTabsState | null {
  try {
    const raw = window.localStorage.getItem(tabsStorageKey(workspaceId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    const tabs = Array.isArray(parsed?.tabs) ? parsed.tabs : [];
    const clean: FleetTab[] = [];
    const base = workspaceBase(workspaceId);
    for (const t of tabs) {
      if (!t || typeof t !== "object") continue;
      const path = String(t.path || "");
      // A tab belonging to another workspace (or an outright junk value) is
      // dropped rather than restored — restoring it would navigate a reader
      // into a workspace they may no longer be in. Checked on the PATHNAME so
      // a query string can never smuggle the base past this test.
      if (!pathnameOf(path).startsWith(base)) continue;
      clean.push({
        id: String(t.id || newTabId()),
        path,
        title: String(t.title || "Untitled"),
        kind: (String(t.kind || "other") as FleetTabKind),
      });
    }
    if (clean.length === 0) return null;
    const activeId = clean.some((t) => t.id === parsed?.activeId) ? String(parsed.activeId) : clean[0].id;
    return { tabs: clean, activeId };
  } catch {
    return null;
  }
}

export function writeStoredTabs(workspaceId: string, state: FleetTabsState): void {
  try {
    window.localStorage.setItem(tabsStorageKey(workspaceId), JSON.stringify(state));
  } catch {
    /* localStorage unavailable (private mode, quota) — tabs stay in-memory */
  }
}
