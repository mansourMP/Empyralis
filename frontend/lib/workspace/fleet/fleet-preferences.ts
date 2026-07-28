"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";

import { useAccountShell } from "@/lib/shell/account-shell-context";
import { useAppTheme } from "@/lib/ui/app-theme";

export type FleetTheme = "light" | "dark";
export type FleetSectionKey = "projects" | "agents";
type SectionMap = Record<FleetSectionKey, boolean>;

const COLLAPSED_KEY = "fleet:rail-collapsed";
const SECTIONS_KEY = "fleet:rail-sections";
const DEFAULT_SECTIONS: SectionMap = { projects: true, agents: true };

/* ── Resizable panel widths (MAN-126) ──────────────────────────────────────
   Geometry for the two resizable edges. Collapsed rail width and the two
   snap thresholds live here too so the CSS and the pointer maths cannot
   drift apart. */
export const RAIL_WIDTH = {
  key: "fleet:rail-width",
  def: 240,
  min: 200,
  max: 400,
  collapsed: 56,
  /** Dragging right from collapsed past this restores the rail. */
  restoreAt: 120,
} as const;

export const PANEL_WIDTH = {
  key: "fleet:panel-width",
  def: 320,
  min: 280,
  max: 520,
} as const;

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/**
 * The one pointer/keyboard contract for every draggable panel edge, lifted
 * from workstation-split-workbench.tsx (which pioneered it) with one
 * deliberate change: the drag does NOT round-trip through React state.
 *
 * A resize must track the pointer 1:1. Calling setState on every pointermove
 * puts a render — reconciliation, effects, layout — between the user's hand
 * and the pixel, which is exactly the lag that makes a resize feel cheap. So
 * pointermove writes the width straight to a CSS custom property on the
 * element via `style.setProperty`, and React only learns the final number on
 * release (where one render is free). `.is-resizing` on the shell root kills
 * the width transition for the duration, so drag is never eased — only the
 * collapse/expand toggle animates.
 */
