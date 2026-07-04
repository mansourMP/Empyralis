"use client";

import type { ReactNode } from "react";

import "./fleet-theme.css";
import { FleetCommandPalette } from "./FleetCommandPalette";
import { FleetShellDecider } from "./FleetShellDecider";
import { PrimaryRail } from "./PrimaryRail";
import { useFleetPreferences } from "./fleet-preferences";

/**
 * Themed root for the whole workspace surface. Owns the fleet-local theme,
 * rail-collapse, and section-expansion preferences. `data-theme` on the
 * root flips every fleet token in one place. Also hosts the global Cmd+K
 * command palette.
 */
export function FleetShell({
  workspaceId,
  shellSlot,
  children,
}: {
  workspaceId: string;
  shellSlot: ReactNode;
  children: ReactNode;
}) {
  const {
    theme,
    collapsed,
    sections,
    toggleTheme,
    toggleCollapsed,
    toggleSection,
  } = useFleetPreferences();

  return (
    <div className="fleet-root" data-theme={theme}>
      <PrimaryRail
        workspaceId={workspaceId}
        collapsed={collapsed}
        onToggleCollapsed={toggleCollapsed}
        theme={theme}
        onToggleTheme={toggleTheme}
        sections={sections}
        onToggleSection={toggleSection}
      />
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <FleetShellDecider shellSlot={shellSlot}>{children}</FleetShellDecider>
      </div>
      <FleetCommandPalette
        workspaceId={workspaceId}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
    </div>
  );
}
