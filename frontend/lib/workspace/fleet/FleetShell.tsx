"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";

import "./fleet-theme.css";
import { CloudProvisionNotifier } from "./CloudProvisionNotifier";
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
    sections,
    toggleTheme,
    toggleCollapsed,
    toggleSection,
  } = useFleetPreferences();

  const [sageOpen, setSageOpen] = useState(false);
  const onOpenSage = useCallback(() => setSageOpen(true), []);
  const onCloseSage = useCallback(() => setSageOpen(false), []);

  // Listen for the custom event dispatched by FleetHome / any other component
  // that wants to open the Sage console without a direct prop thread.
  useEffect(() => {
    const handler = () => onOpenSage();
    window.addEventListener("fleet:open-sage", handler);
    return () => window.removeEventListener("fleet:open-sage", handler);
  }, [onOpenSage]);

  // Mobile nav drawer — same event-bus pattern as fleet:open-sage above.
  // The hamburger button lives in FleetContentFrame, several layers below
  // FleetShellDecider; a custom event avoids threading a setter down through
  // it just for this one narrow-viewport control.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const closeMobileNav = useCallback(() => setMobileNavOpen(false), []);
  useEffect(() => {
    const toggle = () => setMobileNavOpen((v) => !v);
    window.addEventListener("fleet:toggle-mobile-nav", toggle);
    return () => window.removeEventListener("fleet:toggle-mobile-nav", toggle);
  }, []);
  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileNavOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileNavOpen]);

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
        sections={sections}
        onToggleSection={toggleSection}
        mobileOpen={mobileNavOpen}
        onCloseMobile={closeMobileNav}
        sageOpen={sageOpen}
        onOpenSage={onOpenSage}
        onCloseSage={onCloseSage}
      />
      {mobileNavOpen && <div className="fleet-rail-scrim" onClick={closeMobileNav} />}
      {/* No panel wrapper here: each branch of the decider owns its own panel
          box — see FleetContentFrame / FleetShellDecider. */}
      <FleetShellDecider workspaceId={workspaceId} shellSlot={shellSlot}>{children}</FleetShellDecider>
      <FleetCommandPalette
        workspaceId={workspaceId}
        theme={theme}
        onToggleTheme={toggleTheme}
        onOpenSage={onOpenSage}
      />
      {/* A cloud server can now be set up in the background, which means it
          usually finishes while the user is somewhere else in the workspace.
          Mounted here so the "it's ready" / "it failed" notification reaches
          them wherever that is. Renders nothing at all unless a build is
          actually settling. */}
      <CloudProvisionNotifier workspaceId={workspaceId} />
    </div>
  );
}
