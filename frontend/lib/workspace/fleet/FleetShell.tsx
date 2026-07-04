"use client";

import type { ReactNode } from "react";

import "./fleet-theme.css";
import { FleetShellDecider } from "./FleetShellDecider";
import { PrimaryRail } from "./PrimaryRail";
import { useFleetPreferences } from "./fleet-preferences";

/**
 * Themed root for the whole workspace surface. Owns the fleet-local theme +
 * rail-collapse preferences and exposes them to the rail. `data-theme` on the
 * root flips every fleet token in one place.
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
  const { theme, collapsed, toggleTheme, toggleCollapsed } = useFleetPreferences();

  return (
    <div className="fleet-root" data-theme={theme}>
      <PrimaryRail
        workspaceId={workspaceId}
        collapsed={collapsed}
        onToggleCollapsed={toggleCollapsed}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <FleetShellDecider shellSlot={shellSlot}>{children}</FleetShellDecider>
      </div>
    </div>
  );
}
