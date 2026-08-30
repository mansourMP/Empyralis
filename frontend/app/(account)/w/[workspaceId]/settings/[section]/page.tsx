"use client";

import { useParams } from "next/navigation";

import { SettingsShell } from "@/lib/workspace/fleet/SettingsShell";
import { DEFAULT_SETTINGS_SECTION, isSettingsSection } from "@/lib/workspace/fleet/settings-sections";

/**
 * Settings, routed by section — mirrors the agent detail pattern
 * (agents/[agentId]/[tab]/page.tsx): the bare
 * settings/page.tsx redirects here with a default section, this page reads
 * and validates the URL's own [section] segment, and SettingsShell derives
 * everything from that on every render rather than a local copy.
 *
 * An unrecognized segment (bad/typo'd URL, or a bookmark to a section that
 * no longer exists) falls back to the default rather than a 404 — same
 * choice the agent tab route makes for an unknown tab.
 */
export default function SettingsSectionPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  const rawSection = String(params?.section || "");
  const section = isSettingsSection(rawSection) ? rawSection : DEFAULT_SETTINGS_SECTION;

  return <SettingsShell workspaceId={workspaceId} section={section} />;
}
