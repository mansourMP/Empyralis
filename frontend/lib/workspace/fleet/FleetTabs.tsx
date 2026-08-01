"use client";

/**
 * The main-content tab strip — Safari/VS-Code semantics, Linear's chrome.
 *
 * WHAT IS AND IS NOT TABBED. Only the content panel is. The primary rail, the
 * command palette, the Sage console and every overlay stay exactly where they
 * were; a tab swaps what is inside the panel and nothing else. That is why the
 * strip lives in `.fleet-shell-column` — the main-content column, to the right
 * of the rail — rather than across the top of the window. It sits ABOVE the
 * content panel and outside it (MAN-127), so the panel is purely page.
 *
 * ROUTING IS STILL THE TRUTH. Each tab holds an in-app url — pathname AND
 * query string, so a filtered project comes back filtered — and switching
 * tabs is a real `router.push`. The URL bar is never a lie, deep links land
 * correctly, and browser back/forward keep working — a back navigation just
 * re-enters this reconciler from the other direction (see `syncFromPathname`).
 * The tab list is a persisted index of open views layered ON TOP of routing,
 * never a substitute for it.
 *
 * ACTIVATION SEMANTICS (browsers', deliberately):
 *   · plain click on a card/link  → REPLACES the active tab's content
 *   · ⌘/Ctrl+click, middle click  → opens a NEW BACKGROUND tab, no navigation
 *   · `+`                         → a new tab landed on Projects (this
 *                                   workspace's default view — see
 *                                   defaultTabPath in fleet-tabs.ts). Used to
 *                                   pop the ⌘K quick-switcher open over a
 *                                   blank landing page; founder feedback
 *                                   during YC-demo prep was that a full-
 *                                   workspace command palette (every agent
 *                                   listed) is not a light "type where you
 *                                   want to go" affordance, it's a modal —
 *                                   the wrong opening move for a plain `+`
 *                                   click. ⌘K itself is untouched and still
 *                                   opens the palette from anywhere.
 *
 * The modifier-click interception is ONE capture-phase listener scoped to the
 * content panel, not a prop threaded through every clickable thing. It picks
 * up two kinds of target: any in-workspace `<a href>`, and any element
 * carrying `data-tab-href` (the board cards and task rows, which are buttons
 * rather than links because they are also drag sources). Adding the attribute
 * is the entire cost of making a new surface ⌘-clickable.
 *
 * KEYBOARD (tab switching itself). Deliberately none. ⌘W and ⌘1..9 are owned
 * by the browser itself (Chrome/Safari never deliver them to the page), and
 * every unmodified letter is already spoken for by PrimaryRail's `g`-chord/
 * j/k model. Claiming a half-working shortcut would be worse than not
 * claiming one — see the report note; a rebindable scheme is real follow-up
 * work. (The history buttons rendered alongside the strip, below, are a
 * separate control with their own ⌘[ / ⌘] binding — see
 * FleetHistoryControls.)
 */

import {
  createContext,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  BarChart3,
  Bot,
  ChevronLeft,
  ChevronRight,
  CircleDashed,
  Cpu,
  FolderKanban,
  Home,
  Inbox,
  MessagesSquare,
  Plus,
  Settings,
  Square,
  X,
  type LucideIcon,
} from "lucide-react";

import { useBreadcrumbLabels } from "./Breadcrumbs";
import {
  canGoBack as tabCanGoBack,
  canGoForward as tabCanGoForward,
  defaultTabPath,
  describePath,
  makeTab,
  navigateTab,
  pathnameOf,
  readStoredTabs,
  samePath,
  stepTabHistory,
  workspaceBase,
  writeStoredTabs,
  type FleetTab,
  type FleetTabKind,
  type FleetTabsState,
} from "./fleet-tabs";
import "./fleet-nav-history.css";

type OpenOptions = { title?: string };

type TabsApi = {
  tabs: FleetTab[];
  activeId: string;
  /** Background: create the tab but stay where you are. ⌘-click's behaviour.
   *  Exposed (rather than kept private to the click interceptor) for the
   *  surfaces that can't opt in via `data-tab-href` because they build their
   *  destination in a handler — e.g. the agents list, whose rows resolve a
   *  project id before they know their own route. */
  openInNewTab: (path: string, options?: OpenOptions) => void;
  activateTab: (id: string) => void;
  closeTab: (id: string) => void;
  newTab: () => void;
  /** ‹ / › — the ACTIVE tab's own history, never the browser's global one.
   *  Exposed as state, not just handlers, so the buttons can be genuinely
   *  disabled at either end (CLAUDE.md: no dead controls). */
  canGoBack: boolean;
  canGoForward: boolean;
  goBack: () => void;
  goForward: () => void;
};

