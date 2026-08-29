"use client";

import { useParams } from "next/navigation";

import { WorkLedgerView } from "@/lib/workspace/fleet/WorkLedgerView";

/**
 * The workspace work ledger — one level below Agents (CLAUDE.md: "a surface
 * must earn its place — if it can live one level down, it should"), never a
 * primary rail item. Reached by a real link from the Agents page toolbar
 * (see agents/page.tsx) so cmd-click still opens a new tab.
 *
 * No <h1> here on purpose — FleetContentFrame's Breadcrumbs component
 * already renders the current crumb ("Work", under STATIC_LABELS in
 * Breadcrumbs.tsx) as the page's one real h1 (MAN-145 title-dedup),
 * matching every other routed page in this directory (ConversationsPage,
 * InboxPage — neither renders its own h1 either).
 */
export default function AgentsWorkPage() {
  const params = useParams();
  const workspaceId = String(params?.workspaceId || "");
  return <WorkLedgerView workspaceId={workspaceId} />;
}
