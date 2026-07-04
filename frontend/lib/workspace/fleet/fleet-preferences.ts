"use client";

import { useCallback, useEffect, useState } from "react";

export type FleetTheme = "light" | "dark";
export type FleetSectionKey = "workspace" | "infrastructure";
type SectionMap = Record<FleetSectionKey, boolean>;

const THEME_KEY = "fleet:theme";
const COLLAPSED_KEY = "fleet:rail-collapsed";
const SECTIONS_KEY = "fleet:rail-sections";
const DEFAULT_THEME: FleetTheme = "dark";
const DEFAULT_SECTIONS: SectionMap = { workspace: true, infrastructure: true };

/**
 * Fleet-local UI preferences (theme, rail collapse, section expansion),
 * persisted to localStorage. Server/first render uses defaults to avoid
 * hydration mismatch; stored choices are applied on mount.
 */
export function useFleetPreferences() {
  const [theme, setTheme] = useState<FleetTheme>(DEFAULT_THEME);
  const [collapsed, setCollapsed] = useState(false);
  const [sections, setSections] = useState<SectionMap>(DEFAULT_SECTIONS);

  useEffect(() => {
    try {
      const storedTheme = window.localStorage.getItem(THEME_KEY);
      if (storedTheme === "light" || storedTheme === "dark") setTheme(storedTheme);
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
    setTheme((prev) => {
      const next = prev === "dark" ? "light" : "dark";
      try {
        window.localStorage.setItem(THEME_KEY, next);
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

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

  return { theme, collapsed, sections, toggleTheme, toggleCollapsed, toggleSection };
}
