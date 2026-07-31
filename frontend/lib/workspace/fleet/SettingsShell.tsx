"use client";

import Link from "next/link";
import { Building2, CreditCard, Plug, UserCog } from "lucide-react";

import { GroupedRail, type GroupedRailGroup } from "@/lib/workspace/fleet/GroupedRail";
import { AccountSection } from "@/lib/workspace/fleet/AccountSection";
import { WorkspaceNameSection } from "@/lib/workspace/fleet/WorkspaceNameSection";
import { MembersSection } from "@/lib/workspace/fleet/MembersSection";
import { EmergencyStopSection } from "@/lib/workspace/fleet/EmergencyStopSection";
import { HardwareSection } from "@/lib/workspace/fleet/HardwareSection";
import { McpServersSection } from "@/lib/workspace/fleet/McpServersSection";
import { McpApiKeysSection } from "@/lib/workspace/fleet/McpApiKeysSection";
import { SETTINGS_SECTIONS, type SettingsSection } from "@/lib/workspace/fleet/settings-sections";

import "./settings-shell.css";

const SECTION_LABEL: Record<SettingsSection, string> = {
  account: "Account",
  workspace: "Workspace",
  connections: "Connections",
};

const SECTION_ICON: Record<SettingsSection, typeof UserCog> = {
  account: UserCog,
  workspace: Building2,
  connections: Plug,
};

/**
 * Settings — a routed page (GroupedRail + one active section) that replaced
 * the old single page with all seven settings stacked in a row. `section`
 * is the URL's own [section] segment, already resolved and validated by
 * the caller (settings/[section]/page.tsx) — this component derives the
 * rail's active item and which section renders directly from that prop on
 * every render, never copying it into local state. Same rule
 * FleetAgentDetail's `initialTab` follows and for the same reason: a stale
 * local copy is how a mid-navigation render silently snaps the UI back to
 * the wrong section (see FleetAgentDetail.tsx's comment, ~line 281).
 *
 * Existing section components (MembersSection, HardwareSection,
 * McpServersSection) render completely unchanged — they just render one
 * group at a time now instead of all seven stacked on one page.
 */
export function SettingsShell({
  workspaceId,
  section,
}: {
  workspaceId: string;
  section: SettingsSection;
}) {
  const base = `/w/${encodeURIComponent(workspaceId)}/settings`;

  const groups: GroupedRailGroup[] = [
    {
      id: "settings",
      items: SETTINGS_SECTIONS.map((id) => ({
        id,
        label: SECTION_LABEL[id],
        href: `${base}/${id}`,
        icon: SECTION_ICON[id],
      })),
    },
  ];

  return (
    <main className="fleet-content fleet-content--wide">
      <div className="fleet-header">
        <h1 className="fleet-title">Settings</h1>
      </div>
      <div className="settings-shell-body">
        <GroupedRail groups={groups} activeId={section} ariaLabel="Settings sections" />
        <div className="settings-shell-content">
          {section === "account" ? <AccountSection /> : null}

          {section === "workspace" ? (
            <>
              <WorkspaceNameSection workspaceId={workspaceId} />
              <MembersSection workspaceId={workspaceId} />

              <h2 className="fleet-detail-section-title" style={{ marginTop: "var(--space-6)" }}>Billing</h2>
              <Link href={`/w/${workspaceId}/billing`} className="fleet-list-row" style={{ textDecoration: "none" }}>
                <span className="fleet-list-row-icon"><CreditCard size={16} strokeWidth={1.75} /></span>
                <span className="fleet-list-row-main">
                  <span className="fleet-list-row-title">Billing &amp; usage</span>
                  <span className="fleet-list-row-desc">Plans, credits, and per-agent spend.</span>
                </span>
                <span className="fleet-list-row-meta">Open →</span>
              </Link>

              <EmergencyStopSection workspaceId={workspaceId} />
            </>
          ) : null}

          {section === "connections" ? (
            <>
              {/* Hardware — who works here, then what they work ON. Same
                  standalone-route pairing as before (see hardware/page.tsx,
                  which renders this exact component with heading={false}). */}
              <HardwareSection workspaceId={workspaceId} />

              {/* Outbound: this workspace's agents connecting OUT to remote
                  MCP servers/tools. */}
              <McpServersSection workspaceId={workspaceId} />

              {/* Inbound: external MCP clients connecting IN — its own
                  section now, not an inline block tacked onto the page. */}
              <McpApiKeysSection workspaceId={workspaceId} />
            </>
          ) : null}
        </div>
      </div>
    </main>
  );
}