const TabsContext = createContext<TabsApi | null>(null);

/** Returns null outside the provider, so a component that might render on a
 *  non-tabbed surface can degrade to plain routing rather than crash. */
export function useFleetTabs(): TabsApi | null {
  return useContext(TabsContext);
}

const KIND_ICONS: Record<FleetTabKind, LucideIcon> = {
  home: Home,
  inbox: Inbox,
  conversations: MessagesSquare,
  projects: FolderKanban,
  project: FolderKanban,
  agents: Bot,
  agent: Bot,
  task: CircleDashed,
  hardware: Cpu,
  billing: BarChart3,
  settings: Settings,
  other: Square,
};

/**
 * Reactive access to the query string, without dragging the whole shell into
 * a Suspense boundary.
 *
 * `useSearchParams` is the ONLY thing that re-renders when a page rewrites
 * its own query in place — which is exactly how the project page stores its
 * status/channel/sort filters (`router.replace` from updateFilters, same
 * pathname, new query). `usePathname` never fires for that, and reading
 * `window.location.search` by hand — what the pages themselves do — is not
 * reactive at all.
 *
 * The catch is that calling it during a prerender makes the calling tree bail
 * out to its nearest Suspense boundary, and this provider wraps every route
 * in the fleet shell. So it is called HERE, in a component that renders
 * nothing, under a boundary that owns nothing: the bailout can only ever cost
 * a null.
 */
function SearchParamsBridge({ onChange }: { onChange: (search: string) => void }) {
  const search = useSearchParams().toString();
  useEffect(() => {
    onChange(search);
  }, [search, onChange]);
  return null;
}

