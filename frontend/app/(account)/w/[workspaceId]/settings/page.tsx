import { redirect } from "next/navigation";

import { DEFAULT_SETTINGS_SECTION } from "@/lib/workspace/fleet/settings-sections";

// Settings without a section → its default section. Keeps the settings-level
// link (PrimaryRail.tsx's settingsHref, /w/{ws}/settings) a live redirect
// instead of a dead end. Mirrors agents/[agentId]/page.tsx's identical
// bare-redirect-to-default pattern; see settings/[section]/page.tsx and
// SettingsShell.tsx for where the real page now lives.
export default async function SettingsIndexRedirect({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  redirect(`/w/${encodeURIComponent(workspaceId)}/settings/${DEFAULT_SETTINGS_SECTION}`);
}
