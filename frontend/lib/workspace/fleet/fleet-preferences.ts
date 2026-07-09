"use client";

import { useCallback, useEffect, useState } from "react";

import { useAccountShell } from "@/lib/shell/account-shell-context";
import { useAppTheme } from "@/lib/ui/app-theme";

export type FleetTheme = "light" | "dark";
export type FleetSectionKey = "projects" | "agents";
type SectionMap = Record<FleetSectionKey, boolean>;

const COLLAPSED_KEY = "fleet:rail-collapsed";
const SECTIONS_KEY = "fleet:rail-sections";
const DEFAULT_SECTIONS: SectionMap = { projects: true, agents: true };

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
