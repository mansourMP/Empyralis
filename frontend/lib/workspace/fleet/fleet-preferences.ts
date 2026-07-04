"use client";

import { useCallback, useEffect, useState } from "react";

export type FleetTheme = "light" | "dark";

const THEME_KEY = "fleet:theme";
const COLLAPSED_KEY = "fleet:rail-collapsed";
const DEFAULT_THEME: FleetTheme = "dark";

/**
 * Fleet-local UI preferences (theme + rail collapse), persisted to
 * localStorage. Server/first render uses defaults to avoid hydration
 * mismatch; the stored choice is applied on mount.
 */
export function useFleetPreferences() {
  const [theme, setTheme] = useState<FleetTheme>(DEFAULT_THEME);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    try {
      const storedTheme = window.localStorage.getItem(THEME_KEY);
      if (storedTheme === "light" || storedTheme === "dark") {
        setTheme(storedTheme);
      }
      const storedCollapsed = window.localStorage.getItem(COLLAPSED_KEY);
      if (storedCollapsed === "1") {
        setCollapsed(true);
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

  return { theme, collapsed, toggleTheme, toggleCollapsed };
}
