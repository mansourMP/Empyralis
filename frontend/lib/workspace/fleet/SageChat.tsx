"use client";

import { Sparkles } from "lucide-react";

import { PRIMARY_THREAD_ID } from "@/lib/workspace/workstation-chat-pane-model";
import { AgentChat } from "./AgentChat";
import { useBreadcrumbBadge } from "./Breadcrumbs";

const STARTER_PROMPTS = [
  "What agents do I have?",
  "Create a support agent for my store",
  "Help me set up a Telegram bot",
];

// Module-level constant, not created inline in the component body: the
// breadcrumb badge registry keys on referential identity (see
// useBreadcrumbBadge), so a fresh JSX element on every render would re-fire
// its effect every render — a render loop. One stable element, created once.
const OPERATOR_BADGE = <span className="fleet-badge fleet-badge--operator">Operator</span>;

/**
 * Sage's whole surface: a full-width conversation with the Operator. No
 * tabs, no stat cards, no right panel, no deployment status — per the UI
 * contract, this is the command line of the platform, not an agent detail
 * page. History + live cross-channel sync come from the canonical
 * workspace-scoped "sage-main" thread; sending streams the reply inline.
 */
export function SageChat({ workspaceId }: { workspaceId: string }) {
  // The breadcrumb already says "Sage" — no second "Sage · Operator" header
  // block repeating it below. The role marker rides along on the breadcrumb
  // crumb itself instead (small badge, contract: no doubled page titles).
  useBreadcrumbBadge("sage", OPERATOR_BADGE);

  return (
    <AgentChat
      workspaceId={workspaceId}
      threadId={PRIMARY_THREAD_ID}
      emptyIcon={Sparkles}
      emptyTitle="Ask Sage anything"
      emptyBody="Ask Sage to create an agent, check your fleet, or set something up."
      starterPrompts={STARTER_PROMPTS}
      placeholder="Message Sage…"
      sourceTag="fleet_sage_chat"
      liveSyncUrl={`/api/workstation/${encodeURIComponent(workspaceId)}/sage/turns/stream`}
    />
  );
}