export function useResizableWidth({
  storageKey,
  defaultWidth,
  minWidth,
  maxWidth,
  cssVar,
  edge = "right",
  dragFloor,
  onRelease,
}: {
  storageKey: string;
  defaultWidth: number;
  minWidth: number;
  maxWidth: number;
  /** Custom property the live width is written to during a drag. */
  cssVar: string;
  /** Which edge the handle sits on — decides the sign of the drag maths. */
  edge?: "left" | "right";
  /** Lowest width the drag may *show* before release (snap zone). */
  dragFloor?: number;
  /** Commit hook: given the raw released width, return the width to keep. */
  onRelease?: (raw: number) => number;
}) {
  const [width, setWidth] = useState(defaultWidth);
  const [resizing, setResizing] = useState(false);
  const elRef = useRef<HTMLElement | null>(null);

  // Hydrate after mount, never during render — same timing as the collapse
  // and section prefs above, so SSR and first paint agree.
  useEffect(() => {
    try {
      const stored = Number(window.localStorage.getItem(storageKey));
      if (Number.isFinite(stored) && stored > 0) setWidth(clamp(stored, minWidth, maxWidth));
    } catch {
      /* localStorage unavailable — keep the default */
    }
  }, [storageKey, minWidth, maxWidth]);

  // Mirror committed state onto the element. During a drag this is skipped:
  // the pointermove handler owns the property and React must not fight it.
  useEffect(() => {
    if (resizing) return;
    elRef.current?.style.setProperty(cssVar, `${width}px`);
  }, [cssVar, width, resizing]);

  const persist = useCallback(
    (next: number) => {
      try {
        window.localStorage.setItem(storageKey, String(next));
      } catch {
        /* ignore */
      }
    },
    [storageKey],
  );

  const commit = useCallback(
    (next: number) => {
      const settled = clamp(Math.round(next), minWidth, maxWidth);
      setWidth(settled);
      persist(settled);
      return settled;
    },
    [maxWidth, minWidth, persist],
  );

  const onPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const el = elRef.current;
      if (!el || event.button !== 0) return;
      event.preventDefault();

      // Delta maths, deliberately, rather than "pointer X minus the panel's
      // left edge". getBoundingClientRect() reports the element's *visual*
      // box, so it includes any transform — and the properties drawer is
      // literally defined by a translateX. Grabbing its handle while that
      // transform was non-zero fed the slide offset straight into the width
      // and slammed the panel to its maximum. Measuring from where the drag
      // started is immune to that, and to the element's position generally,
      // while tracking the pointer exactly as 1:1 as before.
      const startWidth = el.getBoundingClientRect().width;
      const startX = event.clientX;
      const root = el.closest(".fleet-root") as HTMLElement | null;
      const floor = dragFloor ?? minWidth;
      let raw = startWidth;

      const prevCursor = document.body.style.cursor;
      const prevSelect = document.body.style.userSelect;
      const prevTouch = document.body.style.touchAction;

      const onMove = (move: globalThis.PointerEvent) => {
        const delta = edge === "right" ? move.clientX - startX : startX - move.clientX;
        raw = clamp(startWidth + delta, floor, maxWidth);
        // Straight to the DOM — no setState, no re-render, no lag.
        el.style.setProperty(cssVar, `${raw}px`);
      };

      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        document.body.style.cursor = prevCursor;
        document.body.style.userSelect = prevSelect;
        document.body.style.touchAction = prevTouch;
        root?.classList.remove("is-resizing");
        setResizing(false);
        commit(onRelease ? onRelease(raw) : raw);
      };

      setResizing(true);
      root?.classList.add("is-resizing");
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      document.body.style.touchAction = "none";
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", onUp);
    },
    [commit, cssVar, dragFloor, edge, maxWidth, minWidth, onRelease],
  );

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>) => {
      const step = 16;
      const dir = edge === "right" ? 1 : -1;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        commit(width - step * dir);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        commit(width + step * dir);
      } else if (event.key === "Home") {
        event.preventDefault();
        commit(minWidth);
      } else if (event.key === "End") {
        event.preventDefault();
        commit(defaultWidth);
      }
    },
    [commit, defaultWidth, edge, minWidth, width],
  );

  /** Spread onto the handle element — full ARIA slider-separator semantics. */
  const separatorProps = {
    role: "separator" as const,
    "aria-orientation": "vertical" as const,
    "aria-valuemin": minWidth,
    "aria-valuemax": maxWidth,
    "aria-valuenow": width,
    tabIndex: 0,
    onPointerDown,
    onKeyDown,
  };

  return { width, setWidth: commit, elRef, resizing, separatorProps };
}

/**
 * Fleet-local UI preferences (rail collapse, section expansion), persisted
 * to localStorage. Theme is NOT fleet-local: it delegates to the single
 * account-wide theme (useAppTheme/useAccountShell) so `data-theme` on
 * <html> and `.fleet-root` always agree — see docs/UI-MODEL.md. Fleet used
 * to keep its own `fleet:theme` key defaulting to dark while the account
 * shell defaulted to light, so the two could show opposite themes at once.
 */
export function useFleetPreferences() {
  const { resolvedTheme } = useAppTheme();
  const { actions } = useAccountShell();
  const [collapsed, setCollapsed] = useState(false);
  const [sections, setSections] = useState<SectionMap>(DEFAULT_SECTIONS);

  useEffect(() => {
    try {
      if (window.localStorage.getItem(COLLAPSED_KEY) === "1") setCollapsed(true);
      const raw = window.localStorage.getItem(SECTIONS_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          setSections({ ...DEFAULT_SECTIONS, ...parsed });
        }
      }
    } catch {
      /* localStorage unavailable — keep defaults */
    }
  }, []);

  const toggleTheme = useCallback(() => {
    actions.setGlobalTheme(resolvedTheme === "dark" ? "light" : "dark");
  }, [actions, resolvedTheme]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  const toggleSection = useCallback((key: FleetSectionKey) => {
    setSections((prev) => {
      const next = { ...prev, [key]: !prev[key] };
      try {
        window.localStorage.setItem(SECTIONS_KEY, JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  return { theme: resolvedTheme as FleetTheme, collapsed, sections, toggleTheme, toggleCollapsed, toggleSection };
}
