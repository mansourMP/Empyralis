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
  ownerName,
  ownerEmail,
  ownerRole,
}: {
  workspaceId: string;
  shellSlot: ReactNode;
  children: ReactNode;
  ownerName?: string;
  ownerEmail?: string;
  ownerRole?: string;
}) {
  const {
    theme,
    collapsed,
    toggleTheme,
    toggleCollapsed,
  } = useFleetPreferences();

  return (
    <div className="fleet-root" data-theme={theme}>
      <PrimaryRail
        workspaceId={workspaceId}
        ownerName={ownerName}
        ownerEmail={ownerEmail}
        ownerRole={ownerRole}
        collapsed={collapsed}
        onToggleCollapsed={toggleCollapsed}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <FleetShellDecider workspaceId={workspaceId} shellSlot={shellSlot}>{children}</FleetShellDecider>
      </div>
      <FleetCommandPalette
        workspaceId={workspaceId}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
    </div>
  );
}