export function FleetTabsProvider({
  workspaceId,
  children,
}: {
  workspaceId: string;
  children: ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname() || "";
  const labels = useBreadcrumbLabels();
  const base = workspaceBase(workspaceId);

  // Seeded from the address bar rather than left empty until the bridge below
  // reports in, so a deep link that carries filters (`?status=done`) is
  // already whole on the very first pass through the restore effect. Nothing
  // derived from it reaches the DOM — tab titles describe the pathname only —
  // so the server's "" and the client's real query can't disagree visibly.
  const [search, setSearch] = useState<string>(() =>
    typeof window === "undefined" ? "" : window.location.search.replace(/^\?/, ""),
  );
  /** What the address bar says, in full. The unit every tab stores and every
   *  navigation decision below compares against. */
  const url = search ? `${pathname}?${search}` : pathname;

  // First render is derived PURELY from the url, so the server and the
  // client agree — localStorage is merged in an effect right after (see
  // below). The alternative (render nothing until hydrated) flashes an empty
  // strip on every cold load.
  const [state, setState] = useState<FleetTabsState>(() => {
    const tab = makeTab(pathname.startsWith(base) ? url : defaultTabPath(workspaceId), workspaceId);
    return { tabs: [tab], activeId: tab.id };
  });
  // Which workspace's stored tabs have actually been read back into `state`.
  // STATE, not a ref, and set through setState rather than assigned inside the
  // restore effect — this is what stops the persist effect below from writing
  // the pre-restore placeholder over a real saved session. A ref assigned
  // during the restore effect is already true when the persist effect runs in
  // the SAME pass (its closure still holds the placeholder state), so it wrote
  // one tab over the saved set; StrictMode then re-ran the restore against
  // that clobbered value and the extra tabs were gone. Losing a reader's open
  // tabs on reload is exactly the failure this whole feature must not have.
  const [restoredFor, setRestoredFor] = useState<string | null>(null);

  // ── Restore ───────────────────────────────────────────────────────────────
  // The URL wins over the stored active tab: a reader who deep-linked (or
  // reloaded on a page they navigated to) must land on what the address bar
  // says, exactly like a browser session restore does. It wins over the
  // stored QUERY too — the landed-on page reads its filters straight out of
  // window.location, so letting a stale stored query survive would leave the
  // tab claiming a filter the page underneath it isn't applying. Every OTHER
  // tab keeps the query it was stored with, which is the whole fix: a
  // filtered project tab you weren't looking at when you reloaded comes back
  // filtered.
  useEffect(() => {
    const stored = readStoredTabs(workspaceId);
    setRestoredFor(workspaceId);
    if (!stored) return;
    setState((cur) => {
      const match = stored.tabs.find((t) => samePath(t.path, url));
      if (match) {
        // Same view, so navigateTab REPLACES that tab's current entry with
        // the address bar's query rather than pushing — the reader reloaded
        // on a page they were already on, they did not navigate anywhere.
        // Everything behind it in that tab's stack survives the reload.
        const tabs = stored.tabs.map((t) => (t.id === match.id ? navigateTab(t, url) : t));
        return { tabs, activeId: match.id };
      }
      const idx = Math.max(0, stored.tabs.findIndex((t) => t.id === stored.activeId));
      const tabs = stored.tabs.slice();
      const described = describePath(url, workspaceId, labels);
      // A different view (a deep link, or a link followed in from outside):
      // the stored active tab is repurposed, so this IS a navigation in it
      // and navigateTab pushes. ‹ then returns to what that tab was holding
      // before the reload, which is what a browser session restore does too.
      tabs[idx] = { ...navigateTab(tabs[idx], url), title: described.title, kind: described.kind };
      return { tabs, activeId: tabs[idx].id };
    });
    // Restore runs once per workspace, on mount — `url`/`labels` are read
    // as of that moment on purpose, and the navigation effect below owns every
    // change after it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  // ── Navigation → tabs ─────────────────────────────────────────────────────
  // The single reconciler, and the ONLY place a tab's history grows. Every way
  // a pathname can change funnels through here: a plain click, a tab click,
  // browser back/forward, a redirect, a programmatic push, and the ‹/› buttons
  // below. If some tab already shows the new path it becomes active;
  // otherwise the ACTIVE tab's content is replaced — which is what "a plain
  // click replaces the current tab" means, and also the right behaviour for a
  // back navigation to a view that no tab is holding.
  //
  // A ‹/› step needs no special case here: it has already moved the active
  // tab's index and path in state before routing, so by the time this runs the
  // active tab matches `url` EXACTLY and the first branch returns `cur`
  // untouched. That is what stops the buttons from pushing the entry they just
  // stepped onto back on top of the stack.
  useEffect(() => {
    if (!pathname.startsWith(base)) return;
    setState((cur) => {
      // The active tab gets first refusal on the match. Two tabs CAN hold the
      // same pathname (a ‹ step, or `+`, can land one tab on a view another
      // already shows), and without this the reconciler would hand the
      // navigation to whichever sits earlier in the strip — yanking the reader
      // into a different tab, which is the whole bug being fixed.
      const active = cur.tabs.find((t) => t.id === cur.activeId);
      const match =
        active && samePath(active.path, url) ? active : cur.tabs.find((t) => samePath(t.path, url));
      if (match) {
        // Same view. If only the query moved (a filter changed), the tab
        // records the new one instead of opening a second tab for it — the
        // tab must keep agreeing with the address bar, because that string is
        // what it will be restored from. navigateTab replaces that tab's
        // current history entry rather than pushing (see its comment).
        if (match.path !== url) {
          const tabs = cur.tabs.map((t) => (t.id === match.id ? navigateTab(t, url) : t));
          return { tabs, activeId: match.id };
        }
        return match.id === cur.activeId ? cur : { ...cur, activeId: match.id };
      }
      const described = describePath(url, workspaceId, labels);
      const idx = cur.tabs.findIndex((t) => t.id === cur.activeId);
      if (idx < 0) {
        const tab = makeTab(url, workspaceId, labels);
        return { tabs: [...cur.tabs, tab], activeId: tab.id };
      }
      const tabs = cur.tabs.slice();
      // A real navigation inside this tab: pushes, truncating any forward
      // entries first, so going back and then somewhere new destroys the
      // forward branch exactly as a browser does.
      tabs[idx] = { ...navigateTab(tabs[idx], url), title: described.title, kind: described.kind };
      return { ...cur, tabs };
    });
    // `labels` is intentionally not a dependency: it only ever IMPROVES a
    // title, which the effect below handles on its own schedule. Including it
    // here would re-run the whole reconciler every time a page registers a
    // name.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, pathname, base, workspaceId]);

  // ── Real names, once they resolve ─────────────────────────────────────────
  // A tab opened before its page has fetched anything is titled "Project" or
  // a task's short id. The moment the breadcrumb registry learns the real
  // name, every tab pointing at that thing picks it up. Only a RESOLVED name
  // may overwrite an existing title, so a title passed explicitly at open
  // time (a ⌘-clicked card knows its own task title) is never downgraded.
  useEffect(() => {
    setState((cur) => {
      let changed = false;
      const tabs = cur.tabs.map((t) => {
        const described = describePath(t.path, workspaceId, labels);
        if (!described.resolved || described.title === t.title) return t;
        changed = true;
        return { ...t, title: described.title, kind: described.kind };
      });
      return changed ? { ...cur, tabs } : cur;
    });
  }, [labels, workspaceId]);

  // ── Persist ───────────────────────────────────────────────────────────────
  // Keyed on the workspace the current state was restored FOR: switching
  // workspaces without a remount would otherwise write the old workspace's
  // tabs under the new workspace's key for one render.
  useEffect(() => {
    if (restoredFor !== workspaceId) return;
    writeStoredTabs(workspaceId, state);
  }, [state, workspaceId, restoredFor]);

  const openInNewTab = useCallback(
    (path: string, options?: OpenOptions) => {
      if (!pathnameOf(path).startsWith(base)) return;
      // Built outside the updater: makeTab mints an id, and an updater has to
      // stay a pure function of the previous state (StrictMode runs it twice).
      const tab = makeTab(path, workspaceId, labels, options?.title);
      setState((cur) =>
        // Already open → don't stack a duplicate. The tab the reader wanted
        // is visibly there either way, which is the point of the gesture.
        cur.tabs.some((t) => samePath(t.path, path)) ? cur : { ...cur, tabs: [...cur.tabs, tab] },
      );
    },
    [base, workspaceId, labels],
  );

  const activateTab = useCallback(
    (id: string) => {
      const tab = state.tabs.find((t) => t.id === id);
      if (!tab) return;
      setState((cur) => ({ ...cur, activeId: id }));
      // Exact-url comparison, not samePath: switching to a tab whose query
      // differs from the address bar's still has to navigate, or the filters
      // the tab remembers would never actually be applied.
      if (tab.path !== url) router.push(tab.path);
    },
    [state.tabs, url, router],
  );

  // Computed from `state` rather than inside a setState updater on purpose:
  // an updater must be a pure function of the previous state. React invokes
  // it during render (and twice under StrictMode, which this app enables), so
  // deciding "and now navigate here" inside one is a side effect that fires an
  // unpredictable number of times — or, when React defers the updater, zero
  // times before the line that reads the result. That last case was a real
  // bug: closing the final tab swapped the strip to the default view and left
  // the URL sitting on the closed tab's route.
  const closeTab = useCallback(
    (id: string) => {
      const idx = state.tabs.findIndex((t) => t.id === id);
      if (idx < 0) return;
      const tabs = state.tabs.filter((t) => t.id !== id);

      // At least one tab always exists: closing the last one lands on the
      // default view rather than leaving an empty panel.
      if (tabs.length === 0) {
        const fallback = makeTab(defaultTabPath(workspaceId), workspaceId, labels);
        setState({ tabs: [fallback], activeId: fallback.id });
        if (fallback.path !== url) router.push(fallback.path);
        return;
      }

      // Closing a background tab leaves the current view alone.
      if (state.activeId !== id) {
        setState({ tabs, activeId: state.activeId });
        return;
      }

      // Browsers activate the tab to the RIGHT of the one you closed (the
      // last tab falls back to its left neighbour).
      const next = tabs[Math.min(idx, tabs.length - 1)];
      setState({ tabs, activeId: next.id });
      if (next.path !== url) router.push(next.path);
    },
    [state, workspaceId, labels, url, router],
  );

  const newTab = useCallback(() => {
    // Lands on Projects (defaultTabPath) and stays there — a real, useful
    // view, not a blank page. This used to also pop the ⌘K command palette
    // open over that landing page; removed (YC-demo founder feedback) — the
    // palette lists every agent in the workspace, a full-screen modal is
    // not a light "new tab, type where you want to go" affordance. ⌘K
    // itself is unchanged and still opens the palette from anywhere.
    const path = defaultTabPath(workspaceId);
    const tab = makeTab(path, workspaceId, labels);
    setState((cur) => ({ tabs: [...cur.tabs, tab], activeId: tab.id }));
    router.push(path);
  }, [workspaceId, labels, router]);

  // ── ‹ / › — the active tab's own history ──────────────────────────────────
  // Computed OUTSIDE the updater, like closeTab above and for the same reason:
  // "and now navigate here" is a side effect, and React runs an updater an
  // unpredictable number of times (twice under StrictMode, which this app
  // enables).
  //
  // The step lands in state FIRST and routes second. That ordering is what
  // makes the reconciler a no-op for this navigation — it finds the active tab
  // already sitting on `url` — so a ‹ can never re-push the entry it just
  // stepped back onto, and a › can never truncate the branch it is walking.
  //
  // router.push, not router.replace: the address bar must say what is on
  // screen, and every view the reader actually looks at should stay reachable
  // by the BROWSER's own back button in the order they looked at it. The two
  // controls answer different questions — the browser's retraces this session
  // across all tabs, ‹ retraces THIS tab — and neither is a lie about the
  // other, which is precisely what the old shared-pointer model could not do.
  //
  // The title/kind are re-derived here rather than left to the reconciler,
  // BECAUSE that no-op is so complete: a step lands on a genuinely different
  // view, and the effect that would normally relabel the tab sees nothing to
  // do. Skipping this left the strip showing "Conversations" over /inbox after
  // a ‹ — right page, wrong label and wrong icon. Applied unconditionally, the
  // same way the reconciler's own new-view branch does it; a name that only
  // the breadcrumb registry knows (a task, a project) is still upgraded a
  // moment later by the resolve effect above.
  const stepHistory = useCallback(
    (delta: -1 | 1) => {
      const tab = state.tabs.find((t) => t.id === state.activeId);
      if (!tab) return;
      const stepped = stepTabHistory(tab, delta);
      if (!stepped) return;
      const described = describePath(stepped.path, workspaceId, labels);
      const moved = { ...stepped, title: described.title, kind: described.kind };
      setState((cur) => ({ ...cur, tabs: cur.tabs.map((t) => (t.id === moved.id ? moved : t)) }));
      if (moved.path !== url) router.push(moved.path);
    },
    [state, url, router, workspaceId, labels],
  );

  const goBack = useCallback(() => stepHistory(-1), [stepHistory]);
  const goForward = useCallback(() => stepHistory(1), [stepHistory]);

  const activeTab = state.tabs.find((t) => t.id === state.activeId);
  const canGoBack = tabCanGoBack(activeTab);
  const canGoForward = tabCanGoForward(activeTab);

  // ── ⌘/Ctrl+click and middle-click → background tab ────────────────────────
  useEffect(() => {
    const resolveHref = (target: EventTarget | null): { path: string; title?: string } | null => {
      const el = target instanceof Element ? target : null;
      if (!el) return null;
      // Scoped to the content panel: the rail keeps native browser behaviour
      // (⌘-clicking a project there still opens a real browser tab), which is
      // the one place a reader plausibly wants the OS-level gesture.
      if (!el.closest(".fleet-shell-main")) return null;
      const carrier = el.closest<HTMLElement>("[data-tab-href]");
      if (carrier) {
        const path = carrier.getAttribute("data-tab-href") || "";
        if (!pathnameOf(path).startsWith(base)) return null;
        return { path, title: carrier.getAttribute("data-tab-title") || undefined };
      }
      const anchor = el.closest<HTMLAnchorElement>("a[href]");
      if (!anchor) return null;
      const raw = anchor.getAttribute("href") || "";
      if (!raw.startsWith("/")) return null; // external / hash / mailto — leave alone
      // Query kept (it is the view's state, and a background tab should open
      // showing what the link points at), fragment dropped (in-page only).
      const path = raw.split("#")[0];
      if (!pathnameOf(path).startsWith(base)) return null;
      return { path, title: anchor.getAttribute("data-tab-title") || undefined };
    };

    const onClick = (e: MouseEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
      const hit = resolveHref(e.target);
      if (!hit) return;
      e.preventDefault();
      // Capture phase + stopPropagation is what keeps the card's own onClick
      // (which would navigate the ACTIVE tab) from also firing.
      e.stopPropagation();
      openInNewTab(hit.path, { title: hit.title });
    };
    const onAux = (e: MouseEvent) => {
      if (e.button !== 1) return;
      const hit = resolveHref(e.target);
      if (!hit) return;
      e.preventDefault();
      e.stopPropagation();
      openInNewTab(hit.path, { title: hit.title });
    };
    // Middle-click's default (autoscroll) is armed on mousedown, so it has to
    // be suppressed there too or the page sprouts a scroll cursor.
    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 1) return;
      if (resolveHref(e.target)) e.preventDefault();
    };

    // WINDOW, not document, and capture phase — this ordering is load-bearing.
    // Next hydrates the whole document, so React's own delegated click
    // listener lives on `document` and was registered at hydration, i.e.
    // BEFORE this effect. A document-capture listener here would therefore run
    // second and could not stop React from also firing the card's onClick, so
    // a ⌘-click would open a background tab AND navigate the current one.
    // `window` is the first entry in the capture path, ahead of document, so
    // stopping propagation here really does stop everything downstream.
    window.addEventListener("click", onClick, true);
    window.addEventListener("auxclick", onAux, true);
    window.addEventListener("mousedown", onMouseDown, true);
    return () => {
      window.removeEventListener("click", onClick, true);
      window.removeEventListener("auxclick", onAux, true);
      window.removeEventListener("mousedown", onMouseDown, true);
    };
  }, [base, openInNewTab]);

  const api = useMemo<TabsApi>(
    () => ({
      tabs: state.tabs,
      activeId: state.activeId,
      openInNewTab,
      activateTab,
      closeTab,
      newTab,
      canGoBack,
      canGoForward,
      goBack,
      goForward,
    }),
    [
      state.tabs,
      state.activeId,
      openInNewTab,
      activateTab,
      closeTab,
      newTab,
      canGoBack,
      canGoForward,
      goBack,
      goForward,
    ],
  );

  return (
    <TabsContext.Provider value={api}>
      <Suspense fallback={null}>
        <SearchParamsBridge onChange={setSearch} />
      </Suspense>
      {children}
    </TabsContext.Provider>
  );
}

/**
 * ‹ / › — app-chrome history navigation, rendered at the head of the tab
 * strip (Linear's own top-left placement for the same cluster).
 *
 * THESE WALK THE ACTIVE TAB'S OWN STACK (see FleetTab.history), not the
 * browser's global one. The first version of this feature did the opposite:
 * it rode the History API through a `nav-history.ts` module whose header
 * argued at length that a parallel app-level stack could only ever be an
 * approximation of the real history, and that sharing the browser's pointer
 * meant the two controls could never disagree. That reasoning is overturned
 * and the module is deleted. It never accounted for this app having a tab
 * model of its own: the browser's history is ONE line with every tab's
 * navigations interleaved into it, so ‹ routinely threw the reader into a
 * DIFFERENT content tab while abandoning their position in the tab they were
 * actually in — and the stamped-index scheme it needed to answer
 * canGoBack/canGoForward under-reported after any reload, leaving the buttons
 * dead when real history sat on both sides. "Never disagrees with the
 * browser" was the wrong property to optimise for; a browser does not give
 * its OWN tabs a shared back button either.
 *
 * Disabled (not hidden) at either end of history — a real `disabled`
 * button, not a silently inert one (CLAUDE.md: no dead controls). Native
 * `disabled` also removes it from tab order on its own, so there's nothing
 * extra to wire for the enabled case to stay keyboard-reachable. The states
 * are now exact rather than conservative: a tab knows its own index and its
 * own length, so ‹ is enabled precisely when something precedes the current
 * entry and › precisely when something follows it.
 *
 * Shortcut is ⌘[ / ⌘] (Ctrl on non-Mac) — Safari and Chrome's own
 * long-standing back/forward menu equivalents on macOS, not ArrowLeft/Right:
 * PrimaryRail's `g`-chord already bails out on any modifier key, so there's
 * no collision there, but arrow keys specifically are live elsewhere as
 * PLAIN (unmodified-key-agnostic) shortcuts — the rail-resizer and the
 * workstation split-workbench's resize handles both react to a bare
 * ArrowLeft/ArrowRight keydown without checking for a held Alt/Cmd, so
 * Alt+Left/Right (the Windows/Linux convention) would double-fire a resize
 * on top of the navigation if one of those handles happened to be focused.
 * Brackets are free of that: nothing else in this codebase binds them.
 */
function FleetHistoryControls() {
  // Read through the context rather than taken as props: this renders inside
  // FleetTabStrip, which has already bailed out when there is no provider, but
  // the optional chaining keeps the hooks below unconditional either way.
  const api = useContext(TabsContext);
  const canGoBack = Boolean(api?.canGoBack);
  const canGoForward = Boolean(api?.canGoForward);
  const goBack = api?.goBack;
  const goForward = api?.goForward;

  useEffect(() => {
    const isTyping = () => {
      const el = document.activeElement as HTMLElement | null;
      if (!el) return false;
      return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable;
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey || isTyping()) return;
      if (e.key === "[") {
        if (!canGoBack || !goBack) return;
        e.preventDefault();
        goBack();
      } else if (e.key === "]") {
        if (!canGoForward || !goForward) return;
        e.preventDefault();
        goForward();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [canGoBack, canGoForward, goBack, goForward]);

  return (
    <div className="fleet-navhistory">
      <button
        type="button"
        className="fleet-navhistory-btn"
        onClick={() => goBack?.()}
        disabled={!canGoBack}
        aria-label="Back"
        title="Back (⌘[)"
      >
        <ChevronLeft size={15} strokeWidth={2} />
      </button>
      <button
        type="button"
        className="fleet-navhistory-btn"
        onClick={() => goForward?.()}
        disabled={!canGoForward}
        aria-label="Forward"
        title="Forward (⌘])"
      >
        <ChevronRight size={15} strokeWidth={2} />
      </button>
    </div>
  );
}

/** The strip itself. Rendered by FleetContentFrame on the canvas directly
 *  ABOVE the floating content panel (MAN-127) — see this file's header. */
export function FleetTabStrip() {
  const api = useContext(TabsContext);
  const activeRef = useRef<HTMLDivElement | null>(null);

  // Keep the active tab in view when it is opened off the visible end of a
  // long strip (e.g. a ⌘-clicked tab that is later activated).
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [api?.activeId]);

  if (!api) return null;
  const { tabs, activeId, activateTab, closeTab, newTab } = api;

  return (
    <div className="fleet-tabstrip">
      {/* History cluster leads the strip (Linear's own top-left placement) —
          not inside the tablist below: `role="tablist"` should contain only
          `role="tab"` children, and moving it onto `.fleet-tabstrip-list`
          specifically (rather than leaving it up here where it used to
          cover the `+` button too) fixes that for the same reason. */}
      <FleetHistoryControls />
      {/* The list is sized to its content (`flex: 0 1 auto`, in the CSS), so it
          ends where the last tab ends and `+` lands right beside it — Safari,
          Chrome and Linear all put it there.

          OVERFLOW: `+` deliberately stays OUTSIDE the scroller. Once the tabs
          stop fitting, the list (min-width: 0) shrinks to the space left and
          scrolls inside itself, so `+` comes to rest pinned at the right edge
          next to the last visible tab — never scrolled out of reach, which is
          what putting it inside the scroller would have cost. Safari and
          Chrome behave the same way once their tab bar is full. */}
      <div className="fleet-tabstrip-list" role="tablist" aria-label="Open views">
        {tabs.map((tab) => {
          const Icon = KIND_ICONS[tab.kind] || Square;
          const active = tab.id === activeId;
          return (
            <div
              key={tab.id}
              ref={active ? activeRef : undefined}
              className={`fleet-tab${active ? " fleet-tab--active" : ""}`}
              role="tab"
              aria-selected={active}
              tabIndex={0}
              title={tab.title}
              onClick={() => activateTab(tab.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  activateTab(tab.id);
                }
              }}
              onAuxClick={(e) => {
                // Middle-click closes a tab, same as every browser.
                if (e.button === 1) {
                  e.preventDefault();
                  e.stopPropagation();
                  closeTab(tab.id);
                }
              }}
            >
              <span className="fleet-tab-icon">
                <Icon size={13} strokeWidth={1.75} />
              </span>
              <span className="fleet-tab-title">{tab.title}</span>
              {/* Always closable, including the last tab — closing that one
                  lands on the default view rather than leaving the panel
                  blank (see closeTab). */}
              <button
                type="button"
                className="fleet-tab-close"
                aria-label={`Close ${tab.title}`}
                onClick={(e) => {
                  e.stopPropagation();
                  closeTab(tab.id);
                }}
              >
                <X size={12} strokeWidth={2} />
              </button>
            </div>
          );
        })}
      </div>
      <button type="button" className="fleet-tabstrip-new" onClick={newTab} title="New tab" aria-label="New tab">
        <Plus size={14} strokeWidth={2} />
      </button>
    </div>
  );
}
