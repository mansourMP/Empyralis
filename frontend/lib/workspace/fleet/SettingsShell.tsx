"use client";

import Link from "next/link";
import { CreditCard } from "lucide-react";

import { AccountSection } from "@/lib/workspace/fleet/AccountSection";
import { WorkspaceNameSection } from "@/lib/workspace/fleet/WorkspaceNameSection";
import { MembersSection } from "@/lib/workspace/fleet/MembersSection";
import { EmergencyStopSection } from "@/lib/workspace/fleet/EmergencyStopSection";
import { HardwareSection } from "@/lib/workspace/fleet/HardwareSection";
import { McpServersSection } from "@/lib/workspace/fleet/McpServersSection";
import { McpApiKeysSection } from "@/lib/workspace/fleet/McpApiKeysSection";
import { ChannelPairingSection } from "@/lib/workspace/fleet/ChannelPairingSection";
import { KeyboardShortcutsSection } from "@/lib/workspace/fleet/KeyboardShortcutsSection";
import { type SettingsSection } from "@/lib/workspace/fleet/settings-sections";

/**
 * Settings — ONE active section filling the content width. The section
 * picker is NOT here: it is the primary rail itself, morphed into the
 * Settings space (PrimaryRail.tsx + primary-rail-space.ts — founder,
 * 2026-08-16: "the rail is where you pick; the content is what you
 * picked"). The GroupedRail this component used to render beside the
 * content was a second rail pretending to be content, and it is gone —
 * primary-rail-space.test.ts asserts it stays gone.
 *
 * `section` is the URL's own [section] segment, already resolved and
 * validated by the caller (settings/[section]/page.tsx) — this component
 * derives which section renders directly from that prop on every render,
 * never copying it into local state. Same rule FleetAgentDetail's
 * `initialTab` follows and for the same reason: a stale local copy is how a
 * mid-navigation render silently snaps the UI back to the wrong section.
 *
 * Existing section components (MembersSection, HardwareSection,
 * McpServersSection, …) render completely unchanged — they just have the
 * full reading width now instead of sharing it with a sidebar.
 */
export function SettingsShell({
  workspaceId,
  section,
}: {
  workspaceId: string;
  section: SettingsSection;
}) {
  return (
    <main className="fleet-content fleet-content--wide">
      {/* MAN-145 title-dedup: the breadcrumb's current crumb IS this page's
          <h1> (see Breadcrumbs.tsx) — since the settings crumb chain folds
          to the active section, it reads "Account"/"Workspace"/
          "Connections"/"Keyboard shortcuts" directly. No heading block
          here. */}
      <div>
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

            {/* Inbound: channel senders connecting IN (Slack DMs, SMS,
                WeChat Official, Telegram, WhatsApp) — the other inbound
                direction, same "Connections" grouping as the MCP API keys
                above it. */}
            <ChannelPairingSection workspaceId={workspaceId} />
          </>
        ) : null}

        {/* The shortcut reference — a routed page since 2026-08-16, not an
            account-popover accordion. See KeyboardShortcutsSection.tsx. */}
        {section === "shortcuts" ? <KeyboardShortcutsSection /> : null}
      </div>
    </main>
  );
}
