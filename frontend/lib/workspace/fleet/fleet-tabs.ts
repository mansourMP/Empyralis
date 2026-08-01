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
 * EACH TAB OWNS ITS HISTORY. A tab carries the list of urls visited in it and
 * an index into that list, which is what the ‹ / › chrome walks. This is the
 * one thing the browser's own history cannot model for us: it interleaves
 * every tab's navigations into a single line, so driving ‹ from it threw the
 * reader into a different content tab and lost their place in the tab they
 * were in. See FleetTab.history and navigateTab below.
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
   *
   * INVARIANT: always equal to `history[historyIndex]`. Every function in
   * this module that moves one moves the other; nothing else may write
   * either. `path` is kept as its own field rather than derived at every
   * read because it is what the whole component reads dozens of times per
   * render, and a mismatched pair is easier to spot than a bad index.
   */
  path: string;
  title: string;
  kind: FleetTabKind;
  /**
   * THIS TAB'S OWN history — the urls visited in it, oldest first. Not a
   * window onto the browser's global stack.
   *
   * The browser's history interleaves every tab's navigations into one
   * line, so a back button driven by it lands you in whatever tab you
   * happened to touch previously and abandons your position in the tab you
   * were actually in. That is the model a browser gives each of ITS tabs
   * too — press back in one and you move within that tab, never into
   * another one — and it is the only model that makes sense once the app
   * grows a tab strip of its own.
   */
  history: string[];
  /** Which entry of `history` is on screen. Back decrements, forward
   *  increments, a new navigation truncates everything above it. */
  historyIndex: number;
};

export type FleetTabsState = {
  tabs: FleetTab[];
  activeId: string;
};

/** Bumped only when a stored blob can no longer be read into the current
 *  model AT ALL. Deliberately NOT bumped when `path` grew to carry a query
 *  string, and deliberately NOT bumped when `history`/`historyIndex` were
 *  added: in both cases the older blob is a readable subset, and bumping
 *  throws away every reader's open tabs — the one failure this whole feature
 *  must not have. A v1 tab has no stack, so readStoredTabs seeds one from the
 *  single `path` it does have (see restoreHistory): the tab comes back exactly
 *  where it was, with ‹ correctly disabled because nothing preceded it. */
const STORAGE_VERSION = 1;

/**
 * How deep one tab's history goes before the oldest entry falls off.
 *
 * Bounded because this is persisted: an unbounded list is a localStorage
 * blob that grows for as long as a tab stays open, and the quota it would
 * eventually hit is shared with everything else the app stores. 50 is far
 * past any plausible reach-back (Chrome shows 15 in its own back menu).
 */
export const MAX_TAB_HISTORY = 50;

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
    // A fresh tab starts with exactly one entry, so ‹ and › are both
    // correctly disabled until it has actually been somewhere.
    history: [path],
    historyIndex: 0,
  };
}

export function canGoBack(tab: FleetTab | undefined): boolean {
  return Boolean(tab && tab.historyIndex > 0);
}

export function canGoForward(tab: FleetTab | undefined): boolean {
  return Boolean(tab && tab.historyIndex < tab.history.length - 1);
}

/**
 * Move this tab to `url` — the browser's own semantics, applied to one tab.
 *
 * A NEW PATHNAME pushes: everything above the current index is discarded
 * first, so going back and then somewhere new destroys the forward branch,
 * exactly as it does in every browser.
 *
 * A QUERY-ONLY CHANGE REPLACES the current entry instead. That is a filter
 * moving (the project page's status/channel/sort live in the query and are
 * written with `router.replace` — the page itself declines to make a history
 * entry for them), and pushing one would fill the stack with near-identical
 * urls: five clicks in a filter row would cost five presses of ‹ to escape,
 * and ‹ would "undo a checkbox" rather than return to the previous VIEW.
 * The tab still records the new query, because that string is what it is
 * restored from. Consequence worth naming: ‹ cannot walk back through filter
 * states — leaving a filtered project and returning brings back the last
 * filter you set, not the one before it.
 *
 * Pure: same tab in, same tab out, no ids minted — safe to call from inside
 * a setState updater, which StrictMode runs twice.
 */
export function navigateTab(tab: FleetTab, url: string): FleetTab {
  if (tab.path === url) return tab;
  if (samePath(tab.path, url)) {
    const history = tab.history.slice();
    history[tab.historyIndex] = url;
    return { ...tab, path: url, history };
  }
  const kept = tab.history.slice(0, tab.historyIndex + 1);
  kept.push(url);
  const overflow = Math.max(0, kept.length - MAX_TAB_HISTORY);
  const history = overflow > 0 ? kept.slice(overflow) : kept;
  return { ...tab, path: url, history, historyIndex: history.length - 1 };
}

/** ‹ (-1) / › (+1) within one tab. Returns null at either end, so a caller
 *  can never route somewhere the stack doesn't hold. */
export function stepTabHistory(tab: FleetTab, delta: -1 | 1): FleetTab | null {
  const next = tab.historyIndex + delta;
  if (next < 0 || next >= tab.history.length) return null;
  return { ...tab, historyIndex: next, path: tab.history[next] };
}

/** Two urls are "the same view" when their pathnames match. Query strings
 *  (a project's status/sort filters) deliberately do NOT open a second tab —
 *  they are a view's own state, not a different view. The tab still STORES
 *  the query (see FleetTab.path); it just isn't part of tab identity. */
export function samePath(a: string, b: string): boolean {
  return pathnameOf(a) === pathnameOf(b);
}

/**
 * A stored tab's stack, restored or rebuilt — the v1→v2-shape migration, done
 * without bumping STORAGE_VERSION (see that constant).
 *
 * Anything short of a stack that is INTERNALLY CONSISTENT with the tab's
 * `path` falls back to seeding a single-entry stack from that path: a v1 blob
 * (no `history` key at all), a blob whose index points off the end, one whose
 * current entry disagrees with `path`, one carrying a url from another
 * workspace, one longer than the cap we write. Seeding costs at most a tab's
 * reach-back; trusting a half-valid stack costs the ‹ button landing on a url
 * the tab was never on, which is the bug this replaces.
 */
function restoreHistory(
  rawHistory: unknown,
  rawIndex: unknown,
  path: string,
  base: string,
): { history: string[]; historyIndex: number } {
  const seeded = { history: [path], historyIndex: 0 };
  if (!Array.isArray(rawHistory) || rawHistory.length === 0) return seeded;
  if (rawHistory.length > MAX_TAB_HISTORY) return seeded;
  const history: string[] = [];
  for (const entry of rawHistory) {
    if (typeof entry !== "string" || !pathnameOf(entry).startsWith(base)) return seeded;
    history.push(entry);
  }
  if (typeof rawIndex !== "number" || !Number.isInteger(rawIndex)) return seeded;
  if (rawIndex < 0 || rawIndex >= history.length) return seeded;
  if (history[rawIndex] !== path) return seeded;
  return { history, historyIndex: rawIndex };
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
        ...restoreHistory(t.history, t.historyIndex, path, base),
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
