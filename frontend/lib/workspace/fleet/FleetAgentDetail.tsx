"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  BookOpen,
  Brain,
  Check,
  ChevronRight,
  Clock,
  Cpu,
  FolderTree,
  LayoutGrid,
  Loader2,
  Lock,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Plug,
  Radio,
  RefreshCw,
  Smartphone,
  Sparkles,
  Square,
  Trash2,
  Wand2,
  X,
  type LucideIcon,
} from "lucide-react";

import { WorkTab } from "./tabs/WorkTab";
import { ContextTab } from "./tabs/ContextTab";
import { HardwareTab } from "./tabs/HardwareTab";
import { MemoryTab } from "./tabs/MemoryTab";
import { ProfileFilesSection } from "./tabs/ProfileFilesSection";
import { GroupedRail, type GroupedRailGroup } from "./GroupedRail";
import { defaultAgentProfileSegment, isProfileTab, planAgentProfileSegments, type AgentProfileSegmentId } from "./agent-profile-shape";
import { agentSetupHeading, agentSetupNextStep } from "./agent-setup-steps";
import { AgentDeleteDialog } from "./AgentDeleteDialog";
import { canDeleteAgent, deleteFleetAgent } from "./agent-delete";

import {
  resumeFleetAgent,
  stopFleetAgent,
  useFleetAgentChannels,
  useFleetAgentConnectors,
  useFleetAgentCapabilities,
  useFleetAgentSchedule,
  previewFleetAgentSchedule,
  createFleetAgentSchedule,
  deleteFleetAgentSchedule,
  friendlyChannelOwnershipError,
  useFleetProjects,
  useFleetWorkspaceTasks,
  type FleetAgent,
  type FleetAgentSkill,
  type FleetChannel,
  type FleetCapability,
  type FleetScheduleItem,
} from "./fleet-data";
import { groupTasksByAgent } from "./agent-card-face";
import { agentDisplayStatus } from "./agent-view-options";
import { timeAgo, formatDateTime, formatNumber, usagePayerLabel, type AgentStatusTone, type UsageMatrixRow } from "./fleet-presentation";
import { AgentSigil, StatusChip, StatusDot } from "./fleet-indicators";
import { PanelSection, PanelRow, FleetRightPanel, type PanelValueTone } from "./FleetRightPanel";
import type { UsageBucket } from "./fleet-sparkline";
import { channelIconSrc } from "./fleet-icons";
import { ConnectorPicker } from "./ConnectorPicker";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { getErrorMessage } from "@/lib/ui/api-error";
import { RUNTIME_LABELS } from "./gateway-box-picker";
import { resolveAgentModelSummary, platformCreditsTierLabel } from "./fleet-model-config";
import { FleetToggleRowsSkeleton, FleetCardGridSkeleton } from "./fleet-states";

import "./agent-configure-sheet.css";
import "./agent-profile-sheet.css";
import { formatUsd } from "../../ui/money";

// Properties panel's Placement row used to render placement.label with no
// tone at all — the one row on that panel that never went red/green, even
// though the adjacent Status row does. PanelRow's tone vocabulary is
// smaller than HardwarePlacementTone's (no "degraded"), so this collapses
// onto the closest existing bucket the same way HardwareTab's own
// placement dot already does (see gateway-box-picker.tsx's
// resolveHardwarePlacement + HardwareTab.tsx's dotClass): cloud/online read
// as healthy, everything else — degraded, offline, or never-paired — reads
// as offline so a disconnected/unpaired box is visually distinct.
const HARDWARE_PLACEMENT_PANEL_TONE: Record<HardwarePlacementTone, PanelValueTone> = {
  cloud: "online",
  online: "online",
  degraded: "offline",
  offline: "offline",
  unpaired: "offline",
};

// The backend's channels[].connected for Slack (routes_fleet.py's
// fleet_agent_channels) reflects OAuth app install only — a workspace can
// have the Slack app installed with no channel bound to THIS agent yet.
// Every reader of "is this platform really connected" (the Properties
// count below, the channel grid pill, the "already connected" banner
// prefill) needs to agree that Slack isn't connected until it owns a
// channel — slackChannelBinding, this same fetch's endpoint_key equivalent
// — not merely OAuth-installed.
//
// Telegram has the mirror-image gap: sage_telegram_hosted's own `connected`
// only reflects the hosted-bot door (Sage's shared bot, deep-linked to this
// agent). A BYO bot token (this agent's own bot, see CHANNEL_DOORS'
// sage_telegram_hosted.byo_bot) lives entirely in telegramBotConnected —
// same "rides along on this fetch instead of the channel's own row" shape
// as slackChannelBinding — so a live BYO-bot agent read as connected: false
// everywhere here: undercounted in the Properties "Channels" total, and the
// grid pill showed "Set up" instead of "Connected".
// Exported (2026-08-21) for AgentCreateCard's Channel step, which needs the
// SAME "is this really connected" answer this file already computes — Slack
// and hosted Telegram each answer it from a second field, so a caller that
// read `channel.connected` alone would quietly undercount and tell someone
// their channel step was still empty. One rule, two call sites, never two
// rules.
export function isChannelConnected(
  channel: Pick<FleetChannel, "id" | "connected">,
  slackChannelBinding: string | null,
  telegramBotConnected: boolean,
): boolean {
  if (channel.id === "slack") return Boolean(slackChannelBinding);
  if (channel.id === "sage_telegram_hosted") return channel.connected || telegramBotConnected;
  return channel.connected;
}

// "work" stays a legal value — [tab]/page.tsx's VALID_TABS still accepts a
// direct hit on the old .../work URL, same "dead-but-live" treatment
// CLAUDE.md already documents for /agents and /conversations — but it is no
// longer a distinct SURFACE: activeTab==="work" renders the exact same
// observation view as activeTab==="chat" (see the render below), so an old
// bookmark still works instead of 404ing.
type TabId = "general" | "work" | "channels" | "connectors" | "hardware" | "model" | "skills" | "memory" | "capabilities" | "chat" | "persona" | "context";

// Single source of id/label/icon truth for every one of the remaining
// sections — Chat is the agent's front door, rendered directly (no tab
// strip; see the render below), Persona and Memory live in the Profile
// sheet (opened by tapping the agent's own identity — see
// PROFILE_SEGMENT_DEFS below), the other eight live in the Configure
// sheet's GroupedRail groups (CONFIGURE_GROUPS below), and all three
// surfaces read labels/icons from here so none of them can drift from the
// others. "work" is deliberately absent from this list now (see the TabId
// comment above) — it has no label/icon of its own to show anywhere, it
// just resolves to the same content as "chat". Declared with "chat" first —
// chat is the agent's front door (an agent opens to Chat, not a config
// screen — see [tab]/page.tsx's own "chat" fallback). The Configure and
// Profile sheets both ignore this order entirely (each picks its own
// members/grouping explicitly by id).
//
// "Overview" is GONE, not renamed (founder, 2026-08-13: "remove overview
// because it's something that we genuinely don't need inside this agent").
// Its former content is redistributed rather than deleted wholesale:
//   - agent rename (AgentTitle) + Schedule → moved here, into a new
//     "general" Configure tab (below) — set-once identity/behaviour config,
//     exactly what Configure already exists to hold. Persona (below) moved
//     out of that same tab a second time on 2026-08-19 — see the Profile
//     sheet comment further down for why it now has its own surface.
//   - the one-line status sentence (NowStrip) → deleted outright. It was a
//     fourth restatement of the same status the Properties panel's own
//     "Status" row, the agents rail's StatusDot, and the agents list
//     all already show — nobody has to "go looking for it" on a tab that no
//     longer exists when it is already one glance away everywhere else.
//   - the day-grouped activity feed → deleted outright, not folded into the
//     observation view. That view's own trace-based timeline
//     (agent_trace_service's tool/plan/browser/delegation/approval event
//     taxonomy) is a strictly richer account of "what this agent has
//     actually done" than the activity_ledger_events list Overview showed
//     — removing the shallower duplicate does not weaken it.
const TABS: { id: TabId; label: string; icon: LucideIcon }[] = [
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "persona", label: "Persona", icon: Bot },
  { id: "general", label: "General", icon: LayoutGrid },
  { id: "model", label: "Model", icon: Sparkles },
  { id: "skills", label: "Skills", icon: BookOpen },
  { id: "channels", label: "Channels", icon: Radio },
  // "Apps", not "Connectors". The founder named the right word: *"it's
  // clearly applications, MCP applications... name is clearly not tools."*
  // The ID is untouched on purpose — it is a route segment, in both
  // [tab]/page.tsx VALID_TABS whitelists and in live deep links; only what a
  // person reads changes.
  //
  // There is no "Tools" row here any more (2026-08-21). It listed every tool
  // with a per-tool "who may trigger this" control, which is the
  // tool-authority tier the founder killed outright — see
  // server_modules/authority_mandate_service.py for his words and for what
  // enforcement is left. Not hidden, deleted: the tab, its component, the
  // route id, and the mandate.audience_tools grant it wrote all went
  // together, so nothing is left to rebuild against.
  { id: "connectors", label: "Apps", icon: Plug },
  // feat/agent-context-grant: which PROJECTS this agent may reach. Named
  // "Context" and not "Projects" because the thing being granted is the
  // agent's context layer (tasks + documents), and because an agent no
  // longer LIVES in a project at all (CLAUDE.md, founder 2026-08-20).
  { id: "context", label: "Context", icon: FolderTree },
  { id: "capabilities", label: "Capabilities", icon: Wand2 },
  { id: "hardware", label: "Hardware", icon: Cpu },
  { id: "memory", label: "Memory", icon: Brain },
];

// Chat is the one thing you actually watch day to day — rendered directly,
// no tab strip, no composer (2026-08-20: the platform is not a chat
// product — see FleetAgentDetail's own top-of-file note). Everything else
// moved into the Configure sheet, reached from the header's "⋯" menu;
// nothing was deleted (founder: "the rest must not be deleted" — true of
// every section except Overview itself, which the founder separately asked
// removed outright), it just isn't equal-billing top-level nav anymore
// (UI-CONTRACT: "a surface must earn its place").
//
// Persona and Memory left Configure a second time on 2026-08-19 — see the
// Profile sheet comment below (~line 1420) for the founder's own words and
// the reasoning; PROFILE_TAB_IDS there is their new home, not
// CONFIGURE_GROUPS.

// Configure sheet groups — BRAIN (what it thinks with) / REACH (how it's
// reached, and what it can reach out to) / COMPUTE (what it runs on).
// Rendered via GroupedRail, the same component Settings uses, per the
// design's whole point: one rail component, two callers, not a rail built
// twice. Order within each group is the order the old flat tab strip had
// them in. "general" (identity/schedule, ex-Overview) stays in Brain — it's
// still "what/how it thinks", the same theme that already justified moving
// Model/Capabilities/Skills off the top strip, so it didn't need a fourth
// group invented just to hold it (UI-CONTRACT: "most configuration is set
// once and does not deserve equal billing").
const CONFIGURE_GROUPS: { id: string; label: string; tabs: TabId[] }[] = [
  { id: "brain", label: "Brain", tabs: ["general", "model", "capabilities", "skills"] },
  // "context" sits in Reach on purpose — this group is literally "how
  // it's reached, and WHAT IT CAN REACH OUT TO", and the project grant is
  // the largest thing an agent can reach out to.
  { id: "reach", label: "Reach", tabs: ["channels", "connectors", "context"] },
  { id: "compute", label: "Compute", tabs: ["hardware"] },
];
const CONFIGURE_TAB_IDS = new Set<TabId>(CONFIGURE_GROUPS.flatMap((g) => g.tabs));

// Profile sheet — Telegram's "tap the name in a chat header to see who
// you're talking to" pattern, applied to an agent. Founder, 2026-08-19,
// describing this exact product: "on top there is a profile of this
// specific chat, if I open it — for an agent, on top there would be a
// specific prompt like system prompt or whatever... And media and other
// files also going to be there — memory files is going to be just
// alongside media and others." Opened by tapping the identity block
// (avatar + name) in AgentDetailHeader while already on Chat — see that
// component's own comment for why the same tap does something different
// on every OTHER tab (returns to Chat, unchanged).
//
// Deliberately its own sheet, not folded into Configure: Configure holds
// set-once TECHNICAL configuration (Model, Channels, Hardware, ...);
// Profile holds "who this agent IS" — the two are different questions with
// different audiences, and Telegram's own metaphor (tap a name, not a
// settings gear) is specifically about identity, not configuration.
// PROFILE_TAB_IDS mirrors CONFIGURE_TAB_IDS's role exactly (a set of [tab]
// route segments that render inside a sheet instead of as a top-level tab)
// — pulled into its own pure module (agent-profile-shape.ts) rather than a
// bare Set literal here because which segments show (Persona is hidden for
// the workspace master — it has no per-agent persona field anything reads)
// is a real decision worth a test, not just a membership check.
const PROFILE_SEGMENT_DEFS: Record<AgentProfileSegmentId, { label: string; icon: LucideIcon }> = {
  persona: { label: "Persona", icon: Bot },
  memory: { label: "Memory & Files", icon: Brain },
};

// ── Cost period (Day/Week/Month) — shared by the Properties panel's "Cost
// today" stat and the Model tab's own identical row. summarize_usage
// (usage_events_repository.py) already pre-aggregates period=week|month —
// every caller here used to hardcode period=day, so there was never a way
// to see this week's or this month's spend without doing the arithmetic
// yourself. ─────────────────────────────────────────────────────────────────
type CostPeriod = "day" | "week" | "month";

/** The bucket key `summarize_usage` would date_trunc the CURRENT period down
 *  to, in the same UTC-midnight-anchored YYYY-MM-DD shape the API already
 *  returns for `buckets[].bucket` — so a plain string-slice comparison finds
 *  "this period"'s bucket the same way the pre-existing day-only logic did.
 *  Postgres date_trunc('week', …) anchors to the ISO week's Monday; month
 *  anchors to the 1st. */
function currentPeriodBucketKey(period: CostPeriod, now: Date): string {
  if (period === "month") {
    return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)).toISOString().slice(0, 10);
  }
  if (period === "week") {
    const utcDay = now.getUTCDay(); // 0=Sun..6=Sat
    const diffToMonday = utcDay === 0 ? -6 : 1 - utcDay;
    return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + diffToMonday))
      .toISOString()
      .slice(0, 10);
  }
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())).toISOString().slice(0, 10);
}

function costPeriodLabel(period: CostPeriod): string {
  if (period === "month") return "Cost this month";
  if (period === "week") return "Cost this week";
  return "Cost today";
}

const COST_PERIOD_OPTIONS: { id: CostPeriod; label: string }[] = [
  { id: "day", label: "Day" },
  { id: "week", label: "Week" },
  { id: "month", label: "Month" },
];

function CostPeriodToggle({ period, onChange }: { period: CostPeriod; onChange: (period: CostPeriod) => void }) {
  return (
    <div className="fleet-usage-period-toggle" role="tablist" aria-label="Cost period">
      {COST_PERIOD_OPTIONS.map((opt) => (
        <button
          key={opt.id}
          type="button"
          role="tab"
          aria-selected={period === opt.id}
          className={`fleet-usage-period-btn${period === opt.id ? " is-active" : ""}`}
          onClick={() => onChange(opt.id)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// ── Owner-only stop control, as a hook — extracted from what used to be a
// standalone `StopAgentControl` component rendered only on Work's header
// action slot. Now that ONE header (AgentDetailHeader below) is the frame
// for every tab, the trigger moved into that header's "⋯" menu, but the
// state machine (busy/error/confirm) and the confirm dialog markup are
// unchanged from the original — just no longer bound to a single tab's
// visible toolbar. kill_switch_gate.py enforces this server-side; this is
// purely the control surface.
function useAgentStopControl(
  workspaceId: string,
  agentId: string,
  agent: FleetAgent | null,
  onChanged?: () => void,
) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const stopped = agent?.stopped;

  const handleStop = useCallback(async () => {
    setBusy(true);
    setError(null);
    const result = await stopFleetAgent(workspaceId, agentId);
    setBusy(false);
    if (result.ok) {
      setConfirmOpen(false);
      onChanged?.();
    } else {
      setError(result.error || "Could not stop this agent.");
    }
  }, [workspaceId, agentId, onChanged]);

  const handleResume = useCallback(async () => {
    setBusy(true);
    setError(null);
    const result = await resumeFleetAgent(workspaceId, agentId);
    setBusy(false);
    if (result.ok) onChanged?.();
    else setError(result.error || "Could not resume this agent.");
  }, [workspaceId, agentId, onChanged]);

  // Close the confirm dialog on Escape (own the key so it doesn't bubble to
  // the page's own "Esc goes back" handler).
  useEffect(() => {
    if (!confirmOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) {
        e.stopPropagation();
        setConfirmOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [confirmOpen, busy]);

  return {
    stopped,
    busy,
    error,
    confirmOpen,
    openConfirm: () => { setError(null); setConfirmOpen(true); },
    closeConfirm: () => { if (!busy) setConfirmOpen(false); },
    handleStop,
    handleResume,
  };
}

// Stopping is a real, disruptive action (the agent goes dark on every
// channel until resumed) so it stays gated behind a confirm dialog — a
// blurred/dimmed backdrop plus a small centered card, Cancel (neutral) +
// Stop agent (.fleet-btn--danger, solid red). Unchanged markup from the
// original standalone control, just rendered from the header now.
function StopAgentConfirmDialog({
  agentLabel,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  agentLabel: string;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fleet-detail-backdrop" onClick={onCancel}>
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="fleet-stop-agent-title"
        className="fleet-small-dialog"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="fleet-small-dialog-header">
          <span id="fleet-stop-agent-title" className="fleet-title">Stop agent</span>
        </div>
        <div className="fleet-small-dialog-body">
          <p style={{ margin: 0, fontSize: 13, color: "var(--text-primary)", lineHeight: 1.5 }}>
            Are you sure you want to stop <strong>{agentLabel}</strong>?
            It stops responding on every channel until you resume it.
          </p>
          {error && (
            <p style={{ margin: 0, fontSize: 12, color: "var(--offline-text)" }}>{error}</p>
          )}
        </div>
        <div className="fleet-small-dialog-footer">
          <button type="button" className="fleet-btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="fleet-btn fleet-btn--danger" onClick={onConfirm} disabled={busy}>
            {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Square size={14} strokeWidth={1.75} />}
            {busy ? "Stopping…" : "Stop agent"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Agent detail header — the ONE minimal bar every tab renders, instead of
// a breadcrumb + Chat|Work tabs + Configure button + a duplicate "Chat with
// this agent" button all visible above the content at once (founder: "main
// content page must be empty and just super clean... I don't want it to be
// developer tool"). Telegram's own shape: back, who you're talking to, one
// overflow control — never zero wayfinding. Back is always a real <Link> to
// the project (the same destination the old breadcrumb's parent crumb
// pointed at), never router.back() and never a second, tab-dependent
// destination — CLAUDE.md's "only ONE surface may be the picker at a time"
// extends here to "only one back path", so every tab shares the identical
// backHref rather than each inventing its own idea of "up". Sigil +
// StatusDot are the same pair AgentsList/ProjectAgentsRail already use for
// "who is this and is it up".
//
// The identity block always opens this agent's Profile now — 2026-08-20,
// following the founder's "not a chat product" decision (see
// FleetAgentDetail's own top-of-file note): there is no separate "Chat"
// destination to return to any more, so the one destination behind an
// agent's name is who/what it is, exactly like tapping a contact's name in
// Telegram. Every tab (the observation view included) shares this.
//
// THE "WORK" BUTTON IS GONE — founder, 2026-08-19/20, on the platform no
// longer being a messaging surface: "I don't really like it, I think it
// must go." Chat and Work used to be two competing header controls (one for
// the owner's own private test chat, one for watching real conversations);
// with the composer removed there is only one thing left to look at, so
// there is only one thing in the header pointing at it — the identity link
// itself, exactly as Telegram's own chat header has no second "go to this
// conversation" button beside the contact name. Configure, Stop/Resume, and
// opening Properties (danger-styled and separated by a divider — a real but
// occasionally-needed control that must never sit beside anything primary)
// stay in the "⋯" menu. Reuses DocumentDetailView's own "⋯" menu shell
// (.fleet-list-row-menu-wrap/.fleet-list-row-menu/-item, defined once in
// fleet-theme.css) rather than inventing a fourth dropdown implementation.
function AgentDetailHeader({
  workspaceId,
  agentId,
  agent,
  agentLoadPhase,
  agentLabel,
  statusTone,
  statusLabel,
  sheetOpen,
  backHref,
  backLabel,
  profileHref,
  configureHref,
  onOpenProperties,
  onAgentChanged,
  onDeleted,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  /** "loading"/"failed" render a neutral placeholder instead of the fixed
   *  agentLabel/statusLabel below — see FleetAgentDetail's own computation
   *  of this for why those two facts are not the same thing. */
  agentLoadPhase: "ready" | "loading" | "failed" | "notFound";
  agentLabel: string;
  statusTone: AgentStatusTone;
  statusLabel: string;
  sheetOpen: boolean;
  backHref: string;
  backLabel: string;
  profileHref: string;
  configureHref: string;
  onOpenProperties: () => void;
  onAgentChanged?: () => void;
  /** Where to go once this agent no longer exists. Called only after the
   *  server has CONFIRMED the delete — never optimistically. */
  onDeleted: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const stopControl = useAgentStopControl(workspaceId, agentId, agent, onAgentChanged);

  // ── Delete (restored 2026-08-21) ────────────────────────────────────────
  // The route and fleet_tools.fleet_delete_agent never moved; the 2026-08-20
  // agents-page redesign stopped importing AgentsList.tsx, which held the
  // only caller, so agents could be created and never removed. The rule and
  // the request live in agent-delete.ts, the dialog in AgentDeleteDialog.tsx
  // — shared, so reviving this did not make a second delete path.
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const deletable = canDeleteAgent(agent);

  const closeDelete = useCallback(() => {
    if (deleteBusy) return;
    setDeleteOpen(false);
    setDeleteError(null);
  }, [deleteBusy]);

  const confirmDelete = useCallback(async () => {
    if (deleteBusy) return;
    setDeleteBusy(true);
    setDeleteError(null);
    const outcome = await deleteFleetAgent(workspaceId, agentId, agentLabel);
    setDeleteBusy(false);
    // Nothing moves until the server has confirmed. A `refused` and an
    // `unconfirmed` both keep the dialog open carrying their own, different
    // sentence — collapsing them would send someone to retry something
    // already done, or give up on something that worked.
    if (outcome.status === "deleted") {
      setDeleteOpen(false);
      onDeleted();
      return;
    }
    setDeleteError(outcome.message);
  }, [agentId, agentLabel, deleteBusy, onDeleted, workspaceId]);

  useEffect(() => {
    if (!menuOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      if (menuRef.current?.contains(e.target as Node)) return;
      setMenuOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // Stopped so the page's own Escape handler (browser back) doesn't
        // also fire on the same keypress that just closed this menu.
        e.stopPropagation();
        setMenuOpen(false);
      }
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown, true);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown, true);
    };
  }, [menuOpen]);

  const identityInner =
    agentLoadPhase === "loading" ? (
      <>
        <span className="fleet-chat-header-avatar">
          <div className="fleet-skeleton-bar" style={{ width: 26, height: 26, borderRadius: 999 }} aria-hidden="true" />
        </span>
        <span className="fleet-chat-header-identity" aria-busy="true" aria-label="Loading agent">
          <span className="fleet-chat-header-name">
            <span className="fleet-skeleton-bar" style={{ width: 120, height: 13 }} />
          </span>
          <span className="fleet-chat-header-status">
            <span className="fleet-skeleton-bar" style={{ width: 70, height: 11, marginTop: 2 }} />
          </span>
        </span>
      </>
    ) : agentLoadPhase === "failed" ? (
      <>
        <span className="fleet-chat-header-avatar">
          <AgentSigil seed={agentId} size={26} />
        </span>
        <span className="fleet-chat-header-identity">
          <span className="fleet-chat-header-name">Couldn’t load this agent</span>
          <span className="fleet-chat-header-status">
            <StatusDot tone="error" size={6} />
            Check your connection — it’ll keep retrying
          </span>
        </span>
      </>
    ) : (
      <>
        <span className="fleet-chat-header-avatar">
          <AgentSigil seed={agentId} size={26} />
        </span>
        <span className="fleet-chat-header-identity">
          <span className="fleet-chat-header-name">{agentLabel}</span>
          <span className="fleet-chat-header-status">
            <StatusDot tone={statusTone} size={6} />
            {statusLabel}
          </span>
        </span>
      </>
    );

  return (
    <div className="fleet-chat-header">
      <Link href={backHref} className="fleet-icon-btn fleet-chat-header-back" aria-label={`Back to ${backLabel}`}>
        <ArrowLeft size={16} strokeWidth={1.75} />
      </Link>
      {/* Tap the name to see this agent's Profile — Telegram's "tap the
          name" pattern (see the block comment above). There is no other
          destination behind it any more: the observation view below IS the
          front door, on every tab that isn't already a sheet over it. */}
      <Link
        href={profileHref}
        replace
        className="fleet-chat-header-identity-wrap fleet-chat-header-identity-wrap--link"
        aria-label={`Open ${agentLabel}'s profile`}
      >
        {identityInner}
      </Link>
      <div className="fleet-list-row-menu-wrap fleet-chat-header-menu" ref={menuRef}>
        <button
          type="button"
          className="fleet-icon-btn"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="More"
          onClick={() => setMenuOpen((v) => !v)}
        >
          <MoreHorizontal size={16} strokeWidth={1.75} />
        </button>
        {menuOpen ? (
          <div className="fleet-list-row-menu" role="menu">
            <button
              type="button"
              role="menuitem"
              className="fleet-list-row-menu-item"
              onClick={() => {
                setMenuOpen(false);
                onOpenProperties();
              }}
            >
              Properties
            </button>
            {!sheetOpen && (
              <Link
                href={configureHref}
                replace
                role="menuitem"
                className="fleet-list-row-menu-item"
                onClick={() => setMenuOpen(false)}
              >
                Configure
              </Link>
            )}
            <div className="fleet-list-row-menu-divider" />
            {stopControl.stopped?.active ? (
              <button
                type="button"
                role="menuitem"
                className="fleet-list-row-menu-item"
                disabled={stopControl.busy}
                onClick={() => {
                  setMenuOpen(false);
                  stopControl.handleResume();
                }}
              >
                <span>Resume agent</span>
                {stopControl.stopped.stopped_by_label && (
                  <span style={{ display: "block", fontSize: 11, color: "var(--text-muted)" }}>
                    Stopped by {stopControl.stopped.stopped_by_label}
                  </span>
                )}
              </button>
            ) : (
              <button
                type="button"
                role="menuitem"
                className="fleet-list-row-menu-item fleet-list-row-menu-item--danger"
                onClick={() => {
                  setMenuOpen(false);
                  stopControl.openConfirm();
                }}
              >
                Stop agent
              </button>
            )}
            {/* Not rendered at all for the workspace operator —
                fleet_delete_agent refuses it outright, and a menu item whose
                only possible outcome is an error is a dead control. */}
            {deletable && (
              <button
                type="button"
                role="menuitem"
                className="fleet-list-row-menu-item fleet-list-row-menu-item--danger"
                onClick={() => {
                  setMenuOpen(false);
                  setDeleteError(null);
                  setDeleteOpen(true);
                }}
              >
                Delete agent
              </button>
            )}
          </div>
        ) : null}
      </div>
      {stopControl.confirmOpen && (
        <StopAgentConfirmDialog
          agentLabel={agentLabel}
          busy={stopControl.busy}
          error={stopControl.error}
          onCancel={stopControl.closeConfirm}
          onConfirm={stopControl.handleStop}
        />
      )}
      {deleteOpen && (
        <AgentDeleteDialog
          agentName={agentLabel}
          busy={deleteBusy}
          error={deleteError}
          onCancel={closeDelete}
          onConfirm={confirmDelete}
        />
      )}
    </div>
  );
}

// ── Properties panel's cost breakdown — one row per TIER, not per raw row ──
//
// `costMatrix` is raw backend granularity: usage_events_repository.
// summarize_usage groups by (agent_install_id, provider, model, mode), so a
// platform-credit agent whose usage was ever recorded under more than one
// literal `mode` string (e.g. the retired "empyralis_credits"/"empyralis"
// tokens before "platform_credits" became the one canonical value — see
// that file's _USAGE_MODE_TO_PAYER) or more than one literal `model` id
// (the DeepSeek retirement aliases — platformCreditsTierLabel's own doc)
// comes back as SEVERAL raw rows for what is, to the person looking at this
// panel, one thing: "this agent's Pro-tier usage." A per-row map (checking
// `row.payer === "platform_credits"` and rendering each row on its own)
// fixed the raw-vendor-string leak for any individual row, but never merged
// rows — an agent whose history crossed that mode rename still showed BOTH
// "deepseek · deepseek-reasoner $0.0043" (an unattributed row) and
// "Pro $0.0029" (an attributed one) as two separate lines. Grouped below
// instead: every row that resolves to a platform-credit tier collapses into
// ONE row per tier (Flash/Pro), tokens and cost summed across every raw row
// that contributed to it.
const PLATFORM_CREDITS_MODE_ALIASES = new Set(["platform_credits", "empyralis_credits", "empyralis"]);

/** Whether a matrix row is platform-credit usage — checked against `payer`
 *  first (usage_events_repository.summarize_usage already recomputes this
 *  fresh from the raw `mode` column on every read, via the exact alias set
 *  mirrored in PLATFORM_CREDITS_MODE_ALIASES above), with a fallback to the
 *  raw `mode` itself only when `payer` came back missing/"unknown" — belt
 *  and braces against a backend build that hasn't picked up an alias this
 *  file already knows about, never a guess beyond these three literal
 *  tokens. A row whose mode is genuinely something else (blank, or a real
 *  BYOK/local/subscription mode) stays "unknown"/its own payer and is NOT
 *  folded into a tier — same honesty rule the backend's own test enforces
 *  (test_empty_or_missing_mode_is_unknown_not_silently_platform): an
 *  unattributable row keeps its raw vendor/model name rather than being
 *  silently merged into someone else's tier total. */
function isPlatformCreditsUsageRow(row: UsageMatrixRow): boolean {
  if (row.payer === "platform_credits") return true;
  if (row.payer && row.payer !== "unknown") return false;
  return PLATFORM_CREDITS_MODE_ALIASES.has(String(row.mode || "").trim().toLowerCase());
}

type CostDisplayRow = { key: string; label: string; hint: string; usd_cost: number; pricing_known: boolean };

/** Collapses the raw cost matrix into the rows the Properties panel actually
 *  renders: platform-credit rows merge into one entry per tier (Flash/Pro),
 *  everything else (BYOK/local/subscription, or a row that truly can't be
 *  attributed) passes through one-for-one — real, distinct information
 *  about who paid, never merged with anyone else's. Sorted by cost
 *  descending and capped at 5, same as the panel showed before grouping. */
function buildCostDisplayRows(matrix: UsageMatrixRow[]): CostDisplayRow[] {
  const tierTotals = new Map<
    string,
    { tokens_in: number; tokens_out: number; tokens_cache_read: number; usd_cost: number; pricing_known: boolean }
  >();
  const otherRows: CostDisplayRow[] = [];
  for (const row of matrix) {
    // Cache read tokens only — cache creation is rare enough (one write per
    // new prompt prefix, many reads after) that surfacing both would crowd
    // this single hint line for little signal. Omitted entirely (not
    // "0 cached") for a row recorded before this dimension existed, or by
    // an engine that never reports it — the field is optional on
    // UsageMatrixRow for exactly that.
    if (isPlatformCreditsUsageRow(row)) {
      const tier = platformCreditsTierLabel(row.model); // "Flash" | "Pro"
      const acc = tierTotals.get(tier) || { tokens_in: 0, tokens_out: 0, tokens_cache_read: 0, usd_cost: 0, pricing_known: true };
      acc.tokens_in += row.tokens_in;
      acc.tokens_out += row.tokens_out;
      acc.tokens_cache_read += row.tokens_cache_read || 0;
      acc.usd_cost += row.usd_cost;
      acc.pricing_known = acc.pricing_known && row.pricing_known;
      tierTotals.set(tier, acc);
    } else {
      const cacheHint = row.tokens_cache_read ? ` · ${formatNumber(row.tokens_cache_read)} cached` : "";
      otherRows.push({
        key: `${row.provider}:${row.model}:${row.mode}`,
        label: [row.provider, row.model].filter(Boolean).join(" · ") || "Unknown model",
        hint: `${usagePayerLabel(row.payer)} · ${formatNumber(row.tokens_in)} in / ${formatNumber(row.tokens_out)} out${cacheHint}`,
        usd_cost: row.usd_cost,
        pricing_known: row.pricing_known,
      });
    }
  }
  const tierRows: CostDisplayRow[] = Array.from(tierTotals.entries()).map(([tier, acc]) => {
    const cacheHint = acc.tokens_cache_read ? ` · ${formatNumber(acc.tokens_cache_read)} cached` : "";
    return {
      key: `platform_credits:${tier}`,
      label: tier,
      hint: `${usagePayerLabel("platform_credits")} · ${formatNumber(acc.tokens_in)} in / ${formatNumber(acc.tokens_out)} out${cacheHint}`,
      usd_cost: acc.usd_cost,
      pricing_known: acc.pricing_known,
    };
  });
  return [...tierRows, ...otherRows].sort((a, b) => b.usd_cost - a.usd_cost).slice(0, 5);
}

/**
 * Agent detail — a routed page (a permanent properties panel that never
 * reflows the content column, Configure/Profile as overlay sheets). Every
 * tab has real data or an intentional empty state with a working next
 * action. No stubs.
 *
 * READ-ONLY OBSERVATION, NOT A CHAT PRODUCT — 2026-08-20, founder: "messaging
 * would never be done inside this platform. I'm strictly going to prohibit
 * that and nobody is going to use that... you want to speak and have an
 * agent, go set it up, go to Telegram and speak with the agent inside that
 * channel. We are not going to try to be a channel." And on what's left:
 * "we will only show what kind of messages had been going from which
 * channel, and agent's output and its tools and other things in the
 * process, but you wouldn't be able to speak with the agent."
 *
 * So there is no composer anywhere on this page, and no code path that
 * originates a turn from here. What used to be two competing surfaces —
 * Chat (the owner's own private test composer, AgentChat.tsx) and Work (a
 * read-only activity view) — is now ONE: tabs/WorkTab.tsx, rendered for
 * both the "chat" and legacy "work" tab ids (see the TabId comment below).
 * It shows, per real conversation across every channel: which channel a
 * message arrived from, the agent's own output, and its tool calls/plan
 * steps — LIVE, via SSE, while a turn is still running (that's the reason
 * to open this page at all; removing the composer must never mean losing
 * the ability to watch work happen). Control happens over the channel
 * itself, via the `/command` surface `agent_command_dispatcher` already
 * dispatches on every personal channel — never from a screen here.
 *
 * AgentChat.tsx and its composer are UNCHANGED and still real code — they
 * remain Sage's own workspace-level "Ask AI" console (SageLauncher.tsx),
 * a deliberately different, per-user product surface this pass did not
 * touch. Only THIS agent detail page's Chat tab lost its composer.
 */
export function FleetAgentDetail({
  workspaceId,
  agentId,
  agent,
  agentsLoading,
  agentsError,
  projectId,
  projectName,
  backHref,
  backLabel,
  onChat,
  initialTab,
  onTabChange,
  onRenamed,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  /** From the caller's own useFleetAgents(workspaceId) — `agent` is `null`
   *  both while the fetch is still in flight AND once it has genuinely
   *  failed (a 503, a dropped connection), and those are different facts
   *  (CLAUDE.md's outcome-honesty law: "empty" and "I could not load this"
   *  and "haven't found out yet" may never share one rendering). Before
   *  this, the header rendered the confident, fabricated "Unnamed agent" /
   *  "Not deployed" for BOTH — a definite claim about deployment state made
   *  before any data had arrived. Optional/defaulted so a caller that never
   *  passes them (none exist today, but a future test fixture might) keeps
   *  the same "not yet found" fallback this page already had. */
  agentsLoading?: boolean;
  agentsError?: string | null;
  /** The URL's own projectId segment — available on first paint,
   *  independent of the agents fetch. Used to build the chat header's back
   *  link (AgentDetailHeader below), same "resolved from the route, never
   *  from a still-loading fetch" contract projectName already follows. */
  projectId: string;
  /** Resolved project display name — passed by the routed page (from the URL's
   *  projectId, so it's available on first paint independent of the agents
   *  fetch). undefined = still resolving (shows a loading placeholder); pass
   *  "—" explicitly when there genuinely is no project (e.g. Sage). NEVER
   *  fall back to the raw project_id here — that's the "raw ids on first
   *  paint" bug. */
  projectName?: string;
  /** Where the header's "‹" goes, and what it is called. Defaults to this
   *  agent's PROJECT — correct for the project-scoped route, which is the
   *  door you came through there.
   *
   *  The workspace-level route (/w/{ws}/agents/{id}/…) overrides both,
   *  because "up" from there is the agents list, not a project the reader
   *  may never have opened. That is what Breadcrumbs.tsx already derives
   *  for this route (Agents › {agent}), so the two agreeing is the fix, not
   *  a new opinion — and below 768px it stops being cosmetic: the list pane
   *  collapses away (agents-split-pane.ts) and this arrow becomes the only
   *  way back to it. */
  backHref?: string;
  backLabel?: string;
  onChat: (agentId: string) => void;
  initialTab?: TabId;
  onTabChange?: (tab: TabId) => void;
  /** Called after a successful inline rename (Overview title) so the caller
   *  can refresh whatever list/breadcrumb sources agent.label. */
  onRenamed?: () => void;
}) {
  // The URL's {tab} segment is the single source of truth for which tab
  // renders — read directly from `initialTab` (resolved by the routed
  // [tab]/page.tsx from its own route params) on every render, instead of
  // copying it into a one-shot useState seed. That copy was the actual
  // bug: a re-render mid-navigation (rapid tab clicks, or clicking a
  // second tab while the first was still loading) could see `initialTab`
  // transiently unresolved, and a sync effect here plus the parent's own
  // unresolved-defaults-to-"chat" coercion combined to silently stomp
  // whatever tab the user had just clicked back to Chat. Deriving instead
  // of storing means there is no local copy to desync — whatever the URL
  // says is what renders. [tab]/page.tsx now passes `undefined` (never a
  // manufactured "chat") while a navigation is still resolving, so the
  // "chat" fallback below only ever fires for a genuine first paint before
  // routing has resolved at all — the agent's front door, matching
  // .../agents/[agentId]/page.tsx's own no-tab redirect.
  const activeTab: TabId = initialTab || "chat";
  // Properties panel — a floating overlay (FleetRightPanel), the SAME
  // component and behaviour the project/agents LIST pages use for their own
  // Properties toggle (FleetToolbar's panelOpen), reused directly rather
  // than diverging into a second implementation. Closed by default, opened
  // via the single toggle in the tab bar below, for every tab — never a
  // permanent column that reserves width or reflows the page (founder:
  // "on the right side it must not be something that is merged on the user
  // interface, it just should be something that appears just as a node").
  // This used to be two separate states — a permanent, localStorage-
  // persisted "rail" collapse for desktop/tablet, plus a second one-off
  // overlay just for the Chat tab's mobile layout — because the column had
  // nowhere to go on a narrow Chat screen. Collapsing both onto one overlay
  // used on every tab/width removes that special case entirely: there is no
  // longer a column for Chat's mobile layout to have "no room" for.
  const [propertiesOpen, setPropertiesOpen] = useState(false);
  // No composer, no owner-only test thread — the platform is not a channel
  // (founder, 2026-08-19/20: "messaging would never be done inside this
  // platform... go to Telegram and speak with the agent inside that
  // channel"). `threadId` here is just an OBSERVER of whichever real
  // conversation WorkTab's own left-hand list currently has selected
  // (WorkTab owns the selection; it calls this back on every change) — it
  // exists only so the Profile sheet's Files pane (below) can show the
  // files attached to the conversation someone is actually looking at,
  // instead of a fixed id nothing ever selected. null until WorkTab has
  // loaded at least one real conversation to select.
  const [threadId, setThreadId] = useState<string | null>(null);
  const { channels, loading: channelsLoading, refresh: refreshChannels, telegramBotConnected, slackChannelBinding } = useFleetAgentChannels(workspaceId, agentId);
  const { connectors, loading: connectorsLoading } = useFleetAgentConnectors(workspaceId, agentId);
  const [costPeriod, setCostPeriod] = useState<CostPeriod>("day");
  const [costToday, setCostToday] = useState<number | null>(null);
  const [costBuckets, setCostBuckets] = useState<UsageBucket[]>([]);
  const [costMatrix, setCostMatrix] = useState<UsageMatrixRow[]>([]);

  useEffect(() => {
    let cancelled = false;
    fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=agent&id=${encodeURIComponent(agentId)}&period=${costPeriod}`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d) return;
        // `totals` isn't date-filtered by the backend (summarize_usage only
        // date_trunc's `buckets`) — it's an all-time sum, so using it here
        // would silently mislabel all-time spend as "today's"/"this week's"/
        // "this month's". Match the bucket the CURRENT period truncates to
        // instead; no match (nothing billed yet this period) correctly reads
        // as $0, not all-time spend.
        if (Array.isArray(d.buckets)) {
          setCostBuckets(d.buckets);
          const periodKey = currentPeriodBucketKey(costPeriod, new Date());
          const currentBucket = d.buckets.find((b: UsageBucket) => String(b.bucket || "").slice(0, 10) === periodKey);
          setCostToday(Number(currentBucket?.usd_cost ?? 0));
        }
        if (Array.isArray(d.matrix)) setCostMatrix(d.matrix);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId, agentId, costPeriod]);

  const connectedChannelRows = channels.filter((c) => isChannelConnected(c, slackChannelBinding, telegramBotConnected));
  const connectedChannels = connectedChannelRows.length;
  const connectedConnectors = connectors.filter((c: any) => c?.connected).length;
  // Lives in the permanent properties column now, so it's computed once
  // here rather than per-tab — every tab shows the same placement/role,
  // not just Overview. Brain placement (model_config.gateway_binding, for
  // cli_subscription/local agents) wins over tool-hardware placement
  // (hardware_access + preferred_gateway_id) — see resolveHardwarePlacement.
  // Never runtime_target, which stays pinned to a runtime_profile FK real
  // Fleet agents never update (see docs/PLATFORM-MAP.md Part 22, Hardware).
  const { gateways } = useWorkspaceGateways(workspaceId);
  const placement = resolveHardwarePlacement(agent?.hardware_access, agent?.preferred_gateway_id, gateways, agent?.model_config);
  // Separate honest facts for the Sessions panel's top region (2026-08-14,
  // founder: "this agent is online, this MacBook is online, the gateway is
  // online, the OpenClaw side is online for channels, and which channels
  // this agent is in" — five distinct claims, never collapsed into one
  // light). "This agent" is the existing Status row below (deriveAgentStatus
  // — the BRAIN's honest turn-readiness). Everything from here down is about
  // the MACHINE this agent's tools run on, computed from the SAME resolution
  // resolveHardwarePlacement already does (brain-bound gateway_binding wins
  // over tool hardware_access — see that function's own doc comment) so this
  // can never name a different box than Placement does.
  const boundGatewayId = (() => {
    if (hardwarePlacementIsBrainBound(agent?.model_config)) {
      return String(agent?.model_config?.gateway_binding || "").trim();
    }
    return String(agent?.preferred_gateway_id || "").trim();
  })();
  const boundGateway = boundGatewayId ? gateways.find((g) => gatewayId(g) === boundGatewayId) || null : null;
  // "This computer" / "Gateway" split — connectionPresentation already
  // distinguishes raw reachability from execution readiness in its LABEL
  // ("Online — tools unavailable" for execution_blocked, per the hardware
  // badge honesty fix this file's own CLAUDE.md documents), but a single
  // string is still one light. boundGateway is null both when nothing is
  // paired (cloud agent — nothing to report, row omitted entirely below)
  // and when a preferred/bound id no longer resolves to a real registration
  // (disconnected/deleted box) — connectionPresentation(null) is never
  // called; the row is simply absent rather than guessing a tone for a box
  // that isn't there.
  const computerPresentation = boundGateway ? connectionPresentation(boundGateway) : null;
  const rawGatewayConnectionStatus = boundGateway
    ? `${boundGateway.connection_status || boundGateway.status || ""}`.toLowerCase()
    : "";
  // OpenClaw is a per-CHANNEL surface (useGatewayPersonalChannelSurfaces),
  // not a single flag on the gateway — only fetched when this agent both has
  // a bound box AND has at least one gateway-requiring channel connected, so
  // an agent with no local-bridge/OpenClaw channel never pays for a poll
  // whose answer it has no row to show. `items.length === 0` while `loading`
  // is genuinely unknown (nothing observed yet); once loaded, "connected" is
  // true only when at least one surface reports it — anything else (no
  // surfaces, none connected) reads as "not confirmed" rather than a
  // fabricated red, matching this hook's own "never invent a connected
  // state" doctrine (see its module comment).
  const gatewayRequiringChannelConnected = channels.some(
    (c) => c.requiresGateway && isChannelConnected(c, slackChannelBinding, telegramBotConnected),
  );
  const openClawSurfaces = useGatewayPersonalChannelSurfaces(
    boundGateway && gatewayRequiringChannelConnected ? boundGatewayId : null,
  );
  const openClawConnected = openClawSurfaces.items.some((item) => item.health?.connected === true);
  // deriveAgentStatus (needs `gateways`, hence computed here rather than up
  // top): a cli_subscription agent whose bound CLI isn't signed in reads
  // "Needs sign-in", never a false "Ready" — the header must never claim an
  // agent is runnable when its brain can't produce a turn.
  //
  // agentDisplayStatus wraps that with the SAME "an in-progress assigned
  // task means Working" enrichment AgentCards.tsx/AgentsBoard.tsx/
  // AgentsGroupedList.tsx already apply (agent-view-options.ts) — this page
  // used to call the bare deriveAgentStatus, which is why the grid one click
  // away could read "Working" while this header, for the same agent at the
  // same moment, read "Ready". "Working" has ONE definition in this product
  // (CLAUDE.md) and this is the last surface that had not adopted it.
  // useFleetWorkspaceTasks is the SAME shared, workspace-wide cache
  // PrimaryRail/Inbox/My work already poll — reusing it here costs no extra
  // request, per that hook's own module comment.
  const { tasks: workspaceTasksForStatus } = useFleetWorkspaceTasks(workspaceId);
  const thisAgentTasks = useMemo(
    () => groupTasksByAgent(workspaceTasksForStatus).get(agentId) || [],
    [workspaceTasksForStatus, agentId],
  );
  const status = agentDisplayStatus(agent ?? {}, gateways, thisAgentTasks);
  // Three facts, never collapsed into the two the header used to render
  // ("Unnamed agent" / "Not deployed" for both a genuine empty result and a
  // fetch that has not settled yet). `agent` truthy always wins — a stale
  // "loading" from a slower sibling poll must never blank out data already
  // in hand. Otherwise: still in flight -> "loading" (loading can flip back
  // true on the 30s poll retry even after a prior failure, and that retry
  // deserves the neutral treatment too, not a repeated error). Settled with
  // nothing and an error on record -> "failed". Settled with nothing and no
  // error -> "notFound" (an id that genuinely doesn't resolve — the
  // pre-existing fallback text below is kept for this one, rare case).
  const agentLoadPhase: "ready" | "loading" | "failed" | "notFound" = agent
    ? "ready"
    : agentsLoading
      ? "loading"
      : agentsError
        ? "failed"
        : "notFound";
  // Role itself no longer has a Properties-panel row: every user-created
  // agent is seeded role="specialist" (see fleet_tools.py's create path)
  // and there's no UI to change it, so the row only ever read "Specialist"
  // — real backend concept (it gates fleet_tools.py's 5 platform-management
  // tools), just never-varying, unset-by-the-user information for the one
  // panel that's supposed to be this agent's day-to-day facts. isMaster is
  // still real and load-bearing below (it's what makes the workspace's one
  // operator/Sage agent skip the "Tools" row and its delete control).
  const isMaster = (agent?.role || "agent") === "operator";

  // Tab clicks just navigate — activeTab above already tracks initialTab
  // directly, so there's no local state to update here; the URL round-trips
  // back through [tab]/page.tsx's params and the new tab renders from that.
  const selectTab = useCallback(
    (tab: TabId) => {
      onTabChange?.(tab);
    },
    [onTabChange],
  );

  // Whether the Configure sheet is open — derived from the URL's own
  // activeTab on every render, same rule as activeTab itself (see the
  // comment above it, ~line 296): no local open/closed state to desync.
  // Cold-loading /agents/{id}/channels lands here with initialTab="channels"
  // already resolved, so this is true on first paint, not after a second
  // click — the sheet opens on the right section immediately.
  const sheetOpen = CONFIGURE_TAB_IDS.has(activeTab);
  // Same rule, for the Profile sheet — isProfileTab (agent-profile-shape.ts)
  // rather than a second inline Set, so the membership check and the
  // segments actually rendered below can never disagree.
  const profileOpen = isProfileTab(activeTab);

  // This route is always .../agents/[agentId]/[tab] (the only place this
  // component is mounted) — the last path segment IS the tab id, so a link
  // to a different tab is a plain string-swap of that last segment, no
  // projectId prop-threading needed to reconstruct
  // /w/{ws}/projects/{proj}/agents/{id}/{tab} from scratch. Used for both
  // the two top tabs and the Configure sheet's GroupedRail items — real
  // hrefs so cmd-click/middle-click open a new tab, per the "primary
  // navigation is real links" rule GroupedRail.tsx itself already follows.
  const pathname = usePathname();
  const tabHref = useCallback((tab: TabId) => pathname.replace(/\/[^/]+$/, `/${tab}`), [pathname]);

  // ── "This agent isn't set up yet" (2026-08-21, corrected same day) ───────
  // Setup is a SEQUENCE now, and it lives inside the creation surface
  // (AgentCreateCard/agent-create-wizard.ts): Identity → Model → Channel →
  // Apps. The founder rejected the row of optional buttons that used to sit
  // here — *"it acts like a button, not step-by-step... if you want this if
  // you want that — I don't want to have that."*
  //
  // What survives is the ONE case the sequence can't cover: the Channel step
  // is skippable on purpose, and a skipped one leaves an agent nobody can
  // reach. So this renders a SINGLE control — the next thing that unblocks
  // it — never a menu. The rule lives in agent-setup-steps.ts (pure +
  // tested); everything here is the rendering of its answer.
  //
  // The band is gated on the agent being UNREACHABLE (zero channels), not on
  // "something is outstanding": with chat gone from the platform, a channel
  // is the only way anybody can talk to an agent, and a finished cloud-only
  // agent with no connectors must never be nagged. `channelsLoading` /
  // `connectorsLoading` are passed through so "nothing is connected" and "I
  // have not asked yet" never share a screen — both fetches return [] in
  // either state.
  const setupStep = useMemo(
    () =>
      agentSetupNextStep({
        channelsKnown: !channelsLoading,
        channels,
        connectedChannelCount: connectedChannels,
        hardwareAccess: String(agent?.hardware_access || ""),
        // Bound BY CONFIGURATION, not by resolution: `gateways` loads
        // asynchronously, so asking whether the box resolves right now would
        // flash "Pick its computer" at an agent that already has one.
        hardwareBound: Boolean(boundGatewayId),
        connectorsKnown: !connectorsLoading,
        connectedConnectorCount: connectedConnectors,
        hasProject: Boolean(String(agent?.project_id || "").trim()),
      }),
    [
      channelsLoading,
      channels,
      connectedChannels,
      agent?.hardware_access,
      agent?.project_id,
      boundGatewayId,
      connectorsLoading,
      connectedConnectors,
    ],
  );

  // Esc returns to wherever the user came from (browser back) — not a
  // hardcoded destination, so the flat /agents list, a project's list, or
  // any other referrer all restore correctly. Skipped while typing (so it
  // never discards an in-progress edit) and while a nested dialog is
  // capturing the key itself (see ChannelsTab's banner, which stops
  // propagation before this ever sees it).
  const router = useRouter();
  const onPageKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const el = document.activeElement as HTMLElement | null;
      const isTyping = Boolean(el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable));
      if (isTyping) return;
      router.back();
    },
    [router],
  );

  // The old top tab strip's own focus-ring/scroll-into-view plumbing
  // (activeTabRef) is gone with the strip itself — AgentDetailHeader has no
  // equivalent "which pill is active" control to manage focus for; its Work
  // button and back link are ordinary links, and DELIBERATELY get no
  // mount-time focus() call for the same :focus-visible reason this file
  // used to document at length here (founder: "I always have this purple
  // thing on the ui" — programmatic focus satisfies :focus-visible exactly
  // like a real keypress, so calling it on mount drew a permanent ring for
  // every mouse user). The SPA-landing-spot need that focus call was
  // reaching for belongs on the page's own HEADING instead — TaskDetailView/
  // DocumentDetailView's `headingRef` + `tabIndex={-1}` pattern —  which is
  // Breadcrumbs.tsx's job when this route grows one, not this file's.

  // Configure sheet's own mount focus — the cold-load-into-a-grouped-tab
  // case (/agents/{id}/tools) needs somewhere deliberate to land focus,
  const sheetRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (sheetOpen) sheetRef.current?.focus();
  }, [sheetOpen]);
  // Same for the Profile sheet.
  const profileSheetRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (profileOpen) profileSheetRef.current?.focus();
  }, [profileOpen]);

  // Content for the Properties overlay below (FleetRightPanel) — same
  // PanelSection/PanelRow shape the project page's own Properties panel
  // renders, so the two surfaces can never drift apart in markup even
  // though each computes its own values.
  const propertiesContent = (
    <PanelSection title="Properties">
      <PanelRow label="Status" value={<StatusChip tone={status.tone} label={status.label} />} />
      <PanelRow label="Placement" value={placement.label} tone={HARDWARE_PLACEMENT_PANEL_TONE[placement.tone]} />
      {/* Separate honest facts (2026-08-14) — "this agent", "this MacBook",
          "the gateway" and "OpenClaw" are four different claims and must
          never collapse into Placement's one light. Rendered only when
          there IS a paired computer (boundGateway); a cloud-only agent has
          nothing here to report, and a row with nothing knowable to say is
          worse than no row (CLAUDE.md's "no dead controls" — the same law,
          applied to a status line instead of a button). */}
      {computerPresentation && (
        <PanelRow
          label="This computer"
          value={computerPresentation.label}
          tone={computerPresentation.tone === "online" ? "online" : "offline"}
          hint={boundGateway ? gatewayLabel(boundGateway) : undefined}
        />
      )}
      {/* "Gateway" is execution readiness, not reachability — the same
          execution_blocked distinction the Hardware badge already makes
          (gateway-box-picker.tsx's connectionPresentation: "the WSS session
          and heartbeat are genuinely fine... but shell.execute would fail
          on this box right now"). Only rendered when it says something
          "This computer" doesn't already: a box that isn't even reachable
          has no separate execution claim to make. */}
      {boundGateway && rawGatewayConnectionStatus === "online" && (
        <PanelRow label="Gateway" value="Online" tone="online" />
      )}
      {boundGateway && rawGatewayConnectionStatus === "execution_blocked" && (
        <PanelRow label="Gateway" value="Tools unavailable" tone="offline" />
      )}
      {/* OpenClaw is a per-channel surface, not one flag — see
          useGatewayPersonalChannelSurfaces. Only asked about at all when
          this agent has a gateway-requiring channel actually connected;
          "Checking…"/"Unknown" are real, distinct states from "Offline" —
          never guess green, and never report red for "haven't heard yet". */}
      {boundGateway && gatewayRequiringChannelConnected && (
        <PanelRow
          label="OpenClaw"
          value={
            openClawSurfaces.loading
              ? "Checking…"
              : openClawSurfaces.items.length === 0
                ? "Unknown"
                : openClawConnected
                  ? "Online"
                  : "Offline"
          }
          tone={
            !openClawSurfaces.loading && openClawSurfaces.items.length > 0
              ? (openClawConnected ? "online" : "offline")
              : "muted"
          }
          hint="Whether this computer's channel transport (Signal, iMessage, WeChat, and other connected channels) is reachable right now."
        />
      )}
      {/* Model row removed (founder: "model picking some shit like this must
          not be on the right side, we already moved it to the bottom") —
          model selection now lives in the composer at the bottom of Chat,
          plus the full editor on the Configure > Model tab. Having a THIRD
          picker here duplicated both. AgentModelPickerRow (the popover this
          row used to open) had no other caller, so it was deleted with this
          row rather than left as dead code — see ModelTab below for the
          real editor. */}
      {/* The "Tools" row that used to live here ("customer access" count,
          gated on agent.audience === "external") is deleted outright, not
          just hidden — founder, repeatedly: the owner/customer-facing
          classification is a concept the product should not have at all,
          "conversations are private, agent is public — that shit is
          [wrong], the entire thing." Access to an agent is binary, gated by
          who can reach it (a channel it's wired to), never a per-agent
          "audience" a human grades it with in a form. The Configure >
          General "Purpose" picker that used to write agent.audience
          (PurposeSection) is deleted the same way, below GeneralTab. So is
          the Configure > Tools TAB itself, as of 2026-08-21 — the
          per-CHANNEL-TURN tool-authority mechanism it wrote grants for no
          longer exists either (server_modules/authority_mandate_service.py).
          fleet_tools.resolve_agent_audience is left alone on the backend:
          `audience` still means WHO MAY TALK TO IT (reachability), which is
          the one axis that genuinely differs between the two product
          shapes, and it never gated tools. */}
      {/* Zero is never shown as a row — an unconnected Channels/Connectors
          count is noise, not a fact worth a permanent line in a panel
          that's visible on every tab. Configure > Channels/Connectors is
          still the place to go connect one; this panel just stops
          announcing "0" for something not yet set up. */}
      {/* Names, not a bare count (2026-08-14) — "which channels this agent
          is in" is the founder's own phrasing, and a count answers a
          different question. Configure > Channels is still where you go to
          change any of this; this row is a fact, not a control. */}
      {connectedChannels > 0 && (
        <PanelRow label="Channels" value={connectedChannelRows.map((c) => c.label).join(", ")} />
      )}
      {connectedConnectors > 0 && <PanelRow label="Apps" value={connectedConnectors} />}
      {/* Cost lives at the bottom, and stays off the panel entirely for an
          agent that hasn't spent anything this period — a freshly created
          agent's Properties shouldn't open on a $0.0000 line before anything
          has even run. No sparkline here either (that's the workspace/
          project-level dashboards' job, fleet-sparkline.tsx's UsageStat) —
          at single-agent scale a 90-day trend line for one number reads as
          decoration, not information. */}
      {costToday !== null && costToday > 0 && (
        <div className="fleet-panel-row">
          <span className="fleet-panel-row-label" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <span>{costPeriodLabel(costPeriod)}</span>
            <CostPeriodToggle period={costPeriod} onChange={setCostPeriod} />
          </span>
          <span className="fleet-panel-row-value">{formatUsd(costToday)}</span>
        </div>
      )}
      {/* Full attribution, not just a total: every real model/source this
          agent has actually billed against, all-time — real tokens at that
          model's real per-1M provider price, whoever pays for it. "Not
          priced" (never a fabricated $0.00) when the source has no per-token
          price to charge, e.g. a flat CLI subscription turn. All-time, so
          this can still show real rows (e.g. spend from before a mode
          switch) even while the "Cost today" row above is hidden for a
          quiet period — deliberately not gated on costToday. */}
      {buildCostDisplayRows(costMatrix).map((row) => (
        <PanelRow
          key={row.key}
          label={row.label}
          hint={row.hint}
          value={row.pricing_known ? formatUsd(row.usd_cost) : "Not priced"}
          tone={row.pricing_known ? "default" : "muted"}
        />
      ))}
    </PanelSection>
  );
  // The old "Sessions" section here (a second, owner-only conversation list
  // with its own "New chat" control) is GONE, not merely hidden — it was
  // the picker for the composer's own private test threads
  // (useAgentConversations filtered to the signed-in owner's turns only),
  // and there is no composer left to open a thread into. The single
  // observation view below (WorkTab) already IS a conversation picker — its
  // own left-hand "Work stream" list is every REAL conversation this agent
  // has had, across every channel, not just the owner's web test chats — so
  // this panel would have been a second, narrower picker sitting on top of
  // a better one (this codebase's own standing rule: "only ONE surface may
  // be the picker at a time"). Properties (above) is the whole panel now.
  const inner = (
    <>
      {/* ONE header, every tab — not a breadcrumb, not a Chat|Work tab
          strip, not a separate Configure button, and not a second "Chat
          with this agent" control. The shell's own breadcrumb topbar is
          suppressed for every route under an agent's detail surface (see
          FleetContentFrame.tsx's AGENT_DETAIL_ROUTE), so this is the page's
          only header, not a second one stacked above an emptied-out first.
          See AgentDetailHeader's own block comment for why the persistent
          "Work" button is gone. */}
      <AgentDetailHeader
        workspaceId={workspaceId}
        agentId={agentId}
        agent={agent}
        agentLoadPhase={agentLoadPhase}
        agentLabel={agent?.label || "Unnamed agent"}
        statusTone={status.tone}
        statusLabel={status.label}
        sheetOpen={sheetOpen}
        backHref={backHref || `/w/${encodeURIComponent(workspaceId)}/projects/${encodeURIComponent(projectId)}`}
        backLabel={backLabel || projectName || "Project"}
        configureHref={tabHref(sheetOpen ? activeTab : CONFIGURE_GROUPS[0].tabs[0])}
        profileHref={tabHref(profileOpen ? activeTab : defaultAgentProfileSegment(isMaster))}
        onOpenProperties={() => setPropertiesOpen(true)}
        onAgentChanged={onRenamed}
        // Go somewhere that still exists. Staying on this route after a
        // confirmed delete renders a detail page for an agent the backend no
        // longer has — a 404 the person caused by succeeding.
        onDeleted={() => router.replace(`/w/${encodeURIComponent(workspaceId)}/agents`)}
      />

      {/* ONE control, never a row of them. The creation sequence already
          walked through Channel and Apps in order; this is what is left
          when the Channel step was skipped, which is the only state that
          leaves an agent nobody can reach. A real <Link> straight into the
          Configure section that fixes it (the sheet is derived from the URL
          segment, so a deep link opens on the right section on first
          paint), and it vanishes the moment a channel connects. See
          agent-setup-steps.ts for why exactly one row renders, which steps
          exist, which two deliberately do not, and why nothing here routes
          to `context`. */}
      {setupStep && (
        <section className="fleet-agent-setup" aria-label="Setup">
          <h2 className="fleet-agent-setup-title">{agentSetupHeading(agent?.label || "")}</h2>
          {(() => {
            const StepIcon = setupStep.id === "channel" ? Radio : setupStep.id === "hardware" ? Cpu : Plug;
            return (
              <Link href={tabHref(setupStep.tab)} replace className="fleet-agent-setup-step">
                <span className="fleet-agent-setup-step-icon">
                  <StepIcon size={13} strokeWidth={1.75} aria-hidden="true" />
                </span>
                <span className="fleet-agent-setup-step-text">
                  <span className="fleet-agent-setup-step-label">{setupStep.label}</span>
                  {setupStep.hint ? <span className="fleet-agent-setup-step-hint">{setupStep.hint}</span> : null}
                </span>
                <ChevronRight size={13} strokeWidth={1.75} aria-hidden="true" className="fleet-agent-setup-step-chevron" />
              </Link>
            );
          })()}
        </section>
      )}

      {/* The relative anchor the Properties overlay below floats against —
          .fleet-detail-body is this container's only normal-flow child and
          always renders at full width, whether the overlay (an absolutely-
          positioned child, out of flow entirely) is open or closed. Same
          contract as the list pages' .fleet-content-with-panel. */}
      <div className="fleet-detail-columns">
        <div className="fleet-detail-body">
          {/* "chat" is this agent's front door and "work" is kept only as a
              dead-but-live legacy URL (same treatment this codebase already
              gives /agents, /conversations — see CLAUDE.md) — both render
              the SAME read-only observation surface, never a composer. No
              hidden-mount trick is needed here the way ChatTab used to need
              one: there is no local in-flight send state left to protect
              (no draft, no watchdog, nothing this component owns that the
              turn depends on) — the turn runs and persists server-side
              regardless of whether this is mounted, so it's fine for
              WorkTab to unmount like any other tab when Configure/Profile
              cover it. onSelectThread just mirrors WorkTab's own selection
              up to `threadId` so the Profile sheet's Files pane (below)
              can show files for whichever real conversation is on screen. */}
          {(activeTab === "chat" || activeTab === "work") && (
            <WorkTab
              workspaceId={workspaceId}
              agentId={agentId}
              agent={agent}
              onAgentChanged={onRenamed}
              onSelectThread={setThreadId}
            />
          )}
        </div>
        <FleetRightPanel open={propertiesOpen} onClose={() => setPropertiesOpen(false)} ariaLabel="Properties">
          {propertiesContent}
        </FleetRightPanel>
      </div>
    </>
  );

  // Configure sheet — the other six sections (Model, Capabilities,
  // Channels, Apps, Tools, Hardware). Same tab components as the
  // switch above used to render directly, completely unchanged (same
  // props, same markup) — just called from here instead, since activeTab
  // being one of these six is exactly what CONFIGURE_TAB_IDS/sheetOpen
  // means. GroupedRail is the same component Settings uses (SettingsShell.
  // tsx); `replace` (see GroupedRail.tsx) keeps switching sections inside
  // the sheet from piling up back-button stops, matching the top tabs'
  // own replace-not-push navigation just above.
  const configureGroups: GroupedRailGroup[] = CONFIGURE_GROUPS.map((group) => ({
    id: group.id,
    label: group.label,
    items: group.tabs.map((id) => {
      const def = TABS.find((t) => t.id === id)!;
      return { id: def.id, label: def.label, icon: def.icon, href: tabHref(def.id) };
    }),
  }));

  // Closing goes to Chat — the agent's front door — mirroring [tab]/page.tsx's
  // own convention of coercing an unresolved/invalid tab to "chat" (see its
  // rawTab comment). Same selectTab→onTabChange→router.replace path every
  // other tab switch in this file already uses, not a one-off router call.
  const closeSheet = () => selectTab("chat");

  const configureSheet = sheetOpen ? (
    <div className="agent-configure-backdrop" onClick={closeSheet}>
      <div
        ref={sheetRef}
        className="agent-configure-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={`Configure ${agent?.label || "agent"}`}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          // Every keydown here stops before it reaches `window` — not just
          // Escape — so it can never reach PrimaryRail's global g-then-key
          // chord listener or this page's own onPageKeyDown (Esc-returns-
          // to-referrer) above. Typing in a text field inside the sheet, or
          // just having focus land on a button in here, must not fire rail
          // navigation. Same stopPropagation-on-the-dialog-root convention
          // this file's other nested dialogs already use (.fleet-channel-
          // banner below) and TasksBoard.tsx/TasksGroupedList.tsx use for
          // their own popovers.
          e.stopPropagation();
          if (e.key === "Escape") {
            e.preventDefault();
            closeSheet();
          }
        }}
      >
        <div className="agent-configure-header">
          <h2 className="agent-configure-title">Configure</h2>
          <p className="agent-configure-subtitle">{agent?.label || "Agent"}</p>
          <button type="button" className="fleet-detail-close" onClick={closeSheet} aria-label="Close">
            <X size={16} strokeWidth={1.75} />
          </button>
        </div>
        <div className="agent-configure-body">
          <GroupedRail groups={configureGroups} activeId={activeTab} ariaLabel="Configure agent" replace />
          <div className="agent-configure-content">
            {activeTab === "general" && (
              <GeneralTab workspaceId={workspaceId} agentId={agentId} agent={agent} isMaster={isMaster} onRenamed={onRenamed} />
            )}
            {activeTab === "model" && <ModelTab workspaceId={workspaceId} agentId={agentId} agent={agent} onSaved={onRenamed} />}
            {activeTab === "capabilities" && (
              <CapabilitiesTab workspaceId={workspaceId} agentId={agentId} agent={agent} />
            )}
            {activeTab === "skills" && (
              <SkillsTab workspaceId={workspaceId} agentId={agentId} agent={agent} onSaved={onRenamed} />
            )}
            {activeTab === "channels" && (
              <ChannelsTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChannelsChanged={refreshChannels} hardwareHref={tabHref("hardware")} />
            )}
            {activeTab === "connectors" && (
              <ConnectorsTab workspaceId={workspaceId} agentId={agentId} agent={agent} />
            )}
            {activeTab === "context" && (
              <ContextTab workspaceId={workspaceId} agentId={agentId} isMaster={isMaster} />
            )}
            {activeTab === "hardware" && (
              <HardwareTab
                workspaceId={workspaceId}
                agentId={agentId}
                agent={agent}
                onSaved={onRenamed}
                reachableChannels={connectedChannelRows.map((c) => c.label)}
                channelsLoading={channelsLoading}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  ) : null;

  // Profile sheet — see PROFILE_SEGMENT_DEFS' own comment above for the
  // founder's words and the reasoning. Reuses the exact same dialog shell
  // as configureSheet (agent-configure-backdrop/-sheet/-body/-content —
  // see agent-profile-sheet.css's own header comment for why: the two
  // sheets are never open at once, and the shell is already tuned —
  // responsive breakpoints, backdrop, focus trap keydown handling — so
  // there is nothing to gain from a second, parallel implementation. The
  // identity header (avatar + name + status) is the one genuinely
  // different piece, plus the segmented rail only appearing when there is
  // more than one segment to pick between (a rail of one is worse than no
  // rail — the same call already made for ProjectAgentsRail at one agent).
  const profileSegments = planAgentProfileSegments(isMaster);
  const profileGroups: GroupedRailGroup[] =
    profileSegments.length > 1
      ? [
          {
            id: "profile",
            items: profileSegments.map((id) => {
              const def = PROFILE_SEGMENT_DEFS[id];
              return { id, label: def.label, icon: def.icon, href: tabHref(id) };
            }),
          },
        ]
      : [];

  const profileSheet = profileOpen ? (
    <div className="agent-configure-backdrop" onClick={closeSheet}>
      <div
        ref={profileSheetRef}
        className="agent-configure-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={`${agent?.label || "Agent"} profile`}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          // Same stopPropagation-before-window contract as configureSheet's
          // own dialog root above — see that block's comment.
          e.stopPropagation();
          if (e.key === "Escape") {
            e.preventDefault();
            closeSheet();
          }
        }}
      >
        <div className="agent-configure-header">
          <div className="agent-profile-identity">
            <AgentSigil seed={agentId} size={36} />
            {agentLoadPhase === "loading" ? (
              <div style={{ minWidth: 0 }} aria-busy="true" aria-label="Loading agent">
                <p className="agent-profile-identity-name">
                  <span className="fleet-skeleton-bar" style={{ width: 140, height: 14 }} />
                </p>
                <span className="agent-profile-identity-status">
                  <span className="fleet-skeleton-bar" style={{ width: 80, height: 11, marginTop: 2 }} />
                </span>
              </div>
            ) : agentLoadPhase === "failed" ? (
              <div style={{ minWidth: 0 }}>
                <p className="agent-profile-identity-name">Couldn’t load this agent</p>
                <span className="agent-profile-identity-status">
                  <StatusDot tone="error" size={6} />
                  Check your connection — it’ll keep retrying
                </span>
              </div>
            ) : (
              <div style={{ minWidth: 0 }}>
                <p className="agent-profile-identity-name">{agent?.label || "Unnamed agent"}</p>
                <span className="agent-profile-identity-status">
                  <StatusDot tone={status.tone} size={6} />
                  {status.label}
                </span>
              </div>
            )}
          </div>
          <button type="button" className="fleet-detail-close" onClick={closeSheet} aria-label="Close">
            <X size={16} strokeWidth={1.75} />
          </button>
        </div>
        <div className="agent-configure-body">
          {profileGroups.length > 0 && (
            <GroupedRail groups={profileGroups} activeId={activeTab} ariaLabel="Agent profile" replace />
          )}
          <div className="agent-configure-content">
            {activeTab === "persona" && !isMaster && agent && (
              <PersonaEditor workspaceId={workspaceId} agentId={agentId} agent={agent} />
            )}
            {activeTab === "memory" && (
              <div className="agent-profile-memory-files">
                <div className="agent-profile-memory-pane">
                  <MemoryTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChat={() => onChat(agentId)} />
                </div>
                <div className="agent-profile-files-pane">
                  <h3 className="agent-profile-files-heading">Files</h3>
                  {/* threadId now tracks whichever real conversation is
                      selected in the observation view below (see WorkTab's
                      onSelectThread) — null only until that view has loaded
                      at least one conversation to select. */}
                  {threadId ? (
                    <ProfileFilesSection workspaceId={workspaceId} threadId={threadId} />
                  ) : (
                    <p className="agent-profile-files-loading" style={{ opacity: 0.7 }}>No files yet.</p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  ) : null;

  // No HeaderAction portal here anymore — that slot lives in the shell's
  // breadcrumb topbar (FleetContentFrame.tsx), which is now suppressed for
  // every route under an agent's detail surface (AGENT_DETAIL_ROUTE). Both
  // controls that used to portal into it (Stop agent, "Chat with this
  // agent") moved into AgentDetailHeader itself — Stop into the "⋯" menu,
  // Chat via the identity link — so there is nothing left to portal.
  return (
    <>
      <div
        className="fleet-detail fleet-detail--page"
        aria-label={`${agent?.label || "Agent"} details`}
        onKeyDown={onPageKeyDown}
      >
        {inner}
      </div>
      {configureSheet}
      {profileSheet}
    </>
  );
}

// ── General (Configure) — ex-Overview's identity + operating settings ──────
//
// Overview itself is gone (founder, 2026-08-13: "remove overview because
// it's something that we genuinely don't need inside this agent"). This is
// the part of it that survives, relocated: name and schedule are set-once
// configuration a person would go looking for under Configure, not glance
// at daily — the exact reasoning that already moved Model and Capabilities
// off the top tab strip. The status sentence and the day-grouped activity
// feed that used to sit alongside these did NOT move here; see TABS' own
// comment above for why each was dropped rather than moved.
//
// Persona moved OUT of here a second time on 2026-08-19, into its own
// Profile-sheet segment (PersonaEditor's own component definition below is
// unchanged — same fields, same PATCH, same save — just rendered from
// profileSheet now instead of from this function). "Who this agent is" and
// "how it operates" turned out to be different questions once there was a
// dedicated place to ask the first one; see PROFILE_SEGMENT_DEFS' comment
// above for the founder's own words.
function GeneralTab({
  workspaceId,
  agentId,
  agent,
  isMaster,
  onRenamed,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  isMaster: boolean;
  onRenamed?: () => void;
}) {
  return (
    <div className="fleet-detail-overview">
      {agent && (
        <AgentTitle workspaceId={workspaceId} agentId={agentId} label={agent.label || ""} onRenamed={onRenamed} />
      )}
      {!isMaster && agent && (
        <ScheduleSection workspaceId={workspaceId} agentId={agentId} />
      )}
    </div>
  );
}

// The agent's name — click-to-edit, save on blur/Enter, cancel on Escape.
// NAME IS NOT A CREATE-AGENT STEP: this is the only place an agent is
// renamed after creation, PATCHing display_name (fleet_configure_agent
// writes it to the same `label` column the create route and the name pool
// seed).
function AgentTitle({
  workspaceId, agentId, label, onRenamed,
}: { workspaceId: string; agentId: string; label: string; onRenamed?: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(label);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const skipBlurCommit = useRef(false);

  useEffect(() => {
    if (!editing) setDraft(label);
  }, [label, editing]);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  async function commit() {
    if (saving) return;
    const next = draft.trim();
    if (!next || next === label) {
      setDraft(label);
      setEditing(false);
      setError(null);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { display_name: next } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      setEditing(false);
      onRenamed?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not rename.");
    } finally {
      setSaving(false);
    }
  }

  if (editing) {
    return (
      <div style={{ marginBottom: 12 }}>
        <input
          ref={inputRef}
          className="fleet-overview-title-input"
          value={draft}
          disabled={saving}
          maxLength={200}
          onChange={(e) => setDraft(e.currentTarget.value)}
          onBlur={() => {
            if (skipBlurCommit.current) {
              skipBlurCommit.current = false;
              return;
            }
            void commit();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              e.currentTarget.blur();
            } else if (e.key === "Escape") {
              e.preventDefault();
              skipBlurCommit.current = true;
              setDraft(label);
              setEditing(false);
              setError(null);
            }
          }}
        />
        {error && <p className="fleet-channel-expand-error" style={{ margin: "4px 0 0" }}>{error}</p>}
      </div>
    );
  }

  return (
    // MAN-145 title-dedup follow-up: this used to be wrapped in its own <h1>
    // (a real heading was missing entirely before that) — but the agent's
    // name is ALSO the breadcrumb's current crumb (useBreadcrumbLabel(agentId,
    // agent?.label) two levels up in the routed page), which is the page's
    // <h1> now (see Breadcrumbs.tsx). Two headings both reading the agent's
    // name would be the exact triplication this pass exists to cut — so this
    // is unwrapped back to a plain button, not retagged: the heading role
    // lives one layer up, the rename affordance (click-to-edit, same click
    // target, same visual style) is entirely unchanged.
    <button type="button" className="fleet-overview-title" onClick={() => setEditing(true)} style={{ marginBottom: 12 }} aria-label={`Rename ${label || "this agent"}`}>
      <span>{label || "Untitled agent"}</span>
      <Pencil size={14} strokeWidth={1.75} className="fleet-overview-title-pencil" />
    </button>
  );
}

// Persona / system-prompt editor — the biggest pre-existing customization gap:
// instructions were only ever settable at creation (wizard step 1's one-liner),
// with no way to see or change what an agent IS after it exists.
function PersonaEditor({
  workspaceId, agentId, agent,
}: { workspaceId: string; agentId: string; agent: FleetAgent }) {
  const [draft, setDraft] = useState(agent.instructions || "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Compact by default (~3 rows) — persona text is usually read once, not
  // sat in front of; expand for the rare full-rewrite edit.
  const [expanded, setExpanded] = useState(false);

  // Cmd+K palette / browser Back can swap which agent this tab shows
  // without unmounting this component (only agentId/agent change) — the
  // `useState(agent.instructions || "")` above only seeds `draft` on the
  // very first mount, so without this, `draft` keeps showing the PREVIOUS
  // agent's persona (or an in-progress unsaved edit for it), and Save would
  // PATCH that stale text onto the NEW agent's instructions. Mirrors
  // AgentTitle's own reset effect above (`if (!editing) setDraft(label)`),
  // keyed on agentId — not on `agent` itself, which gets a new object
  // reference on every ~30s poll tick for the SAME agent and would clobber
  // an in-progress, unsaved edit if used here instead.
  useEffect(() => {
    setDraft(agent.instructions || "");
    setSaved(false);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { instructions: draft } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ marginTop: 4 }}>
      {/* Save top-right of the section header — same placement as
          ScheduleSection's own primary action, just below. */}
      <div className="fleet-detail-section-title" style={{ marginTop: 0, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span>Persona</span>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={save} disabled={saving}>
          {saving ? "Saving…" : saved ? <><Check size={14} strokeWidth={2} /> Saved</> : "Save"}
        </button>
      </div>
      <textarea
        className={`fleet-persona-textarea${expanded ? " fleet-persona-textarea--expanded" : ""}`}
        value={draft}
        onChange={(e) => { setDraft(e.currentTarget.value); setSaved(false); }}
        placeholder="What this agent is and how it should behave — e.g. “You handle customer refund requests. Be concise and confirm the order number first.”"
        spellCheck
      />
      <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 12 }}>
        <button type="button" className="fleet-link" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show less" : "Expand"}
        </button>
        {error && <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</span>}
      </div>
    </div>
  );
}

// ── Schedule (Part U2) — when this agent wakes on its own ──────────────────

function formatDueAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return formatDateTime(d, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function ScheduleTierBadge({ tier }: { tier: FleetScheduleItem["authority_tier"] }) {
  if (tier === "owner") {
    return <span className="fleet-badge fleet-badge--lock" style={{ marginLeft: 0 }}>Owner</span>;
  }
  if (tier === "system") {
    return <span className="fleet-badge" style={{ marginLeft: 0 }}>System</span>;
  }
  return <span className="fleet-badge" style={{ marginLeft: 0 }}>Audience</span>;
}

function ScheduleSection({ workspaceId, agentId }: { workspaceId: string; agentId: string }) {
  const { schedule, loading, refresh } = useFleetAgentSchedule(workspaceId, agentId);
  const [creating, setCreating] = useState(false);
  const [when, setWhen] = useState("");
  const [instruction, setInstruction] = useState("");
  const [previewText, setPreviewText] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Show the resolved next run before confirming — reparsed server-side via
  // the same _parse_when schedule_task itself uses, so there's no drift
  // between what's previewed and what actually gets created.
  useEffect(() => {
    if (!creating || !when.trim()) { setPreviewText(null); setPreviewError(null); return; }
    let cancelled = false;
    const handle = setTimeout(() => {
      void previewFleetAgentSchedule(workspaceId, agentId, when.trim()).then((result) => {
        if (cancelled) return;
        if (result.ok && result.due_at) {
          setPreviewText(formatDueAt(result.due_at));
          setPreviewError(null);
        } else {
          setPreviewText(null);
          setPreviewError(result.error || "Couldn't parse that time.");
        }
      });
    }, 400);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [creating, when, workspaceId, agentId]);

  function cancelCreate() {
    setCreating(false);
    setWhen("");
    setInstruction("");
    setPreviewText(null);
    setPreviewError(null);
    setError(null);
  }

  async function handleCreate() {
    setBusy(true);
    setError(null);
    const result = await createFleetAgentSchedule(workspaceId, agentId, when.trim(), instruction.trim());
    setBusy(false);
    if (result.ok) {
      cancelCreate();
      await refresh();
    } else {
      setError(result.error || "Could not schedule this wake-up.");
    }
  }

  async function handleDelete(id: string) {
    setDeletingId(id);
    setError(null);
    const result = await deleteFleetAgentSchedule(workspaceId, agentId, id);
    setDeletingId(null);
    if (result.ok) await refresh();
    else setError(result.error || "Could not cancel this wake-up.");
  }

  return (
    <div style={{ marginTop: 4 }}>
      <div className="fleet-detail-section-title" style={{ marginTop: 0, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span>Schedule</span>
        {!creating && (
          <button type="button" className="fleet-btn" onClick={() => setCreating(true)}>
            <Clock size={14} strokeWidth={1.75} /> Schedule a wake-up
          </button>
        )}
      </div>

      {creating && (
        <div className="fleet-card" style={{ padding: "var(--space-3)", marginBottom: 12 }}>
          <div className="fleet-wizard-label" style={{ marginTop: 0 }}>When</div>
          <input
            className="fleet-wizard-input"
            placeholder="in 2 hours, or 2026-07-10T09:00:00Z"
            value={when}
            onChange={(e) => setWhen(e.target.value)}
          />
          {previewText && <p className="fleet-subtitle" style={{ marginTop: 4 }}>→ {previewText}</p>}
          {previewError && <p className="fleet-channel-expand-error" style={{ marginTop: 4 }}>{previewError}</p>}
          <div className="fleet-wizard-label">What should it do</div>
          <textarea
            className="fleet-wizard-input"
            placeholder="Check the inbox and follow up on anything unanswered."
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            rows={2}
            style={{ height: "auto", padding: "8px 12px", resize: "vertical" }}
          />
          {error && <p className="fleet-channel-expand-error" style={{ marginTop: 4 }}>{error}</p>}
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button type="button" className="fleet-btn" disabled={busy} onClick={cancelCreate}>
              Cancel
            </button>
            <button
              type="button"
              className="fleet-btn fleet-btn--accent"
              disabled={busy || !when.trim() || !instruction.trim() || !previewText}
              onClick={handleCreate}
            >
              {busy ? "Scheduling…" : "Confirm"}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <FleetToggleRowsSkeleton rows={2} trailing="button" label="Loading schedule" />
      ) : schedule.length === 0 ? (
        <p className="fleet-subtitle" style={{ marginTop: 0 }}>
          No scheduled wake-ups. This agent only acts when messaged.
        </p>
      ) : (
        <div className="fleet-config" style={{ padding: 0 }}>
          {schedule.map((item) => (
            <div key={item.id} className="fleet-toggle-row">
              <div style={{ minWidth: 0 }}>
                <div className="fleet-toggle-row-label">{item.description || "Scheduled wake-up"}</div>
                <div className="fleet-toggle-row-desc" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span>{formatDueAt(item.due_at)} · One-time</span>
                  <ScheduleTierBadge tier={item.authority_tier} />
                </div>
              </div>
              <button
                type="button"
                className="fleet-btn"
                disabled={deletingId === item.id}
                onClick={() => handleDelete(item.id)}
                title="Cancel this scheduled wake-up"
              >
                <Trash2 size={14} strokeWidth={1.75} /> {deletingId === item.id ? "Cancelling…" : "Cancel"}
              </button>
            </div>
          ))}
        </div>
      )}
      {!creating && error && <p className="fleet-channel-expand-error" style={{ marginTop: 8 }}>{error}</p>}
    </div>
  );
}

// ── Channels ────────────────────────────────────────────────────────────────

import {
  ChannelLinkForm,
  ChannelSettingsBody,
  ConnectedChannelSummary,
  CredentialForm,
  PanelBackBar,
  useChannelPolicySummary,
  useOpenClawChannelSetup,
} from "./OpenClawChannelsPanel";
import {
  channelCardPill,
  connectMethodFor,
  openclawObservedErrorBanner,
} from "./openclaw-channel-copy";
import { planChannelSetupFlow, setupQuestionFor } from "./channel-setup-flow";
import {
  CHANNEL_GRID_PLATFORMS,
  channelDoorChoiceNote,
  channelDoorHardwareNote,
  channelDoorHardwareState,
  channelDoorUnavailableReason,
  groupTransportedChannels,
  isChannelDoorAvailable,
  planChannelDoors,
  planDoors,
  type ChannelDoor,
} from "./channel-doors";
import { planUnifiedChannelGrid } from "./channel-platform";
import { isChannelRecommended } from "./channel-popularity";
import {
  CHANNEL_RECOMMENDED_BADGE,
  channelHardwareNextStep,
  channelHardwareTier,
  compareChannelGridCards,
  planChannelHardwareFilter,
  showsRecommendedBadge,
  type ChannelHardwareFilterId,
  type ChannelHardwareNextStep,
  type ChannelHardwareTier,
} from "./channel-hardware-tier";
// PersonalChannelConnectPanel and IMessageSetupPanel imports DELETED
// 2026-08-14 (full OpenClaw channel cutover) — their only call sites (the
// Telegram full_account door, WhatsApp/Signal/iMessage's first-party cards)
// are deleted in the same change; see channel-doors.ts.
import {
  isPersonalChannelStatusActive,
  useGatewayPersonalChannelSurfaces,
  usePersonalChannelStatus,
  type PersonalChannelKey,
} from "./personal-channel-pairing";

// CHANNEL_GRID_PLATFORMS — the first-party half of the grid — moved to
// channel-doors.ts (imported above) so openclaw-channel-copy.test.ts can drive
// the REAL labels instead of the hand-copied literals it used to pin. The order
// declared there is no longer the render order: the grid is sorted by
// channel-popularity.ts (see `unifiedChannelCards` below), which is the one
// place both halves of the grid agree on what leads.

// The doors model — CHANNEL_DOORS, the door-count rule that decides whether a
// picker is shown at all, and the hardware/consequence copy each door face
// carries — lives in channel-doors.ts, imported at the top of this file. It is
// pure data + pure functions precisely so channel-doors.test.ts can drive the
// REAL doors instead of re-typing their strings as pinned literals.

// LOCAL_BRIDGE_NO_GATEWAY_HINT and LocalBridgeChannelStatus (the Signal
// first-party health-check panel) DELETED 2026-08-14 (full OpenClaw channel
// cutover) — signal_personal's first-party runtime is gone, and
// openclaw_signal's status now renders through the generic OpenClaw channel
// panel (OpenClawChannelsPanel.tsx / ChannelCardPanel), not this one-off.

/** A channel that runs on the agent's own computer, opened by an agent that
 *  has none: the fact, plainly, plus the REAL next step.
 *
 *  This is the "no dead controls" law read the right way round. The wrong fix
 *  is to render the setup form anyway and let it fail, and the fix that was
 *  actually shipped until now is barely better — one sentence naming a tab,
 *  with nothing to press. A person who has just been told their channel needs
 *  something they do not have should be one click from getting it, and the
 *  click is a real `<Link>` so cmd-click and middle-click work.
 *
 *  Renders no control at all when there is no destination to point at, rather
 *  than a button that goes nowhere — the same rule one level down. */
function HardwareNextStep({ step, href }: { step: ChannelHardwareNextStep; href?: string }) {
  return (
    <div className="fleet-door-unavailable">
      <p className="fleet-door-unavailable-title">
        <Cpu size={14} strokeWidth={2} aria-hidden /> {step.title}
      </p>
      <p className="fleet-channel-expand-hint">{step.body}</p>
      {href ? (
        <Link className="fleet-btn fleet-btn--accent" href={href}>
          {step.action}
        </Link>
      ) : null}
    </div>
  );
}

/** The door that was chosen, kept on screen while its setup runs.
 *
 *  COMPACT ONCE A FORM IS SHOWING
 *  ------------------------------
 *  Before the pick, a door is a card: label, what it is, what it costs. AFTER
 *  the pick that block kept its full height and sat above the form, so the
 *  customer typing a phone number had two card-sized blocks of already-read
 *  text above the field — the founder's words: "two very very big node, it's
 *  just there regardless while I'm just typing my phone number."
 *
 *      picking            ─▶  filling in the form
 *      ┌───────────────┐      ─ Full account · ⚠ Telegram can ban…  [Change] ─
 *      │ Full account  │      ┌─────────────────────────────────────────────┐
 *      │ signs in as…  │      │ Phone number                                │
 *      │ ⚠ can ban…    │      └─────────────────────────────────────────────┘
 *      └───────────────┘
 *
 *  The consequence has already done its job at the moment of choosing, so it
 *  stops being a block and becomes part of the one line — still there, still
 *  readable, no longer dominant. It is never dropped: the risk must stay
 *  discoverable for as long as the door is open. */
function ChosenDoorBar({
  door,
  compact,
  onChange,
}: {
  door: ChannelDoor;
  compact: boolean;
  onChange: (() => void) | null;
}) {
  return (
    <div className={`fleet-door-chosen${compact ? " fleet-door-chosen--compact" : ""}`}>
      <div className="fleet-door-chosen-text">
        <span className="fleet-door-chosen-label">{door.label}</span>
        {door.consequence ? (
          <span className={`fleet-door-consequence fleet-door-consequence--${door.consequence.tone}`}>
            {door.consequence.tone === "risk"
              ? <AlertTriangle size={11} strokeWidth={2} aria-hidden />
              : <Check size={11} strokeWidth={2} aria-hidden />}
            {door.consequence.text}
          </span>
        ) : null}
      </div>
      {onChange ? (
        <button type="button" className="fleet-btn fleet-door-chosen-change" onClick={onChange}>
          <ArrowLeft size={13} strokeWidth={2} aria-hidden /> Change
        </button>
      ) : null}
    </div>
  );
}

function channelStatePill(channel: FleetChannel | undefined): { label: string; tone: "connected" | "gateway" | "locked" | "setup" } {
  if (!channel) return { label: "Unavailable", tone: "locked" };
  if (channel.connected) return { label: "Connected", tone: "connected" };
  if (channel.requiresGateway && channel.onlineGatewayCount === 0) return { label: "Needs Gateway", tone: "gateway" };
  if (channel.nextAction === "locked" || !channel.setupAvailable) return { label: "Not configured here", tone: "locked" };
  return { label: "Set up", tone: "setup" };
}

export function ChannelsTab({
  workspaceId, agentId, agent, onChannelsChanged, hardwareHref,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  /** Where "Set up a computer" goes. A channel that runs on the agent's own
   *  box is not a dead end for a cloud-only agent — it says so plainly and
   *  offers the real next step, which is a REAL LINK so cmd-click and
   *  middle-click work (CLAUDE.md craft doctrine). Optional because a caller
   *  embedding this tab outside the agent surface has no Hardware tab to point
   *  at; where it is absent the panel still states the fact and simply renders
   *  no control, rather than a button that goes nowhere. */
  hardwareHref?: string;
  /** Called (in addition to this tab's own internal refresh) after a
   *  channel connects/binds — lets a caller that holds its OWN separate
   *  useFleetAgentChannels instance (FleetAgentDetail's Properties column,
   *  which needs a live "Channels" count) catch up in place instead of
   *  waiting for a remount. Optional and unused by FleetCreateAgentWizard's
   *  standalone embed of this same tab, which has no such sidebar to sync. */
  onChannelsChanged?: () => void;
}) {
  const { channels, loading, refresh: refreshChannels, telegramBotConnected, slackChannelBinding } = useFleetAgentChannels(workspaceId, agentId);
  // Every connect-success path below should notify both this tab's own
  // hook instance AND (when present) the caller's separate one.
  const handleChannelsChanged = useCallback(() => {
    void refreshChannels();
    onChannelsChanged?.();
  }, [refreshChannels, onChannelsChanged]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [oauthBusy, setOauthBusy] = useState<string | null>(null);
  const [oauthError, setOauthError] = useState<string | null>(null);

  // Full-account channels (Telegram/WhatsApp/iMessage/WeChat "full_account"
  // doors) bind to THIS agent's own gateway, never a workspace-wide/Sage
  // one — this is that binding. `undefined` would fall back to legacy
  // workspace-wide behavior in PersonalChannelConnectPanel; passing a
  // possibly-empty string here always keeps it agent-scoped.
  const agentGatewayId = agent?.preferred_gateway_id?.trim() || null;

  // The transported channels this gateway carries — merged into the SAME
  // card grid as the first-party ones below, never a second stacked section.
  // `agentGatewayId: null` resolves instantly with no rows and no fetch: this
  // transport is structurally box-only, so a cloud-only agent has nothing here
  // to show, not a spinner that never resolves.
  const openclaw = useOpenClawChannelSetup(agentGatewayId, agentId);
  // The shared local-bridge surfaces subscription (localBridge) that used to
  // live here — read by LocalBridgeChannelStatus and IMessageSetupPanel —
  // was deleted 2026-08-14 (full OpenClaw channel cutover) along with both
  // of those components; nothing in this file needs
  // useGatewayPersonalChannelSurfaces a second time (openclaw's own call
  // above already covers the transported grid).
  // Which transported PLATFORM's panel is open, held as its base channel_key
  // (not the entry object) so an open panel re-reads the LIVE row after a
  // provision or a credential save instead of showing a snapshot from click
  // time.
  const [openclawDetailKey, setOpenclawDetailKey] = useState<string | null>(null);
  // Which half of the two-tier split the grid is showing. Defaults to the
  // channels a person can connect with nothing installed — the founder's
  // "lead with what works with no hardware". Plain state, not persisted: the
  // honest default is a property of the grid, and a remembered selection would
  // silently hide half of it on a later visit.
  const [channelFilter, setChannelFilter] = useState<ChannelHardwareFilterId>("hardware_free");
  // Which VARIANT of that platform is being set up — the transported half of
  // the same door pick the first-party panel makes with `selectedDoor`.
  const [openclawDoorKey, setOpenclawDoorKey] = useState<string | null>(null);
  // Whether the connected screen's Edit has been pressed. An INPUT to
  // planChannelSetupFlow, never a branch here: that function only honours it
  // from `ready`, so this cannot open the policy forms on a channel that does
  // not work yet. Cleared whenever the panel or the door changes, so opening a
  // channel always lands on its summary rather than on whatever was last
  // being edited.
  const [openclawEditing, setOpenclawEditing] = useState(false);
  // One shared read of the three policy facts the connected summary states.
  // MUST SIT WITH THE OTHER HOOKS, ABOVE THIS COMPONENT'S OWN LOADING EARLY
  // RETURN — a hook after a conditional return is a hook-count change between
  // renders, which React refuses at runtime (#300) with a blank screen and no
  // clue which hook moved. Keyed off state rather than off the resolved row
  // for the same reason: the row is computed far below that return.
  //
  // `openclawDoorKey ?? openclawDetailKey` is the chosen variant when there was
  // a choice, and the platform's base channel otherwise — which is exactly the
  // channel a single-door card opens into.
  const openclawSummary = useChannelPolicySummary(
    agentGatewayId,
    agentId,
    openclawDoorKey ?? openclawDetailKey,
  );

  // Which door is picked inside the open banner. Only channels with more than
  // one door need an explicit pick — a single-door channel auto-selects its
  // one real door (see `activeDoor` below).
  const [selectedDoor, setSelectedDoor] = useState<string | null>(null);
  const [byoToken, setByoToken] = useState("");
  const [byoBotBusy, setByoBotBusy] = useState(false);
  const [byoBotError, setByoBotError] = useState<string | null>(null);
  const [byoBotSaved, setByoBotSaved] = useState(false);
  const [slackChannelId, setSlackChannelId] = useState("");
  const [slackBindBusy, setSlackBindBusy] = useState(false);
  const [slackBindError, setSlackBindError] = useState<string | null>(null);
  const [slackBindSaved, setSlackBindSaved] = useState(false);
  // WeChat Official Account / WeCom: a 3-4 field credential pair (AppID/
  // CorpID + AppSecret/CorpSecret + Token, plus WeCom's numeric AgentId),
  // not a single pasted token — same BYO-per-agent shape as Telegram/
  // Discord's byo_bot door above, just more fields (see
  // routes_fleet.py's fleet_assign_agent_wechat).
  const [wechatAccountKind, setWechatAccountKind] = useState<"official_account" | "wecom">("official_account");
  const [wechatAppId, setWechatAppId] = useState("");
  const [wechatAppSecret, setWechatAppSecret] = useState("");
  const [wechatVerifyToken, setWechatVerifyToken] = useState("");
  const [wechatWecomAgentId, setWechatWecomAgentId] = useState("");
  const [wechatBusy, setWechatBusy] = useState(false);
  const [wechatError, setWechatError] = useState<string | null>(null);
  const [wechatSaved, setWechatSaved] = useState(false);
  // The per-agent callback URL to paste into WeChat/WeCom's admin console
  // (Server Configuration) — only known once assign_wechat_official returns
  // it, so there is nothing to show before a successful save.
  const [wechatWebhookUrl, setWechatWebhookUrl] = useState<string | null>(null);
  const [firstContactReply, setFirstContactReply] = useState(!!agent?.telegram_first_contact_reply);
  const [firstContactSaving, setFirstContactSaving] = useState(false);
  // agent starts null and loads async — resync once the real value arrives
  // (and again if it changes, e.g. edited from another tab) without
  // clobbering an in-progress toggle on every 30s poll of an unchanged value.
  useEffect(() => {
    if (agent) setFirstContactReply(!!agent.telegram_first_contact_reply);
  }, [agent?.telegram_first_contact_reply]);

  const saveByoBotToken = useCallback(async (channel: "telegram" | "discord") => {
    if (!byoToken.trim()) {
      setByoBotError(channel === "discord" ? "Paste the bot token from Discord's developer portal." : "Paste the token BotFather gave you.");
      return;
    }
    setByoBotBusy(true);
    setByoBotError(null);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-channels/${channel}?agent_id=${encodeURIComponent(agentId)}`,
        {
          method: "POST",
          credentials: "include",
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify({ token: byoToken.trim() }),
        },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      setByoBotSaved(true);
      handleChannelsChanged();
    } catch (e) {
      const message = e instanceof Error ? e.message : "Could not save the bot token.";
      setByoBotError(friendlyChannelOwnershipError(message));
    } finally {
      setByoBotBusy(false);
    }
  }, [workspaceId, agentId, byoToken, handleChannelsChanged]);

  const saveWeChatCredentials = useCallback(async () => {
    if (!wechatAppId.trim() || !wechatAppSecret.trim() || !wechatVerifyToken.trim()) {
      setWechatError("AppID/CorpID, AppSecret/CorpSecret, and Token are all required.");
      return;
    }
    if (wechatAccountKind === "wecom" && !wechatWecomAgentId.trim()) {
      setWechatError("WeCom's AgentId (from the app's admin page) is required for a WeCom account.");
      return;
    }
    setWechatBusy(true);
    setWechatError(null);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-channels/wechat?agent_id=${encodeURIComponent(agentId)}`,
        {
          method: "POST",
          credentials: "include",
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify({
            account_kind: wechatAccountKind,
            app_id: wechatAppId.trim(),
            app_secret: wechatAppSecret.trim(),
            verify_token: wechatVerifyToken.trim(),
            wecom_agent_id: wechatAccountKind === "wecom" ? wechatWecomAgentId.trim() : undefined,
          }),
        },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      setWechatSaved(true);
      setWechatWebhookUrl(typeof data?.channel?.webhook_url === "string" ? data.channel.webhook_url : null);
      handleChannelsChanged();
    } catch (e) {
      const message = e instanceof Error ? e.message : "Could not save the WeChat/WeCom credentials.";
      setWechatError(friendlyChannelOwnershipError(message));
    } finally {
      setWechatBusy(false);
    }
  }, [workspaceId, agentId, wechatAccountKind, wechatAppId, wechatAppSecret, wechatVerifyToken, wechatWecomAgentId, handleChannelsChanged]);

  const saveFirstContactReply = useCallback(async (next: boolean) => {
    setFirstContactReply(next);
    setFirstContactSaving(true);
    try {
      await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { telegram_first_contact_reply: next } }),
      });
    } catch {
      // Best-effort — the toggle re-syncs from the agent record on next load.
    } finally {
      setFirstContactSaving(false);
    }
  }, [workspaceId, agentId]);

  const startOAuth = useCallback(async (id: string) => {
    setOauthBusy(id);
    setOauthError(null);
    try {
      const res = await fleetAuthorizedFetch(`/api/connections/${encodeURIComponent(id)}/setup/start`, {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        // metadata.agent_install_id: the backend's shared OAuth pipeline
        // (routes_connections.py) already threads this through the redirect
        // round-trip and files the resulting credential + binding at THIS
        // agent's scope instead of a bare workspace-wide one — it just needs
        // a caller to actually send it. Every other caller of this endpoint
        // is unaffected (it's optional there).
        body: JSON.stringify({ workspace_id: workspaceId, surface: "sage", metadata: { agent_install_id: agentId } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      }
      if (data?.authorization_url) {
        window.location.href = data.authorization_url;
        return;
      }
      throw new Error("No OAuth redirect was returned for this connection.");
    } catch (e) {
      setOauthError(e instanceof Error ? e.message : "Could not start OAuth setup.");
    } finally {
      setOauthBusy(null);
    }
  }, [workspaceId, agentId]);

  const saveSlackChannelBinding = useCallback(async () => {
    if (!slackChannelId.trim()) {
      setSlackBindError("Paste the Slack channel ID (visible in the channel's details).");
      return;
    }
    setSlackBindBusy(true);
    setSlackBindError(null);
    try {
      const res = await fleetAuthorizedFetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-channels/slack?agent_id=${encodeURIComponent(agentId)}`,
        {
          method: "POST",
          credentials: "include",
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify({ slack_channel_id: slackChannelId.trim() }),
        },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      setSlackBindSaved(true);
      handleChannelsChanged();
    } catch (e) {
      const message = e instanceof Error ? e.message : "Could not save the channel binding.";
      setSlackBindError(friendlyChannelOwnershipError(message));
    } finally {
      setSlackBindBusy(false);
    }
  }, [workspaceId, agentId, slackChannelId, handleChannelsChanged]);

  const activePlatform = CHANNEL_GRID_PLATFORMS.find((p) => p.id === expanded) || null;
  // THE DOOR-COUNT RULE, in one call (see channel-doors.ts). One real door →
  // "direct": the card opens straight into that door's setup, with no picker
  // step at all, because an intermediate screen offering one option is a dead
  // click. Two or more → "picker": the choice is shown FIRST, because the
  // doors differ in consequence, not merely in procedure. Nothing here names a
  // channel — Telegram gains a third door and WhatsApp a second as transported
  // paths land, and the count is what has to keep being true.
  const doorPlan = planChannelDoors(expanded);
  const doors = doorPlan.doors;
  const activeDoor =
    doorPlan.mode === "direct"
      ? doorPlan.door
      : doorPlan.mode === "picker"
        ? doors.find((d) => d.key === selectedDoor) || null
        : null;

  // Live pairing status for this channel's full-account door — read as soon
  // as the channel has one and its banner is open, NOT gated on that door
  // being the currently-selected one. Two things read this: (1) the
  // door-choice picker just below, which needs to know "is full_account
  // already connected" to badge the card BEFORE the founder has clicked into
  // it (the founder-reported bug: Telegram's picker gave no hint that
  // "Full account" was already connected as him, so he had to click in to
  // find out), and (2) pairingActive right below, which narrows this down to
  // "is the door on screen right now full_account AND mid-pairing." Must be
  // called unconditionally on every render (rules of hooks), so every branch
  // that isn't "this channel has a real full_account door, and its banner is
  // open" passes a null gatewayId, which short-circuits
  // usePersonalChannelStatus into a no-op — no fetch, no poll, view stays
  // null. alwaysPoll: true because this instance has no onRefresh() of its
  // own to ride in on (unlike PersonalChannelConnectPanel, which re-fetches
  // right after every phone/code/password submit) — without it, this poll
  // would fetch "idle" once, stop (idle isn't an ACTIVE_STATUSES status), and
  // never notice the user going on to actually pair from inside the panel
  // below.
  const hasFullAccountDoor = doors.some((d) => d.key === "full_account");
  const pairingChannelKey: PersonalChannelKey | null =
    !hasFullAccountDoor ? null
      : expanded === "sage_telegram_hosted" ? "telegram_personal"
      : expanded === "whatsapp_personal" ? "whatsapp_personal"
      : null;
  const { view: pairingView } = usePersonalChannelStatus(
    workspaceId,
    pairingChannelKey || "telegram_personal",
    pairingChannelKey ? agentGatewayId : null,
    agentId,
    { alwaysPoll: true },
  );
  // Full-account door badge for the picker below — true once this channel's
  // personal-channel session is actually connected, independent of which
  // door is currently selected.
  const fullAccountDoorConnected = !!pairingChannelKey && pairingView?.state?.status === "connected";
  // True only while the door on screen right now is full_account AND its
  // pairing is actively mid-flight (a code/QR/password step with real typed
  // input at stake — the ACTIVE_STATUSES a fresh poll loop is worth running
  // for, see personal-channel-pairing.ts). Switching the picker to a
  // different door at that exact moment would unmount
  // PersonalChannelConnectPanel and silently discard whatever's been typed
  // so far — the founder-reported bug this gates. Once pairing settles
  // (connected, or back to idle/disconnected) the picker returns to normal.
  const pairingActive = activeDoor?.key === "full_account" && !!pairingChannelKey && isPersonalChannelStatusActive(pairingView?.state?.status);

  // Which door is ALREADY connected, so the picker itself says so instead of
  // making the founder click into a door just to discover it's the one he
  // already paired (the reported bug: Telegram's "Full account" showed
  // "Connected as …" only once you opened it). Chatbot's connected state rides
  // in on telegramBotConnected (see isChannelConnected — the same "separate
  // catalog item from the grid pill" shape); full_account's comes from the live
  // pairing status polled just above, already scoped to whichever channel is on
  // screen.
  const isDoorConnected = (door: ChannelDoor): boolean =>
    expanded === "sage_telegram_hosted" && door.key === "byo_bot"
      ? telegramBotConnected
      : door.key === "full_account"
        ? fullAccountDoorConnected
        : false;
  // Hardware is a fact about a door, so it is answered the same way for the
  // picker face and for a direct-mode channel that has no face — never
  // discovered at two different moments depending on which path the customer
  // took, and never after the pick.
  const doorHardware = (door: ChannelDoor) =>
    channelDoorHardwareState(door, { hasHardware: !!agentGatewayId, doorConnected: isDoorConnected(door) });
  const doorAvailable = (door: ChannelDoor) =>
    isChannelDoorAvailable(door, { hasHardware: !!agentGatewayId, doorConnected: isDoorConnected(door) });
  // A direct-mode door the agent cannot complete must read as unavailable
  // instead of rendering a setup form that only fails once it's on screen —
  // this is the one-door half of the same "no dead controls" rule the picker
  // applies per door face.
  const activeDoorAvailable = !activeDoor || doorAvailable(activeDoor);
  // The picker is the step BEFORE a pick, so it is on screen exactly while
  // there is no pick. Going back is offered afterwards ("Change") — except
  // while a pairing is mid-flight, since unmounting
  // PersonalChannelConnectPanel silently discards a half-entered code.
  const showDoorPicker = doorPlan.mode === "picker" && !activeDoor;
  const canChangeDoor = doorPlan.mode === "picker" && !!activeDoor && !pairingActive;
  // THE one gate every setup form below hangs off. A door key rather than a
  // boolean pair, so a form cannot be reached while the picker is still asking
  // (activeDoor null), nor while the chosen door is unavailable on this agent.
  // One narrow waist instead of the same two conditions repeated at eight call
  // sites — exactly the shape CLAUDE.md warns the next branch will forget.
  const setupDoorKey = activeDoor && activeDoorAvailable ? activeDoor.key : null;

  // Only the TRUE first load (no channels fetched yet) gets the full-tab
  // skeleton — refresh() (called after every connect-success, see
  // handleChannelsChanged above) also flips `loading` true/false, and
  // gating on `loading` alone replaced whatever the user just saw (a
  // "Saved"/"Connected" confirmation, an open banner) with this skeleton on
  // every single action.
  if (loading && channels.length === 0) {
    return <FleetCardGridSkeleton cards={8} label="Loading channels" />;
  }

  // Slack's channel.connected from the backend means "OAuth app installed",
  // not "this agent owns a channel" — see isChannelConnected. Normalizing
  // it here means every reader below (grid pill via channelStatePill, and
  // the "already connected" banner prefill) agrees with the Properties
  // panel's own Channels count.
  const byId = new Map(channels.map((c) => [c.id, { ...c, connected: isChannelConnected(c, slackChannelBinding, telegramBotConnected) }]));

  function closeBanner() {
    setExpanded(null);
    setSelectedDoor(null);
    setByoToken("");
    setByoBotError(null);
    setByoBotSaved(false);
    setOauthError(null);
    setSlackChannelId("");
    setSlackBindError(null);
    setSlackBindSaved(false);
    setWechatAppId("");
    setWechatAppSecret("");
    setWechatVerifyToken("");
    setWechatWecomAgentId("");
    setWechatError(null);
    setWechatSaved(false);
    setWechatWebhookUrl(null);
  }

  function handleCardClick(platform: typeof CHANNEL_GRID_PLATFORMS[number], pill: { tone: string }) {
    // "Not configured here" must be a dead end before the click, matching the
    // Connectors tab — not a click-through to a banner whose only path ends
    // in a raw env-var error (e.g. "Set SLACK_CLIENT_ID..."). "Needs Gateway"
    // stays clickable — pairing a Gateway is a real, actionable next step.
    if (pill.tone === "locked") return;
    if (expanded === platform.id) {
      closeBanner();
      return;
    }
    setExpanded(platform.id);
    setSelectedDoor(null);
    setByoToken("");
    setByoBotError(null);
    // Initialize from the real backend state instead of always blank — a
    // door that's already connected (from a previous session, or another
    // tab) should say so on open, not show an empty form that looks like
    // nothing was ever saved. Discord has exactly one real door, so its
    // grid-level `connected` already means "byo bot token saved"; Telegram's
    // byo_bot door is a separate catalog item from the grid pill's own
    // sage_telegram_hosted status, so it rides in on telegramBotConnected
    // instead (see routes_fleet.py's fleet_agent_channels).
    setByoBotSaved(
      platform.id === "discord_bot" ? Boolean(byId.get("discord_bot")?.connected)
        : platform.id === "sage_telegram_hosted" ? telegramBotConnected
        : false,
    );
    setOauthError(null);
    setSlackChannelId("");
    setSlackBindError(null);
    setSlackBindSaved(platform.id === "slack" ? Boolean(slackChannelBinding) : false);
    setWechatAppId("");
    setWechatAppSecret("");
    setWechatVerifyToken("");
    setWechatWecomAgentId("");
    setWechatError(null);
    // wechat_official's `connected` comes straight off its own catalog item
    // (no separate "rides along" field, unlike Slack/Telegram's byo doors
    // above) — a real enabled agent_channel_binding IS the truth here.
    setWechatSaved(platform.id === "wechat_official" ? Boolean(byId.get("wechat_official")?.connected) : false);
    setWechatWebhookUrl(null);
  }

  // ── The unified channel grid ────────────────────────────────────────────
  // ONE array, ONE render loop, merged alphabetically, rendered as ONE
  // `.fleet-channel-grid` of square `.fleet-channel-card`s — first-party
  // cards (this agent's real, working Telegram/Slack/Discord/WhatsApp/Signal/
  // iMessage/WeChat setup, unchanged) and transported cards (real, working,
  // generated-form setup for whatever the transport carries that isn't one of
  // those platforms) are the SAME card. This is the literal fix for "two
  // components stacked is not one interface" — there used to be a grid of
  // cards followed by a completely separate `<OpenClawChannelsPanel>` section
  // below it.
  //
  // A CARD FACE IS ICON + LABEL + ONE PILL. NOTHING ELSE.
  // -----------------------------------------------------
  // A 2026-08-09 pass unified the two sections correctly and then rendered
  // the merged set as flat text rows, each carrying a subtitle, three state
  // chips, a full detail sentence AND an action button — ~26 of them, which
  // reads as one wall of text and was rejected outright. The merge was right;
  // the row shell was the defect. Everything past the pill now lives in the
  // panel a card OPENS (`activePlatform` for a first-party card,
  // `openclawDetail` for a transported one) — the three states are not
  // collapsed away, they are moved to where there is room for them and where
  // their remediation button already lived.
  //
  // First-party rows do NOT switch to OpenClaw's generated-credential-form
  // pattern even when this agent has a gateway. That was considered and
  // rejected: `openclaw_channel_setup_catalog()` (openclaw_channel_setup_
  // service.py) filters to `channel_lane_contract_service.OPENCLAW_ACTIVE_
  // CHANNELS`, which excludes every one of these 8 platforms today (none is
  // in `OPENCLAW_CUT_OVER_CHANNEL_IDS` — see CLAUDE.md). Writing a credential
  // through OpenClaw's config-patch RPC for a channel that isn't active would
  // still be accepted by the box (it doesn't know Empyralis's "active"
  // concept) but would never be enabled by `provision_openclaw_gateway`
  // (which only iterates `OPENCLAW_PERSONAL_CHANNELS`, the active set) —
  // a credential the owner believes is "set" that no inbound path will ever
  // use. That is a dead control wearing a real-looking form, which is worse
  // than the two-section split it would replace. The card is shared
  // regardless; only the panel it opens, and the backend that panel talks to,
  // differ — and only where the backend genuinely differs today.
  type UnifiedChannelCard = {
    key: string;
    label: string;
    iconSrc?: string;
    pill: { label: string; tone: "connected" | "gateway" | "locked" | "setup" };
    /** The one secondary signal a face carries: that this card opens a CHOICE.
     *  Null on every card with a single way in, which is most of them. Derived
     *  from the same door count that decides whether a picker is shown at all
     *  (channelDoorChoiceNote), on both halves of the grid — never a list of
     *  "these channels have variants". */
    waysNote: string | null;
    /** Whether connecting this card needs a computer paired to this agent —
     *  DERIVED from the card's own doors (channel-hardware-tier.ts), never
     *  from a list of channel names here. Drives the grid's two-tier filter
     *  and, inside the panel, the honest next step for a cloud-only agent. */
    tier: ChannelHardwareTier;
    /** The founder's authored "start here" marker, gated on the tier so it can
     *  never sit on a card that first demands hardware. */
    recommended: boolean;
    /** A card that cannot be opened at all is rendered `disabled` rather than
     *  clicked-into-a-dead-end (product law: no dead controls). Only
     *  first-party "Not configured here" is that — every transported card
     *  opens into something that at minimum explains itself. */
    disabled: boolean;
    active: boolean;
    open: () => void;
  };

  // ONE PLATFORM = ONE CARD, on both halves of the grid. The transport models
  // every variant of a platform as its own channel (Zalo ships as `zalo`,
  // `zalouser` and `zaloclawbot`), which put THREE Zalo cards in the same grid
  // where Telegram — the same concept, one platform reachable several ways —
  // is one card with two doors. Grouped structurally in channel-doors.ts, not
  // by a list of ids typed out here; see its comment for the two independent
  // axes and why WeCom and Weixin can never merge.
  const transportedPlatforms = groupTransportedChannels(
    openclaw.rows.map((row) => row.entry),
  );
  const openclawRowByKey = new Map(openclaw.rows.map((row) => [row.entry.channel_key, row]));

  // ...and ACROSS the two halves, which is where it was never enforced. The
  // transported rows ARE the backend's active catalog (a platform the
  // transport OWNS), so a first-party entry whose platform appears there has
  // been cut over and its card is the stale one — see channel-platform.ts for
  // why this is read off live data rather than a list of "these are dupes".
  // Both survivors of the 2026-08-14 cutover (`sage_telegram_hosted`,
  // `wechat_official`) drop out here; Slack and Discord keep their cards,
  // because their OpenClaw channels are superseded upstream and never reach
  // this catalog at all.
  const firstPartyGrid = planUnifiedChannelGrid(CHANNEL_GRID_PLATFORMS, transportedPlatforms).firstParty;

  const legacyCards: UnifiedChannelCard[] = firstPartyGrid.map((platform) => {
    const pill = channelStatePill(byId.get(platform.id));
    const doorPlanForCard = planChannelDoors(platform.id);
    const tier = channelHardwareTier(doorPlanForCard.doors);
    return {
      key: `first_party:${platform.id}`,
      label: platform.label,
      iconSrc: channelIconSrc(platform.id),
      pill,
      waysNote: channelDoorChoiceNote(doorPlanForCard),
      tier,
      recommended: showsRecommendedBadge(isChannelRecommended(platform.label), tier),
      disabled: pill.tone === "locked",
      active: expanded === platform.id,
      open: () => handleCardClick(platform, pill),
    };
  });

  // Rendered regardless of `agentGatewayId`. The catalog
  // (`useOpenClawChannelSetup`) is a property of the pinned transport, not
  // of any one box, and now loads with no gateway bound — see that hook's
  // doc comment. A cloud-only agent's transported cards read "Needs Gateway"
  // (via `remediationFor`'s `needs_hardware` kind, fed by `Boolean
  // (agentGatewayId)` inside the hook), the same pill the first-party
  // WhatsApp/Signal/iMessage cards already show in this state — never
  // absent from the grid, which is the defect this fixes.
  const openclawCards: UnifiedChannelCard[] = transportedPlatforms.map((platform) => {
    const rows = platform.variants
      .map((variant) => openclawRowByKey.get(variant.channel_key))
      .filter((row): row is NonNullable<typeof row> => Boolean(row));
    // A card face carries ONE pill for the whole platform. A platform is
    // ready if any way into it is; otherwise the base variant's own state
    // is what the customer is being asked to act on, which is the same
    // "single most actionable state" channelCardPill already picks.
    const pillRow = rows.find((row) => row.remediation.kind === "ready") ?? rows[0];
    return {
      key: `openclaw:${platform.key}`,
      label: platform.label,
      // Same lookup a first-party card does, on the `channel_key` verbatim —
      // no per-channel code here, and no list of which channels have a mark.
      // `channelIconSrc` reduces the key to its PLATFORM before looking a mark
      // up, which is what makes `openclaw_telegram` resolve to the same
      // telegram.svg `sage_telegram_hosted` used to; the exact-key table it
      // replaced is why every cut-over channel rendered a monogram beside an
      // asset that was already in `public/`. IRC, Yuanbao and Synology Chat
      // have no obtainable official mark and still fall through to the
      // neutral monogram tile, which is also what a channel the transport
      // adds tomorrow will get. Never a guessed or hand-drawn logo.
      iconSrc: channelIconSrc(platform.iconKey),
      pill: pillRow
        ? channelCardPill(pillRow.remediation)
        : { label: "Unknown", tone: "locked" as const },
      waysNote: channelDoorChoiceNote(planDoors(platform.doors)),
      tier: channelHardwareTier(platform.doors),
      // Structurally unreachable today (every transported door needs the box,
      // so the tier gate below can never pass) and computed the same way
      // regardless, so a transported lane that ever becomes hardware-free is
      // treated like any other card rather than by an exception.
      recommended: showsRecommendedBadge(
        isChannelRecommended(platform.label),
        channelHardwareTier(platform.doors),
      ),
      // "Needs Gateway" stays clickable, exactly like the first-party cards
      // in the same state (channelStatePill / handleCardClick) — pairing a
      // Gateway is a real, actionable next step, so this is never `disabled`.
      disabled: false,
      active: openclawDetailKey === platform.key,
      open: () => {
        setOpenclawDetailKey(platform.key);
        setOpenclawDoorKey(null);
        // Opening a channel always lands on the first screen its own state
        // implies — never on whatever was last being edited inside it.
        setOpenclawEditing(false);
      },
    };
  });

  // ORDER: what a person can finish today, first.
  //
  // Three ordering rules, composed rather than fused, because each answers a
  // different question and each is read on its own somewhere:
  //
  //   1. TIER        no computer needed before needs-a-computer, so the grid
  //                  opens with cards that can actually be completed right now
  //                  (derived — channel-hardware-tier.ts).
  //   2. RECOMMENDED the founder's one authored "start here" (Telegram), which
  //                  is why it is card #1 and not merely early.
  //   3. POPULARITY  how many people use the platform, not its first letter —
  //                  alphabetical opened the grid with ClickClack above
  //                  Discord. A PREFIX, never a membership test: a channel the
  //                  transport ships tomorrow is unranked, lands at the bottom
  //                  and sorts alphabetically among its peers, with no code
  //                  change. See channel-popularity.ts.
  // One exported comparator, not a chain spelled out here — see
  // compareChannelGridCards for why the test has to be able to import the
  // real order rather than re-type it.
  const unifiedChannelCards = [...legacyCards, ...openclawCards].sort(compareChannelGridCards);

  // THE TWO-TIER FILTER. Pure, and it decides its own visibility: the control
  // is rendered only when both tiers are actually populated, because a filter
  // that can only ever show everything is a dead control. `plan.selected` is
  // what is in force, which is not always what was asked for — a selection
  // naming a tier with no cards resolves to one that has some rather than
  // rendering an empty grid that reads as broken.
  const hardwareFilter = planChannelHardwareFilter(unifiedChannelCards, channelFilter);
  const visibleChannelCards = hardwareFilter.visible;

  // The transported PLATFORM whose panel is open, re-resolved from the live row
  // set on every render rather than captured at click time — provisioning and
  // a credential save both refresh those rows underneath an open panel, and a
  // captured copy would keep showing the state that made the owner click.
  const openclawPlatform = openclawDetailKey
    ? transportedPlatforms.find((platform) => platform.key === openclawDetailKey) || null
    : null;
  // The SAME door-count rule the first-party panel uses, run on the derived
  // doors: one way in opens straight into it, two or more ask first.
  const openclawDoorPlan = planDoors(openclawPlatform?.doors ?? []);
  const openclawActiveDoor =
    openclawDoorPlan.mode === "direct"
      ? openclawDoorPlan.door
      : openclawDoorPlan.mode === "picker"
        ? openclawDoorPlan.doors.find((door) => door.key === openclawDoorKey) || null
        : null;
  const openclawDetail = openclawActiveDoor ? openclawRowByKey.get(openclawActiveDoor.key) || null : null;

  // ONE QUESTION PER SCREEN. Which screen this panel is on is decided in one
  // pure function (channel-setup-flow.ts) rather than by a stack of ternaries
  // in the JSX — that stack is what let a credential field, two allowlists and
  // an owner-identity field all render at once whether or not the channel was
  // connected. The three rules it encodes (one question, settings only after
  // it works, a back affordance past the first step) are asserted in
  // channel-setup-flow.test.ts against the REAL door plan and the REAL
  // remediations, which no amount of reading this JSX could give us.
  // The honest answer for a transported card opened by a cloud-only agent.
  // Null the moment a computer exists, so this can never linger as a stale
  // instruction beside a working setup form.
  const openclawHardwareStep = channelHardwareNextStep(
    channelHardwareTier(openclawPlatform?.doors ?? []),
    Boolean(agentGatewayId),
  );
  const openclawFlow = planChannelSetupFlow({
    doorPlan: openclawDoorPlan,
    doorChosen: Boolean(openclawActiveDoor),
    remediation: openclawDetail?.remediation ?? null,
    connectMethod: openclawDetail
      ? connectMethodFor(openclawDetail.entry, openclawDetail.observed)
      : null,
    editingSettings: openclawEditing,
  });

  // DELETED with the note it fed (2026-08-21): the leftover
  // OPENCLAW_SUPERSEDED_CHANNELS labels with no first-party card in this tab
  // (SMS today) used to be named in a sentence under the grid. `formatChannelList`
  // stays in openclaw-channel-copy.ts with its own tests — the copy helper is
  // fine, the paragraph was not.

  // "the box could not be reached" and "the box answered fine but has never
  // had the transport installed" are different facts (2026-08-13 audit,
  // #2b) — this is the one place that decides both the banner text AND
  // whether a retry control makes sense, off the STRUCTURED reason code,
  // never a text-match on the message. See openclawObservedErrorBanner's
  // own doc comment.
  const openclawBanner = openclawObservedErrorBanner(openclaw.observedError, openclaw.observedErrorCode);
  const openclawRetryable = openclawBanner?.retryable ?? true;

  return (
    <div>
      {/* Channels vs. Apps reads as one undifferentiated "integrations"
          blob otherwise — this one-liner is the whole fix: it's how people
          reach the agent, not what the agent can use. */}
      <p className="fleet-tab-subtitle">Where people can message this agent</p>

      {openclaw.error ? (
        <div className="openclaw-banner" role="alert">
          <AlertTriangle size={14} aria-hidden />
          <span>{openclaw.error}</span>
        </div>
      ) : null}
      {openclawBanner ? (
        <div className="openclaw-banner" role="status">
          <AlertTriangle size={14} aria-hidden />
          <span>{openclawBanner.text}</span>
        </div>
      ) : null}

      {agentGatewayId ? (
        <div className="openclaw-toolbar">
          {/* No control that cannot work: when the box answered fine but
              never had the transport installed, retrying provision hits the
              exact same "not set up" answer every time — there is nothing
              this button could do here today. Refresh stays either way; it
              only re-reads state, so it is honest to show regardless. */}
          {openclawRetryable ? (
            <button
              type="button"
              // The hairline accent, never the fill. This is a REPAIR action
              // in a toolbar above a grid — it sits beside "Refresh", and
              // when the Channels tab is embedded in the creation sequence
              // the footer's own forward button is already the view's single
              // filled action. Two accent fills in one view is a bug
              // (CLAUDE.md, craft doctrine).
              className={`fleet-btn ${openclaw.repairable.length > 0 ? "fleet-btn--accent" : ""}`}
              onClick={() => void openclaw.provision(openclaw.repairable)}
              disabled={openclaw.busy !== null || openclaw.loading}
            >
              {openclaw.busy === "__all__" ? <Loader2 size={14} className="openclaw-spin" /> : <Plug size={14} />}
              {openclaw.repairable.length > 0
                ? `Set up ${openclaw.repairable.length} channel${openclaw.repairable.length === 1 ? "" : "s"}`
                : "Re-check this computer"}
            </button>
          ) : null}
          <button
            type="button"
            className="fleet-btn"
            onClick={() => void openclaw.refresh()}
            disabled={openclaw.busy !== null || openclaw.loading}
            aria-label="Refresh channel state"
          >
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      ) : null}

      {/* The two-tier split, made filterable — because a channel is now the
          only way a person talks to their agent, so "which of these can I
          finish today" is the first question the grid has to answer. Reuses
          the house .fleet-segmented control rather than inventing a second
          switch shape. Absent entirely when there is nothing to choose
          between. */}
      {hardwareFilter.options.length > 0 ? (
        <div className="fleet-channel-filter">
          <div className="fleet-segmented" role="tablist" aria-label="Filter channels by what they need">
            {hardwareFilter.options.map((option) => (
              <button
                key={option.id}
                type="button"
                role="tab"
                aria-selected={hardwareFilter.selected === option.id}
                className={`fleet-segmented-btn${hardwareFilter.selected === option.id ? " fleet-segmented-btn--active" : ""}`}
                onClick={() => setChannelFilter(option.id)}
              >
                {option.label}
                <span className="fleet-channel-filter-count">{option.count}</span>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div className="fleet-channel-grid">
        {visibleChannelCards.map((card) => (
          <button
            key={card.key}
            type="button"
            className={`fleet-channel-card${card.active ? " fleet-channel-card--active" : ""}`}
            onClick={card.open}
            disabled={card.disabled}
          >
            <span className="fleet-channel-card-icon">
              {card.iconSrc ? <img src={card.iconSrc} alt="" width={32} height={32} /> : card.label.charAt(0)}
            </span>
            <span className="fleet-channel-card-label">{card.label}</span>
            <span className={`fleet-channel-card-pill fleet-channel-card-pill--${card.pill.tone}`}>
              {card.pill.tone === "connected" ? <span className="fleet-channel-card-dot" /> : null}
              {card.pill.label}
            </span>
            {/* The card's ONE secondary line. A face is icon + label + one
                pill, so "Recommended" and the door-choice note share this
                single smaller, dimmer line rather than each becoming a second
                chip competing with the status pill. Absent entirely on a card
                with neither, which is most of them. */}
            {card.recommended || card.waysNote ? (
              <span className="fleet-channel-card-ways">
                {card.recommended ? (
                  <span className="fleet-channel-card-recommended">{CHANNEL_RECOMMENDED_BADGE}</span>
                ) : null}
                {card.waysNote}
              </span>
            ) : null}
          </button>
        ))}
      </div>

      {agentGatewayId && openclaw.loading ? (
        <p className="fleet-subtitle">Reading this agent&apos;s computer…</p>
      ) : null}

      {/* DELETED, 2026-08-21: a standalone paragraph under the grid reading
          "SMS connects elsewhere in Empyralis and isn't shown here." A
          professional tool labels; it does not lecture — and this one
          lectured about the ABSENCE of a card, which is the least
          actionable thing a sentence can say. The channel it named is
          reachable from where it actually lives; nothing here can act on
          it, so nothing here needs to say it. */}

      {/* What a transported channel's card opens into: the SAME
          .fleet-channel-banner shell a first-party card opens (header + icon +
          title + close, then a scrolling body), so the two kinds of card
          behave identically rather than one opening a panel and the other a
          differently-shaped modal. This is where the three states live — all
          three, never collapsed — beside the one sentence that says what to do
          and the one control that does it. */}
      {openclawPlatform ? (
        <div
          className="fleet-detail-backdrop"
          onClick={() => setOpenclawDetailKey(null)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              setOpenclawDetailKey(null);
            }
          }}
        >
          <div
            className="fleet-channel-banner"
            role="dialog"
            aria-modal="true"
            aria-labelledby="channel-detail-heading"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="fleet-channel-banner-header">
              <span className="fleet-channel-banner-icon">
                {channelIconSrc(openclawPlatform.iconKey)
                  ? <img src={channelIconSrc(openclawPlatform.iconKey)} alt="" width={24} height={24} />
                  : openclawPlatform.label.charAt(0)}
              </span>
              {/* The platform, plus which way in — but only once a CHOICE was
                  actually made. A single-door channel names itself and
                  nothing else; appending its one door's label would dress a
                  non-decision up as one. */}
              <span className="fleet-channel-banner-title" id="channel-detail-heading">
                {openclawPlatform.label}
                {openclawDoorPlan.mode === "picker" && openclawActiveDoor ? (
                  <span className="openclaw-title-door"> · {openclawActiveDoor.label}</span>
                ) : null}
              </span>
              <button
                type="button"
                className="fleet-detail-close fleet-detail-close--inline"
                onClick={() => setOpenclawDetailKey(null)}
                aria-label="Close"
              >
                <X size={16} strokeWidth={2} />
              </button>
            </div>

            <div className="fleet-channel-banner-body">
              {/* STEP 1 — WHICH WAY IN, and only when there is a choice. The
                   door COUNT is the whole input (planDoors), so a platform
                   reachable one way opens straight into its setup: an
                   intermediate screen offering one option is a dead click. */}
              {openclawFlow.screen === "pick_door" ? (
                <>
                  <p className="openclaw-question">How do you want to connect?</p>
                  <div className="fleet-wizard-options">
                    {openclawDoorPlan.doors.map((door) => {
                      const row = openclawRowByKey.get(door.key);
                      const connected = row?.remediation.kind === "ready";
                      const hardware = channelDoorHardwareState(door, {
                        hasHardware: !!agentGatewayId,
                        doorConnected: connected,
                      });
                      const hardwareNote = channelDoorHardwareNote(hardware);
                      return (
                        <button
                          key={door.key}
                          type="button"
                          className="fleet-wizard-option"
                          onClick={() => {
                            setOpenclawDoorKey(door.key);
                            setOpenclawEditing(false);
                          }}
                        >
                          <span className="fleet-wizard-option-label">
                            {door.label}
                            {connected ? (
                              <span className="fleet-wizard-option-connected">
                                <span className="fleet-channel-card-dot" /> Ready
                              </span>
                            ) : null}
                          </span>
                          <span className="fleet-wizard-option-body">{door.body}</span>
                          {/* The door's CONSEQUENCE, on its face, BEFORE it is
                              chosen — never a warning after a code has been
                              sent. Only ever present where the difference
                              between two doors is a real, specific risk. */}
                          {door.consequence ? (
                            <span className={`fleet-door-consequence fleet-door-consequence--${door.consequence.tone}`}>
                              {door.consequence.text}
                            </span>
                          ) : null}
                          {hardwareNote ? (
                            <span className="fleet-wizard-option-note">
                              <Cpu size={11} strokeWidth={2} aria-hidden /> {hardwareNote}
                            </span>
                          ) : null}
                        </button>
                      );
                    })}
                  </div>
                </>
              ) : null}

              {/* A back affordance on any step past the first, and never on
                  the first — `back` is null exactly when this screen IS the
                  first one, so no arrow ever points at nothing. */}
              {openclawFlow.back === "doors" ? (
                <PanelBackBar
                  label={openclawPlatform.label}
                  onBack={() => {
                    setOpenclawDoorKey(null);
                    setOpenclawEditing(false);
                  }}
                />
              ) : null}
              {openclawFlow.back === "connected" ? (
                <PanelBackBar label="Done" onBack={() => setOpenclawEditing(false)} />
              ) : null}

              {openclawDetail && openclawFlow.screen !== "pick_door" ? (
                <>
                  {/* STEP 2 — CONNECT. Instructions in the transport's own
                      words (generated: see channel-setup-flow.ts), then ONE
                      form. Never a credential field beside two allowlists and
                      an owner-identity field, which is what this whole screen
                      used to be. */}
                  {openclawFlow.screen === "connect" && agentGatewayId ? (
                    <>
                      <p className="openclaw-question">
                        {openclawFlow.connect === "paste"
                          ? setupQuestionFor(openclawDetail.entry.setup_wizard) ||
                            openclawDetail.entry.selection_label
                          : openclawDetail.entry.selection_label}
                      </p>
                      {openclawFlow.connect === "paste" ? (
                        <CredentialForm
                          gatewayId={agentGatewayId}
                          entry={openclawDetail.entry}
                          observed={openclawDetail.observed}
                          submitLabel="Connect"
                          onCancel={() => setOpenclawDetailKey(null)}
                          onSaved={async () => {
                            await openclaw.refresh({ silent: true });
                          }}
                        />
                      ) : (
                        /* A form BODY inside this same panel, exactly like
                           CredentialForm — never a second dialog stacked on
                           the one the card opened. `unknown_link` reaches
                           here too, deliberately: a pairing channel whose box
                           has not reported a link shape yet must still OFFER
                           a control, because starting the link is what
                           resolves the unknown. */
                        <ChannelLinkForm
                          gatewayId={agentGatewayId}
                          entry={openclawDetail.entry}
                          onCancel={() => setOpenclawDetailKey(null)}
                          onLinked={async () => {
                            await openclaw.refresh({ silent: true });
                          }}
                        />
                      )}
                    </>
                  ) : null}

                  {/* STEP 2, the two states whose whole remedy is one button.
                      The control DOES the work and says nothing about
                      mechanism; it verify-polls the box until its own state
                      catches up, then this screen becomes a different one. */}
                  {(openclawFlow.screen === "install" || openclawFlow.screen === "enable") && agentGatewayId ? (
                    <>
                      <p className="openclaw-question">
                        {openclawFlow.screen === "install"
                          ? `Set ${openclawDetail.entry.label} up on this computer`
                          : `Switch ${openclawDetail.entry.label} on`}
                      </p>
                      {(() => {
                        const channelKey = openclawDetail.entry.channel_key;
                        const setupState = openclaw.setupStateFor(channelKey);
                        // "Queued" is not busy — nothing is running on the box
                        // for this one yet, it is only waiting its turn.
                        const working = setupState === "working";
                        return (
                          <button
                            type="button"
                            className="fleet-btn fleet-btn--accent-fill"
                            onClick={() => openclaw.requestSetup(channelKey)}
                            disabled={setupState !== "idle"}
                          >
                            {working ? <Loader2 size={14} className="openclaw-spin" /> : null}
                            {setupState === "queued"
                              ? "Queued"
                              : working
                                ? "Setting up…"
                                : openclawDetail.remediation.kind === "install" ||
                                    openclawDetail.remediation.kind === "enable"
                                  ? openclawDetail.remediation.label
                                  : "Set up"}
                          </button>
                        );
                      })()}
                    </>
                  ) : null}

                  {/* STEP 3 — IT WORKS. A read-only summary, genuinely
                      different from the connect screen, with ONE Edit. */}
                  {openclawFlow.screen === "connected" && agentGatewayId ? (
                    <ConnectedChannelSummary
                      entry={openclawDetail.entry}
                      observed={openclawDetail.observed}
                      summary={openclawSummary}
                      onEdit={() => setOpenclawEditing(true)}
                    />
                  ) : null}

                  {/* STEP 4 — behind Edit, and structurally unreachable from
                      any state but `connected` (planChannelSetupFlow ignores
                      `editingSettings` everywhere else). */}
                  {openclawFlow.screen === "settings" && agentGatewayId ? (
                    <ChannelSettingsBody
                      gatewayId={agentGatewayId}
                      agentId={agentId}
                      entry={openclawDetail.entry}
                      observed={openclawDetail.observed}
                      onSaved={async () => {
                        await openclaw.refresh({ silent: true });
                        openclawSummary.reload();
                      }}
                    />
                  ) : null}

                  {/* The states with NO browser action at all. A control that
                      cannot work is not rendered — it is replaced by the one
                      sentence that says why, and each of these three says a
                      different thing rather than sharing one message. */}
                  {openclawFlow.screen === "elsewhere" ? (
                    <p className="openclaw-ready openclaw-ready--muted">
                      <Smartphone size={14} aria-hidden /> Link this one directly on the computer — there is nothing to
                      paste here.
                    </p>
                  ) : null}
                  {/* Two different facts, two different renderings — never
                      one shared paragraph. "You have no computer" has a real
                      next step and gets one; "this computer could not be
                      reached / declares nothing" genuinely has no action
                      here and stays the one honest sentence. */}
                  {openclawHardwareStep ? (
                    <HardwareNextStep step={openclawHardwareStep} href={hardwareHref} />
                  ) : openclawFlow.screen === "needs_hardware" || openclawFlow.screen === "unknown" || !agentGatewayId ? (
                    <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
                      {openclawDetail.remediation.detail}
                    </p>
                  ) : null}
                </>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {activePlatform && (
        <div
          className="fleet-detail-backdrop"
          onClick={closeBanner}
          onKeyDown={(e) => {
            // Own the Escape key here — otherwise it bubbles up to the page
            // wrapper's "Esc returns to the previous view" handler and
            // navigates the user away instead of just closing this banner.
            if (e.key === "Escape") {
              e.stopPropagation();
              closeBanner();
            }
          }}
        >
          <div className="fleet-channel-banner" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="fleet-channel-banner-header">
              <span className="fleet-channel-banner-icon">
                {channelIconSrc(activePlatform.id)
                  ? <img src={channelIconSrc(activePlatform.id)} alt="" width={24} height={24} />
                  : activePlatform.label.charAt(0)}
              </span>
              <span className="fleet-channel-banner-title">{activePlatform.label}</span>
              <button type="button" className="fleet-detail-close fleet-detail-close--inline" onClick={closeBanner} aria-label="Close">
                <X size={16} strokeWidth={2} />
              </button>
            </div>

            <div className="fleet-channel-banner-body">
              {/* ── THE DOOR PICKER ────────────────────────────────────────
                   Rendered ONLY when this channel has two or more real doors
                   (doorPlan.mode === "picker") and none is chosen yet. A
                   one-door channel never reaches this — its card opens
                   straight into the setup below, because an intermediate
                   screen offering one option is a dead click.

                   Everything that makes the choice a real choice is on the
                   face, BEFORE it is made: what the door is, what it costs
                   (Telegram's full account puts the owner's own number in
                   reach of a platform ban — that has already happened to a
                   real person here), whether it needs a computer, and whether
                   it is already connected. A door this agent cannot complete
                   is inert and says why — never a pick that fails after the
                   fact. */}
              {showDoorPicker && (
                <div className="fleet-wizard-options">
                  {doors.map((door) => {
                    const connected = isDoorConnected(door);
                    const hardware = doorHardware(door);
                    const hardwareNote = channelDoorHardwareNote(hardware);
                    const available = hardware !== "missing";
                    const face = (
                      <>
                        <span className="fleet-wizard-option-label">
                          {door.label}
                          {connected ? (
                            <span className="fleet-wizard-option-connected">
                              <span className="fleet-channel-card-dot" /> Connected
                            </span>
                          ) : null}
                        </span>
                        <span className="fleet-wizard-option-body">{door.body}</span>
                        {door.consequence ? (
                          <span className={`fleet-door-consequence fleet-door-consequence--${door.consequence.tone}`}>
                            {door.consequence.tone === "risk"
                              ? <AlertTriangle size={11} strokeWidth={2} aria-hidden />
                              : <Check size={11} strokeWidth={2} aria-hidden />}
                            {door.consequence.text}
                          </span>
                        ) : null}
                        {hardwareNote ? (
                          <span className={`fleet-wizard-option-note${available ? "" : " fleet-wizard-option-note--gateway"}`}>
                            <Cpu size={11} strokeWidth={2} aria-hidden /> {hardwareNote}
                          </span>
                        ) : null}
                      </>
                    );
                    return available ? (
                      <button
                        key={door.key}
                        type="button"
                        className="fleet-wizard-option"
                        onClick={() => setSelectedDoor(door.key)}
                      >
                        {face}
                      </button>
                    ) : (
                      <div key={door.key} className="fleet-wizard-option fleet-wizard-option--soon fleet-wizard-option--unavailable">
                        {face}
                      </div>
                    );
                  })}
                </div>
              )}

              {/* The door that was chosen, kept on screen while its setup
                   runs — the consequence line especially, which is the whole
                   reason the picker exists and would otherwise vanish the
                   moment it mattered most. Collapsed to ONE line the moment a
                   setup form is on screen (see ChosenDoorBar). "Change" is
                   hidden while a pairing is mid-flight: going back would
                   unmount PersonalChannelConnectPanel and silently discard a
                   half-entered code. */}
              {activeDoor && doorPlan.mode === "picker" && activeDoorAvailable ? (
                <ChosenDoorBar
                  door={activeDoor}
                  compact={setupDoorKey !== null}
                  onChange={canChangeDoor ? () => setSelectedDoor(null) : null}
                />
              ) : null}

              {/* A one-door channel has no face to carry its consequence, so
                   the fact rides above the form it opened straight into. Only
                   a real risk earns the line — never a reassurance nobody
                   asked for. */}
              {activeDoor && doorPlan.mode === "direct" && activeDoorAvailable && activeDoor.consequence?.tone === "risk" ? (
                <p className="fleet-door-consequence fleet-door-consequence--risk fleet-door-consequence--standalone">
                  <AlertTriangle size={12} strokeWidth={2} aria-hidden /> {activeDoor.consequence.text}
                </p>
              ) : null}

              {/* The door cannot be walked through on this agent as it stands.
                   Say so here, with the way out — and render NO setup form
                   below (see `setupDoorKey`), rather than a form whose first
                   action would fail. */}
              {activeDoor && !activeDoorAvailable ? (
                <div className="fleet-door-unavailable">
                  <p className="fleet-door-unavailable-title">
                    <Cpu size={14} strokeWidth={2} aria-hidden /> {activeDoor.label} needs a computer
                  </p>
                  <p className="fleet-channel-expand-hint">{channelDoorUnavailableReason(activeDoor)}</p>
                  {/* The same real next step the transported panel offers, so
                      a person meets one behaviour rather than two depending on
                      which half of the grid the card came from. */}
                  {hardwareHref ? (
                    <Link className="fleet-btn fleet-btn--accent" href={hardwareHref}>
                      Set up a computer
                    </Link>
                  ) : null}
                </div>
              ) : null}

              {/* Telegram: BYO bot token */}
              {activePlatform.id === "sage_telegram_hosted" && setupDoorKey === "byo_bot" && (
                <div style={{ marginTop: 12 }}>
                  {byoBotSaved ? (
                    <div className="fleet-channel-expand-success">
                      <Check size={16} strokeWidth={2} /> Bot token saved — this agent's own bot is live.
                    </div>
                  ) : (
                    <>
                      <p className="fleet-channel-expand-hint">Paste the token BotFather gave you when you created the bot.</p>
                      <label className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                        <span>Bot Token</span>
                        <input
                          type="password"
                          autoComplete="off"
                          value={byoToken}
                          onChange={(e) => { setByoToken(e.currentTarget.value); setByoBotError(null); }}
                        />
                      </label>
                      <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => void saveByoBotToken("telegram")} disabled={byoBotBusy}>
                        {byoBotBusy ? <><Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Saving…</> : "Save token"}
                      </button>
                    </>
                  )}
                  {byoBotError && <p className="fleet-channel-expand-error">{byoBotError}</p>}

                  {agent && (
                    <div style={{ marginTop: 16, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
                      <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer" }}>
                        <button
                          type="button"
                          role="switch"
                          aria-checked={firstContactReply}
                          aria-label="Reply to new contacts with an intro message"
                          className={`fleet-toggle${firstContactReply ? " is-on" : ""}`}
                          disabled={firstContactSaving}
                          onClick={() => void saveFirstContactReply(!firstContactReply)}
                        />
                        <span style={{ fontSize: 13 }}>
                          Reply to new contacts with an intro message
                          <span style={{ display: "block", fontSize: 12, color: "var(--text-muted)" }}>
                            The first time a stranger messages this bot, it identifies itself as an AI agent with a link. Off by default.
                          </span>
                        </span>
                      </label>
                    </div>
                  )}
                </div>
              )}

              {/* Discord: BYO bot token — one bot binds to exactly one agent
                   (discord_bot_provisioning_service.py), never a workspace-wide
                   credential. No OAuth path exists (or is needed): Discord's
                   Terms forbid automating a real user account, so a bot token
                   pasted from the developer portal is the only path. */}
              {activePlatform.id === "discord_bot" && setupDoorKey === "byo_bot" && (
                <div style={{ marginTop: 12 }}>
                  {byoBotSaved ? (
                    <div className="fleet-channel-expand-success">
                      <Check size={16} strokeWidth={2} /> Bot token saved — this agent's own bot is live.
                    </div>
                  ) : (
                    <>
                      <p className="fleet-channel-expand-hint">Paste the bot token from Discord's developer portal (Bot tab).</p>
                      <label className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                        <span>Bot Token</span>
                        <input
                          type="password"
                          autoComplete="off"
                          value={byoToken}
                          onChange={(e) => { setByoToken(e.currentTarget.value); setByoBotError(null); }}
                        />
                      </label>
                      <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => void saveByoBotToken("discord")} disabled={byoBotBusy}>
                        {byoBotBusy ? <><Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Saving…</> : "Save token"}
                      </button>
                    </>
                  )}
                  {byoBotError && <p className="fleet-channel-expand-error">{byoBotError}</p>}
                </div>
              )}

              {/* Slack: OAuth connects the workspace's Slack app (now agent-
                   scoped via metadata.agent_install_id — see startOAuth),
                   then this agent claims one channel within it. Slack's app
                   install is workspace-wide and can serve many agents,
                   unlike Discord's dedicated-bot-per-agent model, so
                   ownership here is per-channel, not per-connection. */}
              {activePlatform.id === "slack" && setupDoorKey && (
                <div style={{ marginTop: 12 }}>
                  <button
                    type="button"
                    className="fleet-btn fleet-btn--accent"
                    onClick={() => void startOAuth(activePlatform.id)}
                    disabled={oauthBusy === activePlatform.id}
                  >
                    {oauthBusy === activePlatform.id ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : null}
                    {oauthBusy === activePlatform.id ? "Starting…" : `Connect ${activePlatform.label}`}
                  </button>
                  {oauthError && <p className="fleet-channel-expand-error">{oauthError}</p>}

                  <div style={{ marginTop: 16, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
                    {slackBindSaved ? (
                      <div className="fleet-channel-expand-success">
                        <Check size={16} strokeWidth={2} /> Channel bound — this agent owns it.
                      </div>
                    ) : (
                      <>
                        <p className="fleet-channel-expand-hint">Give this agent one Slack channel to own (paste the channel ID from the channel&apos;s details).</p>
                        <label className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                          <span>Slack Channel ID</span>
                          <input
                            type="text"
                            autoComplete="off"
                            placeholder="C0123ABC456"
                            value={slackChannelId}
                            onChange={(e) => { setSlackChannelId(e.currentTarget.value); setSlackBindError(null); }}
                          />
                        </label>
                        <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => void saveSlackChannelBinding()} disabled={slackBindBusy}>
                          {slackBindBusy ? <><Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Saving…</> : "Bind channel"}
                        </button>
                      </>
                    )}
                    {slackBindError && <p className="fleet-channel-expand-error">{slackBindError}</p>}
                  </div>
                </div>
              )}

              {/* Telegram's "full_account" door, WhatsApp/Signal/iMessage's
                   first-party cards, and the panels they opened
                   (PersonalChannelConnectPanel's telegram_personal/
                   whatsapp_personal branches, LocalBridgeChannelStatus for
                   signal_personal, IMessageSetupPanel) were all DELETED
                   2026-08-14 (full OpenClaw channel cutover) along with the
                   gramjs/Baileys/local-bridge runtimes they configured.
                   WhatsApp/Signal/iMessage now appear only in the derived
                   OpenClaw grid; Telegram is single-door (Chatbot only), so
                   this activePlatform/setupDoorKey combination can no longer
                   occur — see channel-doors.ts's CHANNEL_DOORS comment. */}

              {/* WeChat Official Account / WeCom: BYO AppID+AppSecret (or
                   CorpID+CorpSecret+AgentId) — bidirectional, unlike the
                   separate "WeChat Work" catalog entry's outbound-only
                   incoming-webhook connector. Real credential validation
                   happens server-side (a live access_token fetch against
                   Tencent) before anything is stored — see
                   wechat_official_service.assign_wechat_official. */}
              {activePlatform.id === "wechat_official" && setupDoorKey === "app_credential_pair" && (
                <div style={{ marginTop: 12 }}>
                  {wechatSaved ? (
                    <div className="fleet-channel-expand-success">
                      <Check size={16} strokeWidth={2} /> Credentials saved — this agent&apos;s own WeChat/WeCom bot is live.
                    </div>
                  ) : (
                    <>
                      <p className="fleet-channel-expand-hint">
                        Paste the credentials from your own WeChat Official Account or WeCom (WeChat Work) admin console.
                      </p>
                      <label className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                        <span>Account type</span>
                        <select
                          value={wechatAccountKind}
                          onChange={(e) => { setWechatAccountKind(e.currentTarget.value === "wecom" ? "wecom" : "official_account"); setWechatError(null); }}
                        >
                          <option value="official_account">WeChat Official Account</option>
                          <option value="wecom">WeCom (WeChat Work)</option>
                        </select>
                      </label>
                      <label className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                        <span>{wechatAccountKind === "wecom" ? "CorpID" : "AppID"}</span>
                        <input
                          type="text"
                          autoComplete="off"
                          value={wechatAppId}
                          onChange={(e) => { setWechatAppId(e.currentTarget.value); setWechatError(null); }}
                        />
                      </label>
                      <label className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                        <span>{wechatAccountKind === "wecom" ? "CorpSecret" : "AppSecret"}</span>
                        <input
                          type="password"
                          autoComplete="off"
                          value={wechatAppSecret}
                          onChange={(e) => { setWechatAppSecret(e.currentTarget.value); setWechatError(null); }}
                        />
                      </label>
                      {wechatAccountKind === "wecom" && (
                        <label className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                          <span>AgentId</span>
                          <input
                            type="text"
                            autoComplete="off"
                            value={wechatWecomAgentId}
                            onChange={(e) => { setWechatWecomAgentId(e.currentTarget.value); setWechatError(null); }}
                          />
                        </label>
                      )}
                      <label className="gw-pair-panel-field" style={{ marginBottom: 10, display: "flex" }}>
                        <span>Token</span>
                        <input
                          type="password"
                          autoComplete="off"
                          value={wechatVerifyToken}
                          onChange={(e) => { setWechatVerifyToken(e.currentTarget.value); setWechatError(null); }}
                        />
                      </label>
                      <p className="fleet-channel-expand-hint" style={{ color: "var(--text-tertiary)" }}>
                        The Token must match exactly what you enter in WeChat/WeCom&apos;s own Server Configuration — it verifies inbound callbacks are really from Tencent. Message encryption ("safe mode") is not supported yet — leave EncodingAESKey / message encryption off.
                      </p>
                      <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => void saveWeChatCredentials()} disabled={wechatBusy}>
                        {wechatBusy ? <><Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> Saving…</> : "Save credentials"}
                      </button>
                    </>
                  )}
                  {wechatError && <p className="fleet-channel-expand-error">{wechatError}</p>}
                  {wechatWebhookUrl && (
                    <div style={{ marginTop: 16, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
                      <p className="fleet-channel-expand-hint">
                        Paste this URL into WeChat/WeCom&apos;s admin console as the Server Configuration callback URL:
                      </p>
                      <code style={{ display: "block", padding: "8px 10px", background: "var(--bg-inset)", borderRadius: 6, fontSize: 12, wordBreak: "break-all" }}>
                        {wechatWebhookUrl}
                      </code>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Connectors ──────────────────────────────────────────────────────────────

// Exported (2026-08-21) for AgentCreateCard's Apps step — the creation
// sequence shows the REAL connector picker, never a second, simplified copy
// of it that would drift from this one.
export function ConnectorsTab({
  workspaceId, agentId, agent,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const projectId = agent?.project_id || "";

  // Same one-liner treatment as ChannelsTab's own subtitle just above, kept
  // visible across every body state (loading/empty/picker) — the whole
  // point is telling the two tabs apart at a glance, not just once loaded.
  //
  // In the picker branch it is HANDED TO ConnectorPicker rather than drawn
  // above it, because the search field has to sit above everything it
  // filters and the founder's own sketch puts it above this line too. The
  // words stay defined here, once; only their POSITION belongs to the
  // picker. See ConnectorPicker's file header.
  const subtitle = <p className="fleet-tab-subtitle">Apps this agent can use</p>;

  if (!agent) {
    // Matches ConnectorPicker's OWN loading shape below it. Both are now the
    // square-card grid, so the two placeholders shown in sequence (this one,
    // then ConnectorPicker's once `agent` resolves) are the same box — they
    // used to change shape mid-load, and BOTH used to be the wide
    // picker-item card the Apps grid no longer renders at all.
    return (
      <div>
        {subtitle}
        <FleetCardGridSkeleton cards={9} label="Loading apps" />
      </div>
    );
  }

  if (!projectId) {
    return (
      <div>
        {subtitle}
        <EmptyState
          icon={Plug}
          title="No project assigned"
          body="Apps are shared per project — assign one before connecting them."
        />
      </div>
    );
  }

  return (
    <ConnectorPicker
      workspaceId={workspaceId}
      projectId={projectId}
      agentId={agentId}
      heading={subtitle}
    />
  );
}

// ── Shared: collapsed-by-default "Advanced" section ─────────────────────────

function Disclosure({
  label, defaultOpen = false, children,
}: { label: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`fleet-disclosure${open ? " is-open" : ""}`}>
      <button
        type="button"
        className="fleet-disclosure-trigger"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <ChevronRight size={13} strokeWidth={2} className="fleet-disclosure-chevron" />
        {label}
      </button>
      {open && <div className="fleet-disclosure-body">{children}</div>}
    </div>
  );
}

// ── Skills (MAN-310 skills-delivery) ────────────────────────────────────────
// A workspace owner's own reusable-procedure library for this agent — name +
// description (so the model can judge when it's relevant, matching Claude
// Code's own SKILL.md frontmatter) + a body of instructions, delivered to
// the Claude Agent SDK engine as a real SKILL.md file
// (server_modules/claude_agent_sdk_bridge.py's build_skills_plugin_dir).
// Deliberately minimal: no syntax highlighting, no templates, no
// marketplace — a name field, a description field, a body textarea, and
// enable/disable is what a first version needs. `kind` is always "skill"
// here; "command" is a real future value server-side but isn't wired to
// anything the model can invoke yet, so the client never offers it — an
// unusable option is a dead control (CLAUDE.md).
const EMPTY_SKILL_DRAFT = { name: "", description: "", body: "" };

function SkillsTab({
  workspaceId, agentId, agent, onSaved,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null; onSaved?: () => void }) {
  const skills = agent?.skills || [];
  // Editing state: null = list view, "new" = the add form, or an existing
  // skill's id = editing that row in place. Only one editor open at a time —
  // the same single-primary-action-per-view discipline the rest of this
  // file's inline editors (ScheduleSection, PersonaEditor) already follow.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState(EMPTY_SKILL_DRAFT);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Same agentId-keyed reset as PersonaEditor above — a Cmd+K palette swap
  // must not leave a half-typed draft for the PREVIOUS agent open against
  // the new one.
  useEffect(() => {
    setEditingId(null);
    setDraft(EMPTY_SKILL_DRAFT);
    setError(null);
  }, [agentId]);

  async function persist(nextSkills: FleetAgentSkill[]): Promise<boolean> {
    setError(null);
    try {
      const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { skills: nextSkills } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      onSaved?.();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
      return false;
    }
  }

  function startAdd() {
    setDraft(EMPTY_SKILL_DRAFT);
    setEditingId("new");
    setError(null);
  }

  function startEdit(skill: FleetAgentSkill) {
    setDraft({ name: skill.name, description: skill.description, body: skill.body });
    setEditingId(skill.id);
    setError(null);
  }

  function cancelEdit() {
    setEditingId(null);
    setDraft(EMPTY_SKILL_DRAFT);
    setError(null);
  }

  async function saveDraft() {
    const name = draft.name.trim();
    const body = draft.body.trim();
    if (!name || !body) {
      setError(name ? "Body can't be empty." : "Name can't be empty.");
      return;
    }
    setSaving(true);
    const isNew = editingId === "new";
    const nextSkills: FleetAgentSkill[] = isNew
      ? [...skills, {
          id: "", name, description: draft.description.trim(), body, kind: "skill", enabled: true,
        }]
      : skills.map((s) => (s.id === editingId ? { ...s, name, description: draft.description.trim(), body } : s));
    const ok = await persist(nextSkills);
    setSaving(false);
    if (ok) cancelEdit();
  }

  async function toggleEnabled(skill: FleetAgentSkill) {
    setTogglingId(skill.id);
    await persist(skills.map((s) => (s.id === skill.id ? { ...s, enabled: !s.enabled } : s)));
    setTogglingId(null);
  }

  async function deleteSkill(skill: FleetAgentSkill) {
    setDeletingId(skill.id);
    await persist(skills.filter((s) => s.id !== skill.id));
    setDeletingId(null);
  }

  const isEditingNew = editingId === "new";

  return (
    <div style={{ marginTop: 4 }}>
      <div className="fleet-detail-section-title" style={{ marginTop: 0, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span>Skills</span>
        {editingId === null && (
          <button type="button" className="fleet-btn fleet-btn--accent" onClick={startAdd}>
            <BookOpen size={14} strokeWidth={1.75} /> Add skill
          </button>
        )}
      </div>
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>
        A reusable procedure this agent can reach for on its own — the description is what tells it WHEN.
      </p>

      {(isEditingNew || editingId !== null) && (
        <div className="fleet-card" style={{ padding: "var(--space-3)", marginBottom: 12 }}>
          <div className="fleet-wizard-label" style={{ marginTop: 0 }}>Name</div>
          <input
            className="fleet-wizard-input"
            placeholder="Refund lookup"
            value={draft.name}
            onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
          />
          <div className="fleet-wizard-label">Description</div>
          <input
            className="fleet-wizard-input"
            placeholder="Use when a customer asks about a refund status."
            value={draft.description}
            onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
          />
          <div className="fleet-wizard-label">Instructions</div>
          <textarea
            className="fleet-wizard-input"
            placeholder={"1. Look up the order by number.\n2. Report the refund status in one sentence."}
            value={draft.body}
            onChange={(e) => setDraft((d) => ({ ...d, body: e.target.value }))}
            rows={6}
            style={{ height: "auto", padding: "8px 12px", resize: "vertical" }}
          />
          {error && <p className="fleet-channel-expand-error" style={{ marginTop: 4 }}>{error}</p>}
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button type="button" className="fleet-btn" disabled={saving} onClick={cancelEdit}>
              Cancel
            </button>
            <button
              type="button"
              className="fleet-btn fleet-btn--accent"
              disabled={saving || !draft.name.trim() || !draft.body.trim()}
              onClick={saveDraft}
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      )}

      {!agent ? (
        // `agent` is `null` both while `useFleetAgents` is still loading AND
        // when the agent genuinely doesn't exist — the same ambiguity
        // ConnectorsTab/ChannelsTab already treat as "still loading" (see
        // their own `!agent` branches above). This tab used to skip that
        // check and compute `skills = agent?.skills || []` straight into
        // the empty state, so a fresh page load showed "No skills yet"
        // before the fetch that would say whether that's true had
        // necessarily finished.
        <FleetToggleRowsSkeleton rows={3} trailing="button" label="Loading skills" />
      ) : skills.length === 0 && editingId === null ? (
        <EmptyState
          icon={BookOpen}
          title="No skills yet"
          body="Give this agent a named, reusable procedure — like a refund-lookup checklist — and it will reach for it on its own when the description matches what's being asked."
        />
      ) : (
        <div className="fleet-config" style={{ padding: 0 }}>
          {skills.map((skill) => (
            <div key={skill.id} className="fleet-toggle-row">
              <div style={{ minWidth: 0 }}>
                <div className="fleet-toggle-row-label">{skill.name}</div>
                {skill.description && <div className="fleet-toggle-row-desc">{skill.description}</div>}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                <button
                  type="button"
                  className="fleet-btn"
                  disabled={togglingId === skill.id}
                  onClick={() => toggleEnabled(skill)}
                  title={skill.enabled ? "Disable this skill" : "Enable this skill"}
                >
                  {skill.enabled ? "Enabled" : "Disabled"}
                </button>
                <button
                  type="button"
                  className="fleet-icon-btn"
                  onClick={() => startEdit(skill)}
                  aria-label={`Edit ${skill.name}`}
                  title="Edit"
                >
                  <Pencil size={14} strokeWidth={1.75} />
                </button>
                <button
                  type="button"
                  className="fleet-icon-btn"
                  disabled={deletingId === skill.id}
                  onClick={() => deleteSkill(skill)}
                  aria-label={`Delete ${skill.name}`}
                  title="Delete"
                >
                  <Trash2 size={14} strokeWidth={1.75} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {editingId === null && error && <p className="fleet-channel-expand-error" style={{ marginTop: 8 }}>{error}</p>}
    </div>
  );
}

// ── Capabilities (image/video generation, TTS/STT) ─────────────────────────
// See server_modules/agent_capability_service.py for the resolver this
// surfaces, and its module docstring for the full "which 4 pre-existing
// pieces this unifies" audit, plus its "BYOK IS OPENAI/ANTHROPIC-ONLY" note
// that this section's UI encodes. Same platform_credits/byok_api spectrum
// the Model tab already uses for the chat model, one level down — no
// separate enable toggle here: choosing a provider that resolves IS the
// enable, so image_generation's tool (generate_image) just appears in this
// agent's toolset the moment a provider below shows "Ready".
//
// Founder's hard rule: customers never hunt for or paste a raw API key,
// except OpenAI/Anthropic. Researched every media provider in this
// catalog (OpenAI, Stability, ElevenLabs, Runway) plus the obvious
// mainstream alternatives — none offer OAuth "authorize your account" for
// API access, all are bearer-key-only. So unlike the Model tab's
// MODE_LABELS (generic "Your own API key" across a dozen chat-model
// providers), every row here only ever has ONE legitimate BYOK provider —
// OpenAI — enforced server-side too (CapabilityProviderOption.supports_byok
// is only ever True for "openai"; store_capability_secret_patch and
// validate_capability_config_patch both reject anything else even if a
// client bypasses this UI). That key is managed ONCE, below, instead of
// pasted per row, and every non-OpenAI provider is platform-credits-only
// (priced, no paste box) or "not available yet" if it has no
// platform-credits path either.
const CAPABILITY_MODE_LABELS: Record<"platform_credits" | "byok_api", string> = {
  platform_credits: "Platform credits",
  byok_api: "Your OpenAI key",
};

function formatCapabilityPrice(usd: number | null | undefined, unit: string | null | undefined): string | null {
  if (usd == null || !unit) return null;
  return `~$${usd.toFixed(usd < 0.01 ? 3 : 2)} / ${unit}`;
}

function CapabilityRow({
  capability, busy, onModeChange, onFocusSharedKey,
}: {
  capability: FleetCapability;
  busy: boolean;
  onModeChange: (mode: ProviderMode, provider: string) => void;
  onFocusSharedKey: () => void;
}) {
  const [provider, setProvider] = useState(capability.provider);
  // Resynced whenever the server state changes (after a save, or switching
  // agents) so this never drifts from truth.
  const [uiMode, setUiMode] = useState<ProviderMode>(capability.mode);
  useEffect(() => {
    setUiMode(capability.mode);
    setProvider(capability.provider);
  }, [capability.mode, capability.provider]);

  const selectedOption = capability.providers.find((p) => p.id === provider) || capability.providers[0];
  const stubbed = selectedOption ? !selectedOption.live : false;
  const platformOptions = capability.providers.filter((p) => p.supports_platform_credits);
  const canPlatformCredits = platformOptions.length > 0;
  // Only OpenAI ever has supports_byok=true (see module note above) — this
  // is really "does this capability register an OpenAI option at all."
  const canByok = capability.providers.some((p) => p.supports_byok);
  const priceText = uiMode === "platform_credits"
    ? formatCapabilityPrice(selectedOption?.platform_price_usd, selectedOption?.platform_price_unit)
    : null;

  return (
    <div className="fleet-toggle-row" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
        <div style={{ minWidth: 0 }}>
          <div className="fleet-toggle-row-label">{capability.label}</div>
          <div className="fleet-toggle-row-desc">
            {capability.available
              ? `Ready — ${CAPABILITY_MODE_LABELS[capability.mode]} · ${selectedOption?.label || capability.provider}${priceText ? ` · ${priceText}` : ""}`
              // Founder's standing rule: no "coming soon" anywhere in
              // Capabilities. `capability.message` is server-authored and,
              // for a stubbed (not-yet-wired) provider, literally reads
              // "... is coming soon — not wired up yet." — deliberately not
              // trusted here for that case; a stubbed row always gets this
              // neutral, non-time-promising line instead, same words a
              // genuinely absent capability already used lower down.
              : stubbed ? "Not available on this deployment yet." : capability.message || "Not configured yet."}
          </div>
          {!capability.tool_gated && (
            <div className="fleet-toggle-row-desc">Used automatically — no separate tool to enable.</div>
          )}
        </div>
        <span
          className={`fleet-badge${capability.available ? "" : " fleet-badge--muted"}`}
          style={{ marginLeft: 0, flexShrink: 0 }}
        >
          {capability.available ? "Ready" : "Not configured"}
        </span>
      </div>

      {(canPlatformCredits || canByok) ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          {uiMode === "platform_credits" && canPlatformCredits && (
            <select
              className="fleet-wizard-input"
              style={{ maxWidth: 280, width: "auto" }}
              value={provider}
              disabled={busy}
              onChange={(e) => {
                const next = e.currentTarget.value;
                setProvider(next);
                onModeChange("platform_credits", next);
              }}
            >
              {platformOptions.map((p) => {
                const price = formatCapabilityPrice(p.platform_price_usd, p.platform_price_unit);
                // No "(coming soon)" suffix (founder's rule — see the
                // stubbed-message note above) — a not-yet-wired option just
                // shows its plain label with no price, same as any other
                // option this catalog can't yet quote a price for.
                return (
                  <option key={p.id} value={p.id}>
                    {p.label}{p.live && price ? ` — ${price}` : ""}
                  </option>
                );
              })}
            </select>
          )}

          <div style={{ display: "inline-flex", gap: 6 }} role="tablist" aria-label={`${capability.label} mode`}>
            {canPlatformCredits && (
              <button
                type="button"
                role="tab"
                aria-selected={uiMode === "platform_credits"}
                className={`fleet-btn${uiMode === "platform_credits" ? " fleet-btn--accent" : ""}`}
                disabled={busy}
                onClick={() => {
                  setUiMode("platform_credits");
                  const fallback = platformOptions.some((p) => p.id === provider) ? provider : (platformOptions[0]?.id || provider);
                  setProvider(fallback);
                  onModeChange("platform_credits", fallback);
                }}
              >
                {CAPABILITY_MODE_LABELS.platform_credits}
              </button>
            )}
            {canByok && (
              <button
                type="button"
                role="tab"
                aria-selected={uiMode === "byok_api"}
                className={`fleet-btn${uiMode === "byok_api" ? " fleet-btn--accent" : ""}`}
                disabled={busy}
                onClick={() => { setUiMode("byok_api"); onModeChange("byok_api", "openai"); }}
              >
                {CAPABILITY_MODE_LABELS.byok_api}
              </button>
            )}
          </div>
        </div>
      ) : (
        <p className="fleet-toggle-row-desc" style={{ marginTop: 0 }}>Not available on this deployment yet.</p>
      )}

      {/* No per-row paste box any more — byok_api always means the ONE
          shared OpenAI key managed above CapabilitiesTab's list. If it
          isn't saved yet, point there instead of asking again here. */}
      {uiMode === "byok_api" && !capability.has_byok_key && (
        <button type="button" className="fleet-link" onClick={onFocusSharedKey}>
          Add your OpenAI key above
        </button>
      )}
    </div>
  );
}

function CapabilitiesTab({
  workspaceId, agentId,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const { capabilities, isMaster, loading, refresh } = useFleetAgentCapabilities(workspaceId, agentId);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openaiKeyDraft, setOpenaiKeyDraft] = useState("");
  const [openaiKeyBusy, setOpenaiKeyBusy] = useState(false);
  const openaiKeyInputRef = useRef<HTMLInputElement | null>(null);

  async function saveMode(capabilityId: string, mode: ProviderMode, provider: string) {
    setPending(capabilityId);
    setError(null);
    try {
      const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { capability_config: { [capabilityId]: { mode, provider } } } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update this capability.");
    } finally {
      setPending(null);
    }
  }

  // A single OpenAI key covers every OpenAI-eligible capability (image
  // generation, text-to-speech, speech-to-text) — paste it once here
  // instead of once per row below. Fans out to the same per-capability
  // key endpoint each row used to call individually; saving switches each
  // of those capabilities to "Your OpenAI key" (fleet_set_agent_capability_key's
  // existing, tested behavior — pasting a key IS choosing byok for it), and
  // any row can still be switched back to Platform credits afterward
  // without losing the saved key (removing it is a separate action, below).
  const openaiEligible = capabilities.filter((c) => c.providers.some((p) => p.id === "openai" && p.supports_byok));
  const openaiSavedCount = openaiEligible.filter((c) => c.has_byok_key && c.provider === "openai").length;
  const openaiKeySaved = openaiSavedCount > 0;

  async function saveSharedOpenAIKey() {
    const apiKey = openaiKeyDraft.trim();
    if (!apiKey || openaiEligible.length === 0) return;
    setOpenaiKeyBusy(true);
    setError(null);
    try {
      const results = await Promise.all(openaiEligible.map(async (c) => {
        const res = await fleetAuthorizedFetch(
          `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-capabilities/key?agent_id=${encodeURIComponent(agentId)}`,
          {
            method: "POST",
            credentials: "include",
            headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
            body: JSON.stringify({ capability: c.id, provider: "openai", api_key: apiKey }),
          },
        );
        const data = await res.json().catch(() => ({}));
        return res.ok && data?.ok !== false;
      }));
      if (results.some((ok) => !ok)) throw new Error("Saved for some capabilities but not all — try again.");
      setOpenaiKeyDraft("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save your OpenAI key.");
    } finally {
      setOpenaiKeyBusy(false);
    }
  }

  async function removeSharedOpenAIKey() {
    const targets = capabilities.filter((c) => c.has_byok_key && c.provider === "openai");
    if (targets.length === 0) return;
    setOpenaiKeyBusy(true);
    setError(null);
    try {
      const results = await Promise.all(targets.map(async (c) => {
        const res = await fleetAuthorizedFetch(
          `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-capabilities/key?agent_id=${encodeURIComponent(agentId)}&capability=${encodeURIComponent(c.id)}`,
          { method: "DELETE", credentials: "include", headers: buildCookieAuthHeaders("DELETE", {}) },
        );
        const data = await res.json().catch(() => ({}));
        return res.ok && data?.ok !== false;
      }));
      if (results.some((ok) => !ok)) throw new Error("Removed for some capabilities but not all — try again.");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not remove your OpenAI key.");
    } finally {
      setOpenaiKeyBusy(false);
    }
  }

  function focusSharedKeyInput() {
    openaiKeyInputRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    openaiKeyInputRef.current?.focus();
  }

  if (loading) {
    return <FleetToggleRowsSkeleton rows={5} trailing="switch" label="Loading capabilities" />;
  }

  if (isMaster) {
    return (
      <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
        This is the operator agent — its capabilities resolve the same way (platform credits by default), but aren&apos;t gated behind a toggle here since it already has unrestricted tool access.
      </p>
    );
  }

  return (
    <div className="fleet-config">
      {/* Founder feedback (copy discipline pass): dropped the 3-sentence
          platform-credits/OpenAI-key primer that used to open this tab —
          the OpenAI key section right below already states, at the one
          input it's actually about, that it covers image/TTS/STT together
          (see its own hint), and every other row's own label already says
          what it costs. Nothing here needed a paragraph to say it twice. */}
      {openaiEligible.length > 0 && (
        <div className="fleet-channel-expand" style={{ marginBottom: 12 }}>
          <div className="fleet-toggle-row-label">Your OpenAI key</div>
          <p className="fleet-channel-expand-hint" style={{ marginTop: 2, marginBottom: 8 }}>
            {openaiKeySaved
              ? `Saved — covers ${openaiSavedCount} of ${openaiEligible.length} capabilities below. Paste a new key to replace it.`
              : "Covers image generation, text-to-speech, and speech-to-text at once — paste it here instead of on every row below."}
          </p>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input
              ref={openaiKeyInputRef}
              type="password"
              className="fleet-wizard-input"
              style={{ maxWidth: 320 }}
              aria-label="OpenAI API key"
              placeholder={openaiKeySaved ? "Key saved — paste a new one to replace it" : "Paste your OpenAI API key"}
              value={openaiKeyDraft}
              disabled={openaiKeyBusy}
              onChange={(e) => setOpenaiKeyDraft(e.currentTarget.value)}
            />
            <button
              type="button"
              className="fleet-btn fleet-btn--accent"
              disabled={openaiKeyBusy || !openaiKeyDraft.trim()}
              onClick={saveSharedOpenAIKey}
            >
              {openaiKeyBusy ? "Saving…" : "Save key"}
            </button>
            {openaiKeySaved && (
              <button type="button" className="fleet-btn" disabled={openaiKeyBusy} onClick={removeSharedOpenAIKey}>
                Remove key
              </button>
            )}
          </div>
        </div>
      )}

      {capabilities.map((cap) => (
        <CapabilityRow
          key={cap.id}
          capability={cap}
          busy={pending === cap.id}
          onModeChange={(mode, provider) => saveMode(cap.id, mode, provider)}
          onFocusSharedKey={focusSharedKeyInput}
        />
      ))}
      {error && <p className="fleet-channel-expand-error">{error}</p>}
    </div>
  );
}

// ── Model ───────────────────────────────────────────────────────────────────

import {
  BYOK_PROVIDERS, SUBSCRIPTION_PROVIDERS, LOCAL_PROVIDERS, MODE_LABELS,
  COMING_SOON_MODES, COMING_SOON_NOTE, runtimeForProvider, normalizeCliRuntime, type ProviderMode,
  FREEFORM_MODEL_PROVIDERS, modelsForProvider, defaultModelForProvider,
  isRecommendedModel, isLargeModel, LARGE_MODEL_WARNING,
  REASONING_EFFORT_OPTIONS, REASONING_EFFORT_SUPPORTED_MODES, reasoningEffortLabel,
  type CliSubscriptionRuntime,
} from "./fleet-provider-constants";
import {
  GatewayBoxPicker,
  connectionPresentation,
  gatewayId,
  gatewayLabel,
  gatewayRuntimeReady,
  hardwarePlacementIsBrainBound,
  resolveHardwarePlacement,
  useWorkspaceGateways,
  type FleetGateway,
  type HardwarePlacementTone,
} from "./gateway-box-picker";
import {
  resolveDisplayMode,
  seedSelectedModel,
  saveAgentModelConfig,
  PLATFORM_CREDITS_TIER_OPTIONS,
  PLATFORM_CREDITS_MODEL_BY_TIER,
  platformCreditsTierForModel,
  useCodexModelCatalog,
  visibleCodexModels,
  useByokModelCatalog,
  visibleByokModels,
} from "./fleet-model-config";
import { planCodexReasoningPicker } from "./codex-reasoning-options";

/** Label for a <select> model option — appends "(Recommended)" to the
 *  balanced/mid-tier pick every provider's picker pre-selects (see
 *  DEFAULT_MODEL_BY_PROVIDER's own doc comment for how that pick is chosen),
 *  so the reason it's already selected is visible, not just implicit. */
function modelOptionLabel(provider: string, modelId: string): string {
  return isRecommendedModel(provider, modelId) ? `${modelId} (Recommended)` : modelId;
}

/** Founder's rule (2026-07-30): warn — don't block — when the user manually
 *  picks a model this mirror knows is the large/premium tier for its
 *  provider, so a routine turn doesn't silently burn a subscription's daily
 *  limit. Renders nothing when the provider has no evidenced large-tier set
 *  (isLargeModel) or the current pick isn't in it. */
function ModelSizeWarning({ provider, model }: { provider: string; model: string }) {
  if (!isLargeModel(provider, model)) return null;
  return <p className="fleet-channel-expand-error" style={{ margin: 0 }}>{LARGE_MODEL_WARNING}</p>;
}

// Phase 7B: preset / hardware-lock / context-policy / today's cost, shown at
// the top of the Model tab so the agent's governance + spend are visible.
function AgentModelSummary({ workspaceId, agentId, agent }: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const [costPeriod, setCostPeriod] = useState<CostPeriod>("day");
  const [cost, setCost] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=agent&id=${encodeURIComponent(agentId)}&period=${costPeriod}`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d) return;
        // Same bug, same fix as the Properties panel's identical cost stat
        // (see FleetAgentDetail's own usage-fetch effect above): `totals`
        // isn't date-filtered by the backend (summarize_usage only
        // date_trunc's `buckets`), so it's an all-time sum — using it here
        // silently mislabels all-time spend as the selected period's. Match
        // the bucket the current period truncates to instead.
        if (Array.isArray(d.buckets)) {
          const periodKey = currentPeriodBucketKey(costPeriod, new Date());
          const currentBucket = d.buckets.find((b: UsageBucket) => String(b.bucket || "").slice(0, 10) === periodKey);
          setCost(Number(currentBucket?.usd_cost ?? 0));
        } else {
          setCost(0);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [workspaceId, agentId, costPeriod]);
  const preset = (agent?.capability_preset || "standard").toLowerCase();
  const locked = !!agent?.hardware_access_locked;
  const pol = agent?.context_policy || {};
  const maxTok = Number(pol.max_context_tokens || 0);
  const action = pol.on_context_full === "fresh_session" ? "fresh session" : "compact";
  return (
    <div className="fleet-config" style={{ marginBottom: 20 }}>
      <div className="fleet-config-row">
        <span className="fleet-config-label">Capability preset</span>
        <span className="fleet-config-value">
          <span className="fleet-badge fleet-badge--preset">{preset}</span>
          {locked && <span className="fleet-badge fleet-badge--lock"><Lock size={11} strokeWidth={2} /> hardware locked</span>}
        </span>
      </div>
      <div className="fleet-config-row">
        <span className="fleet-config-label">Context policy</span>
        <span className="fleet-config-value">
          {maxTok > 0 ? `${formatNumber(maxTok)} tokens` : "model default"} → {action}
        </span>
      </div>
      {/* Moved down from the permanent Properties panel (2026-08): real,
          live runtime state — runtime_run_delegation_service.py actually
          blocks a specialist from delegating when this is false, it isn't
          a stub — but there is still no direct toggle for it anywhere in
          the UI (fleet_configure_agent's subagents_enabled key is only
          reachable by asking the agent itself to change it, "Ask AI to
          configure"). That combination — real state, no control — belongs
          in the Configure sheet next to the other read-only governance
          facts (preset/context policy above), not pinned to every tab via
          the Properties rail. */}
      <div className="fleet-config-row">
        <span className="fleet-config-label">Sub-agents</span>
        <span className="fleet-config-value">{agent?.subagents_enabled ? "Enabled" : "Disabled"}</span>
      </div>
      <div className="fleet-config-row">
        <span className="fleet-config-label" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          {costPeriodLabel(costPeriod)}
          <CostPeriodToggle period={costPeriod} onChange={setCostPeriod} />
        </span>
        <span className="fleet-config-value">{cost === null ? "…" : formatUsd(cost)}</span>
      </div>
    </div>
  );
}

const _CONTEXT_FULL_OPTIONS: { value: string; label: string }[] = [
  { value: "compact", label: "Compact — summarize and continue" },
  { value: "fresh_session", label: "Fresh session — start a new thread" },
];

function ContextPolicySection({
  workspaceId, agentId, agent,
}: { workspaceId: string; agentId: string; agent: FleetAgent }) {
  const pol = agent.context_policy || {};
  const [maxTokens, setMaxTokens] = useState(String(pol.max_context_tokens || 0));
  const [onFull, setOnFull] = useState(pol.on_context_full === "fresh_session" ? "fresh_session" : "compact");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const parsed = parseInt(maxTokens, 10);
    if (Number.isNaN(parsed) || parsed < 0) {
      setError("Max tokens must be 0 (model default) or a positive number.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { context_policy: { max_context_tokens: parsed, on_context_full: onFull } } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <label className="fleet-wizard-label" style={{ marginTop: 0 }}>Max context tokens</label>
      <input
        className="fleet-wizard-input"
        type="number"
        min={0}
        value={maxTokens}
        onChange={(e) => { setMaxTokens(e.currentTarget.value); setSaved(false); }}
        placeholder="0 = model default"
      />
      <label className="fleet-wizard-label">When context fills up</label>
      <select className="fleet-wizard-input" value={onFull} onChange={(e) => { setOnFull(e.currentTarget.value); setSaved(false); }}>
        {_CONTEXT_FULL_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 12 }}>
        {/* Distinct label is load-bearing, not cosmetic: this save is scoped
            to ONLY context_policy — it does not touch model/provider/gateway
            at all. It used to just say "Save" like the unrelated Model-tab
            save button above it (ModelTab's own save(), a separate PATCH
            call) — same label, same style, stacked on the same page, so a
            model/subscription change made above was silently never
            persisted by clicking this one. Confirmed live: selecting "Your
            subscription" -> Codex -> a Gateway, then clicking only this
            button, left model_config completely unchanged. */}
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={save} disabled={saving}>
          {saving ? "Saving…" : saved ? <><Check size={14} strokeWidth={2} /> Saved</> : "Save context settings"}
        </button>
        {error && <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</span>}
      </div>
    </div>
  );
}

/** channelStatePill-style dynamic hint for the "Your subscription" option —
 *  real hardware state instead of a static note. cli_subscription is real
 *  and savable (BYO-brain Phase 3), but still needs a paired Gateway with
 *  the CLI installed and signed in — this names what's actually missing
 *  rather than a generic lock message. We don't know which CLI (Claude Code,
 *  Codex, Grok Build, or Cursor CLI) they'll pick until the option is
 *  expanded, so this checks for any of the four. */
function cliSubscriptionHint(gateways: FleetGateway[]): string {
  if (gateways.length === 0) return "Needs a paired computer — none paired yet";
  const anyReady = gateways.some(
    (g) => (["claude_code", "codex", "grok_build", "cursor_cli"] as const).some((r) => gatewayRuntimeReady(g, r)),
  );
  return anyReady ? "A paired computer has a CLI ready" : "No paired computer has a subscription CLI ready";
}

function ModelTab({
  workspaceId, agentId, agent, onSaved,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  /** Called after a successful save so the caller can refetch — the
   *  Properties column's "Model" row and this tab's own "Current state"
   *  block both read model_config off the SAME `agent` prop, which
   *  otherwise doesn't catch up until the next ~30s poll (see HardwareTab's
   *  identical onSaved for its own placement row). */
  onSaved?: () => void;
}) {
  const config = agent?.model_config || {};
  const [mode, setMode] = useState<ProviderMode>(resolveDisplayMode(config));
  const [provider, setProvider] = useState<string>(config.provider || "");
  const [gatewayBinding, setGatewayBinding] = useState<string>(config.gateway_binding || "");
  const { gateways: cliGateways } = useWorkspaceGateways(workspaceId);
  const cliRuntime = normalizeCliRuntime(runtimeForProvider(provider));
  // U3-K: this agent's project, so an unset gateway_binding can say what it
  // actually resolves to at turn time (specialist_runtime_context's project-
  // default fallback) rather than reading as unset/broken. Cheap — the
  // workspace's project list is already polled elsewhere on this page tree
  // and shared via useSharedPolledResource, so this doesn't add a new
  // network round trip of its own.
  const { projects } = useFleetProjects(workspaceId);
  const agentProject = projects.find((p) => p.id === agent?.project_id);
  const inheritedGatewayLabel =
    !gatewayBinding.trim() && agentProject?.default_gateway_id
      ? agentProject.default_gateway_label || agentProject.default_gateway_id
      : null;
  // URGENT fix (2026-08-14): ask the box that will actually run this
  // agent's turns what its Codex CLI can really run, rather than trusting
  // the hand-typed MODELS_BY_PROVIDER mirror — see fleet-model-config.ts's
  // own doc comment on useCodexModelCatalog for why. Effective gateway id
  // matches inheritedGatewayLabel's own fallback above (explicit binding,
  // else the project default) so this never queries the wrong box.
  const effectiveGatewayId = gatewayBinding.trim() || agentProject?.default_gateway_id || "";
  const codexModelCatalog = useCodexModelCatalog(workspaceId, effectiveGatewayId, cliRuntime);
  // byok_api's own live catalog — asks the provider's real API what this
  // workspace's already-saved credential can actually see, instead of
  // trusting MODELS_BY_PROVIDER's hand-typed mirror. Fetches for every
  // BYOK provider change; a no-op when `provider` isn't byok_api's own
  // selection (the effect below only reads it while mode === "byok_api").
  const byokModelCatalog = useByokModelCatalog(workspaceId, mode === "byok_api" ? provider : "");
  const [selectedModel, setSelectedModel] = useState<string>(() =>
    seedSelectedModel(resolveDisplayMode(config), config.provider || "", config.model || ""),
  );
  const [apiKey, setApiKey] = useState("");
  const [reasoningEffort, setReasoningEffort] = useState<string>(config.reasoning_effort || "");
  // Re-default the model choice when the provider changes AFTER mount (so an
  // id from the previous provider doesn't linger in a <select> that no longer
  // has it) — but never on first render, which would clobber the agent's
  // actual current model. cli_subscription joined this in the same pass that
  // gave it a model picker at all (2026-07-30) — see seedSelectedModel's own
  // doc comment for what it defaults to per provider.
  const skipNextModelReset = useRef(true);
  useEffect(() => {
    if (skipNextModelReset.current) { skipNextModelReset.current = false; return; }
    if (mode === "byok_api" || mode === "cli_subscription") {
      setSelectedModel(FREEFORM_MODEL_PROVIDERS.has(provider) ? "" : defaultModelForProvider(provider));
    } else if (mode === "local") {
      setSelectedModel(defaultModelForProvider(provider || "ollama"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);
  // URGENT fix (2026-08-14): once the live Codex catalog loads, correct an
  // UNTOUCHED default that has since rotted (the exact live bug — a stale
  // static default sat in the <select> as a real value that no longer
  // matches any option). Only fires when the current value is still
  // EXACTLY the static seed and genuinely absent from the live list —
  // never overwrites a value the owner (or the agent's own saved config)
  // actually chose, live-valid or not; save-time validation is what catches
  // that case honestly instead of silently swapping it out from under them.
  useEffect(() => {
    if (provider !== "openai-codex" || !codexModelCatalog.loaded || !codexModelCatalog.supported) return;
    const live = visibleCodexModels(codexModelCatalog);
    if (!live || live.length === 0) return;
    const stillMatchesStaticSeed = selectedModel === defaultModelForProvider(provider);
    const alreadyLiveValid = live.some((m) => m.id === selectedModel);
    if (stillMatchesStaticSeed && !alreadyLiveValid) {
      setSelectedModel(live.find((m) => m.isDefault)?.id || live[0].id);
    }
  }, [provider, codexModelCatalog, selectedModel]);
  // Same correction, for byok_api's own live catalog — an UNTOUCHED
  // default that this workspace's real credential no longer sees (a
  // retired/renamed model) gets swapped for a real one; a value the owner
  // (or the agent's saved config) actually chose is never touched here.
  useEffect(() => {
    if (mode !== "byok_api" || !byokModelCatalog.loaded || !byokModelCatalog.supported) return;
    const live = visibleByokModels(byokModelCatalog);
    if (!live || live.length === 0) return;
    const stillMatchesStaticSeed = selectedModel === defaultModelForProvider(provider);
    const alreadyLiveValid = live.includes(selectedModel);
    if (stillMatchesStaticSeed && !alreadyLiveValid) {
      setSelectedModel(live[0]);
    }
  }, [mode, provider, byokModelCatalog, selectedModel]);
  // Every field above is seeded from `config` via a useState INITIALIZER,
  // which only runs on the component's very first render. That's fine when
  // `agent` is already loaded by the time this tab mounts (the common path:
  // Overview loads first, the user clicks over) — but FleetAgentDetail
  // renders this tab unconditionally as soon as activeTab === "model", and
  // a direct link or hard refresh landing straight on this tab (initialTab
  // from the URL) mounts it with agent still null, before its fetch
  // resolves. Every field then silently seeds from `{}` and stays there
  // forever once `agent` populates a beat later — confirmed live: reload on
  // this tab left the Reasoning-effort <select> stuck on "Model default"
  // while the read-only "Current state" block (which reads `config` fresh
  // on every render, not through useState) correctly showed the real saved
  // value. Runs ONCE, the first time real agent data arrives — not on every
  // later change, since unlike HardwareTab's identical resync effect (which
  // auto-persists each click, so there's never an unsaved edit to protect)
  // this tab holds edits locally until an explicit Save; resyncing after
  // that first hydration would silently discard whatever the user is
  // mid-editing. skipNextModelReset is primed here too so this hydration's
  // own setProvider call doesn't trip the model-reset effect just above and
  // clobber the model this same hydration just set.
  const hydratedFromAgent = useRef(false);
  useEffect(() => {
    if (hydratedFromAgent.current || !agent) return;
    hydratedFromAgent.current = true;
    const freshConfig = agent.model_config || {};
    skipNextModelReset.current = true;
    const freshMode = resolveDisplayMode(freshConfig);
    setMode(freshMode);
    setProvider(freshConfig.provider || "");
    setGatewayBinding(freshConfig.gateway_binding || "");
    setSelectedModel(seedSelectedModel(freshMode, freshConfig.provider || "", freshConfig.model || ""));
    setReasoningEffort(freshConfig.reasoning_effort || "");
  }, [agent]);
  // COMING_SOON_MODES is empty today (cli_subscription and local both
  // shipped) — kept as a live check, not deleted, so gating a future mode
  // that isn't ready yet needs no new plumbing here.
  const isComingSoon = COMING_SOON_MODES.has(mode);
  // BYO-brain Phase 2/3: "local" (Ollama) and "cli_subscription" (Claude
  // Code/Codex) both dispatch on a paired Gateway box — either one MUST name
  // which box runs it, or every turn fails at turn time instead of save
  // time ("no computer is bound" / "cli_subscription requires a Gateway").
  const localNeedsBox = mode === "local" && !gatewayBinding.trim();
  const cliSubscriptionNeedsBox = mode === "cli_subscription" && !gatewayBinding.trim();
  // platform_credits/byok_api share ONE picker+vocabulary (REASONING_
  // EFFORT_OPTIONS). cli_subscription gets its OWN runtime-gated picker
  // below (renderCliReasoningEffortPicker) — claude_code and codex accept
  // genuinely different values, verified live against each CLI's own
  // --help, so it can't reuse this flat set. local (Ollama) still has no
  // reasoning-effort control at all today — the picker stays hidden for
  // it instead of saving a setting that silently does nothing at turn time.
  const reasoningEffortSupported = REASONING_EFFORT_SUPPORTED_MODES.has(mode);
  const canSaveReasoningEffort = reasoningEffortSupported || mode === "cli_subscription";
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const modelSummary = resolveAgentModelSummary(config);
  const displayProvider = modelSummary.provider;
  const displayModel = modelSummary.model;
  const isPlatformDefault = modelSummary.isPlatformDefault;

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await saveAgentModelConfig(workspaceId, agentId, config, agent?.label, {
        mode, provider, selectedModel, apiKey, gatewayBinding, reasoningEffort,
      });
      setSaved(true);
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  // Shared between the platform_credits and byok_api blocks below (the two
  // modes reasoningEffortSupported allows) so the picker can't drift between
  // them — see REASONING_EFFORT_SUPPORTED_MODES for why cli_subscription/
  // local get a disabled note instead (renderReasoningEffortUnsupportedNote).
  function renderReasoningEffortPicker() {
    return (
      <>
        <label className="fleet-wizard-label">Reasoning effort</label>
        <select
          className="fleet-wizard-input"
          value={reasoningEffort}
          onChange={(e) => { setReasoningEffort(e.currentTarget.value); setSaved(false); }}
        >
          {REASONING_EFFORT_OPTIONS.map((o) => (
            <option key={o.value || "unset"} value={o.value}>{o.label}</option>
          ))}
        </select>
        <p className="fleet-channel-expand-hint">
          Higher effort can solve harder problems but costs more and replies slower. Models that support it natively use it directly; others get it as a strong instruction instead.
        </p>
      </>
    );
  }

  // cli_subscription's reasoning-effort picker. ONE ladder, always
  // rendered, identical to the platform_credits/byok_api picker above —
  // the founder removed the per-runtime split on 2026-08-20 (his words are
  // quoted verbatim in REASONING_EFFORT_LADDER's comment in
  // fleet-provider-constants.ts). The paired Gateway still translates the
  // chosen level into the bound CLI's OWN flag (`--effort` for claude_code,
  // `-c model_reasoning_effort=` for codex, `--reasoning-effort` for
  // grok_build), clamping it into that CLI's own vocabulary first — the
  // wire stays native, only the picker is uniform.
  //
  // The live catalog (codex app-server's model/list, forwarded by the
  // gateway) now ENRICHES rather than RESTRICTS: it names the model's own
  // default, relays the model's own prose per level, and appends any level
  // the model reports that the shared ladder does not carry. That rule
  // lives in planCodexReasoningPicker so it is testable without a browser —
  // do not re-derive it here.
  function renderCliReasoningEffortPicker() {
    const plan = planCodexReasoningPicker({
      runtime: cliRuntime,
      catalogSupported: codexModelCatalog.supported,
      models: codexModelCatalog.models,
      selectedModel,
    });
    return (
      <>
        <label className="fleet-wizard-label">Reasoning effort</label>
        <select
          className="fleet-wizard-input"
          value={reasoningEffort}
          onChange={(e) => { setReasoningEffort(e.currentTarget.value); setSaved(false); }}
        >
          {plan.options.map((o) => (
            <option key={o.value || "unset"} value={o.value}>{o.label}</option>
          ))}
        </select>
        <p className="fleet-channel-expand-hint">
          {plan.kind === "live"
            ? `Annotated with what this exact model reports from your own ${RUNTIME_LABELS[cliRuntime]} account. Levels it doesn’t report stay selectable — ${RUNTIME_LABELS[cliRuntime]} decides what to do with them.`
            : cliRuntime === "cursor_cli"
              ? `Higher effort can solve harder problems but costs more and replies slower. Cursor CLI publishes no reasoning-effort control, so it ignores this today.`
              : `Higher effort can solve harder problems but costs more and replies slower. Passed to ${RUNTIME_LABELS[cliRuntime]}’s own reasoning control.`}
        </p>
      </>
    );
  }

  function renderReasoningEffortUnsupportedNote() {
    return (
      <p className="fleet-channel-expand-hint">
        Reasoning effort isn’t available for this mode yet — it only applies to platform credits and your own API key.
      </p>
    );
  }

  return (
    <div>
      {/* Save at the top, consistent with Persona/Schedule's own primary
          action placement — same fleet-detail-section-title
          space-between header the rest of Overview already uses. */}
      <div className="fleet-detail-section-title" style={{ marginTop: 0, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span>Model</span>
        <button type="button" className="fleet-btn fleet-btn--accent" onClick={save} disabled={saving || isComingSoon || localNeedsBox || cliSubscriptionNeedsBox}>
          {saving ? "Saving…" : saved ? "Saved ✓" : "Save"}
        </button>
      </div>
      {(isComingSoon || (localNeedsBox && !isComingSoon) || (cliSubscriptionNeedsBox && !isComingSoon) || error) && (
        <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 12 }}>
          {isComingSoon && (
            <span className="fleet-channel-expand-hint" style={{ margin: 0 }}>{COMING_SOON_NOTE} — you can’t save this yet.</span>
          )}
          {localNeedsBox && !isComingSoon && (
            <span className="fleet-channel-expand-hint" style={{ margin: 0 }}>Pick a computer to run this agent’s local model.</span>
          )}
          {cliSubscriptionNeedsBox && !isComingSoon && (
            <span className="fleet-channel-expand-hint" style={{ margin: 0 }}>Pick a computer to run this agent’s subscription CLI.</span>
          )}
          {error && <span className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</span>}
        </div>
      )}

      <AgentModelSummary workspaceId={workspaceId} agentId={agentId} agent={agent} />
      {/* Current state — always visible */}
      <div className="fleet-config" style={{ marginBottom: 20 }}>
        <div className="fleet-config-row">
          <span className="fleet-config-label">Provider</span>
          <span className="fleet-config-value">{displayProvider}{isPlatformDefault ? " (platform default)" : ""}</span>
        </div>
        <div className="fleet-config-row">
          <span className="fleet-config-label">Model</span>
          <span className="fleet-config-value">{displayModel}{isPlatformDefault ? " (platform default)" : ""}</span>
        </div>
        {config.mode && (
          <div className="fleet-config-row">
            <span className="fleet-config-label">Payment</span>
            <span className="fleet-config-value">{MODE_LABELS[config.mode as ProviderMode] || config.mode}</span>
          </div>
        )}
        {modelSummary.reasoningEffort && (
          <div className="fleet-config-row">
            <span className="fleet-config-label">Reasoning</span>
            <span className="fleet-config-value">{reasoningEffortLabel(modelSummary.reasoningEffort)}</span>
          </div>
        )}
      </div>

      {/* Editor */}
      <div className="fleet-detail-section-title">Change provider</div>
      <div className="fleet-wizard-options" style={{ marginTop: 8 }}>
        <button
          type="button"
          className={`fleet-wizard-option${mode === "platform_credits" ? " is-selected" : ""}`}
          onClick={() => { setMode("platform_credits"); setSaved(false); }}
        >
          <span className="fleet-wizard-option-label">Platform credits</span>
          <span className="fleet-wizard-option-body">DeepSeek. Empyralis pays.</span>
        </button>
        <button
          type="button"
          className={`fleet-wizard-option${mode === "byok_api" ? " is-selected" : ""}`}
          onClick={() => { setMode("byok_api"); setProvider(provider || "anthropic"); setSaved(false); }}
        >
          <span className="fleet-wizard-option-label">Your own API key</span>
          <span className="fleet-wizard-option-body">Use your key for any provider.</span>
        </button>
        {/* Brain-bound modes need a Gateway/machine — offered only when the
            workspace has a paired box (cloud has none). The current mode's card
            always stays visible so an already-configured agent can still be
            seen/changed even if its hardware later went away. */}
        {(cliGateways.length > 0 || mode === "cli_subscription") && (
          <button
            type="button"
            className={`fleet-wizard-option${mode === "cli_subscription" ? " is-selected" : ""}`}
            onClick={() => { setMode("cli_subscription"); setProvider(provider || "claude_code_cli"); setSaved(false); }}
          >
            <span className="fleet-wizard-option-label">Your subscription</span>
            <span className="fleet-wizard-option-body">Claude Code or Codex via Gateway.</span>
            <span className="fleet-wizard-option-note fleet-wizard-option-note--gateway">
              {cliSubscriptionHint(cliGateways)}
            </span>
          </button>
        )}
        {(cliGateways.length > 0 || mode === "local") && (
          <button
            type="button"
            className={`fleet-wizard-option${mode === "local" ? " is-selected" : ""}`}
            onClick={() => { setMode("local"); setProvider(provider || "ollama"); setSaved(false); }}
          >
            <span className="fleet-wizard-option-label">Run locally</span>
            <span className="fleet-wizard-option-body">Ollama on your own machine, via the Gateway.</span>
          </button>
        )}
      </div>
      {cliGateways.length === 0 && mode !== "cli_subscription" && mode !== "local" && (
        <p className="fleet-config-hint" style={{ marginTop: 6 }}>
          Running on your own Claude/Codex subscription or a local model needs a paired computer — add one under Hardware.
        </p>
      )}

      {/* platform_credits: a real choice between DeepSeek's fast/slow pair
          (Flash/Pro — see PLATFORM_CREDITS_TIER_OPTIONS's own doc), plus
          reasoning effort. Never the raw vendor/model string here either —
          same rule as the primary chip. */}
      {mode === "platform_credits" && (
        <div className="fleet-channel-expand">
          <label className="fleet-wizard-label">Speed</label>
          <div className="fleet-tier-picker">
            {PLATFORM_CREDITS_TIER_OPTIONS.map((opt) => {
              const isSelected = platformCreditsTierForModel(selectedModel) === opt.tier;
              return (
                <button
                  key={opt.tier}
                  type="button"
                  className={`fleet-tier-picker-option${isSelected ? " is-selected" : ""}`}
                  onClick={() => { setSelectedModel(PLATFORM_CREDITS_MODEL_BY_TIER[opt.tier]); setSaved(false); }}
                  aria-pressed={isSelected}
                >
                  <span className="fleet-tier-picker-option-label">{opt.label}</span>
                  <span className="fleet-tier-picker-option-subtitle">{opt.subtitle}</span>
                </button>
              );
            })}
          </div>
          {renderReasoningEffortPicker()}
        </div>
      )}

      {mode === "byok_api" && (
        <div className="fleet-channel-expand">
          <label className="fleet-wizard-label">Provider</label>
          <select className="fleet-wizard-input" value={provider} onChange={(e) => { setProvider(e.currentTarget.value); setSaved(false); }}>
            {BYOK_PROVIDERS.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          <label className="fleet-wizard-label">API key {apiKey ? "" : "(leave blank to keep existing)"}</label>
          <input
            className="fleet-wizard-input"
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => { setApiKey(e.currentTarget.value); setSaved(false); }}
            placeholder="sk-..."
          />
          <p className="fleet-channel-expand-hint">Stored in this workspace's vault.</p>
          {FREEFORM_MODEL_PROVIDERS.has(provider) ? (
            <>
              <label className="fleet-wizard-label">Model ID</label>
              <input
                className="fleet-wizard-input"
                value={selectedModel}
                onChange={(e) => { setSelectedModel(e.currentTarget.value); setSaved(false); }}
                placeholder={provider === "azure_openai" ? "e.g. my-gpt4-deployment" : "e.g. llama-3-70b"}
              />
            </>
          ) : (() => {
            // Live discovery (2026-08-20 fix): the model list a BYOK
            // customer sees is the models their OWN saved credential can
            // actually reach — never a hand-typed guess. Same "ask the
            // real source, fall back honestly" contract the cli_
            // subscription Codex picker already uses below.
            const liveModels = visibleByokModels(byokModelCatalog);
            const options = liveModels
              ? liveModels.map((m) => ({ id: m, label: modelOptionLabel(provider, m) }))
              : modelsForProvider(provider).map((m) => ({ id: m, label: modelOptionLabel(provider, m) }));
            const showStaleNote = !liveModels && byokModelCatalog.loaded;
            return (
              <>
                <label className="fleet-wizard-label">Model</label>
                <select className="fleet-wizard-input" value={selectedModel} onChange={(e) => { setSelectedModel(e.currentTarget.value); setSaved(false); }}>
                  {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                </select>
                {showStaleNote && (
                  <p className="fleet-channel-expand-hint">
                    {byokModelCatalog.credentialRequired
                      ? "Save an API key for this provider to see the real, currently-usable models on your account."
                      : "Couldn't check your account's real models right now — this list may include models that have since been renamed or retired."}
                  </p>
                )}
                <ModelSizeWarning provider={provider} model={selectedModel} />
              </>
            );
          })()}
          {renderReasoningEffortPicker()}
        </div>
      )}

      {mode === "cli_subscription" && (
        <div className="fleet-channel-expand">
          <label className="fleet-wizard-label">Subscription</label>
          <select className="fleet-wizard-input" value={provider} onChange={(e) => { setProvider(e.currentTarget.value); setSaved(false); }}>
            {SUBSCRIPTION_PROVIDERS.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          <p className="fleet-channel-expand-hint">{SUBSCRIPTION_PROVIDERS.find((p) => p.id === provider)?.detail}</p>
          {/* Model picker (2026-07-30 fix): this whole block used to jump
              straight from "which CLI" to reasoning effort — there was no way
              to choose a model at all, so cli_subscription agents always ran
              on whatever the CLI's own default happened to be. See
              MODELS_BY_PROVIDER's doc comment in fleet-provider-constants.ts
              for how each runtime's catalog (or lack of one) was sourced. */}
          {(() => {
            // The paired box's OWN CLI reports what it can actually run right
            // now — asked in each CLI's own native way (codex's app-server
            // model/list RPC, `cursor-agent models`, `grok models`; see the
            // gateway's cli-model-list.ts). This replaced a hand-typed mirror
            // that had already rotted: "gpt-5.4", retired by OpenAI and
            // unusable under this account's auth mode, sat in the list
            // looking like a normal choice.
            const liveModels = visibleCodexModels(codexModelCatalog);
            if (liveModels) {
              const options = liveModels.map((m) => ({
                id: m.id,
                label: m.isDefault ? `${m.displayName} (Recommended)` : m.displayName,
              }));
              return (
                <>
                  <label className="fleet-wizard-label">Model</label>
                  <select className="fleet-wizard-input" value={selectedModel} onChange={(e) => { setSelectedModel(e.currentTarget.value); setSaved(false); }}>
                    {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                  </select>
                  <ModelSizeWarning provider={provider} model={selectedModel} />
                </>
              );
            }
            // No live catalog. For a runtime with no published model-id
            // vocabulary either, a free-text field is the honest control —
            // a <select> of guessed ids would hand the CLI a value it may
            // not recognize. Where the CLI said WHY (Cursor's own "No models
            // available for this account."), relay its words rather than our
            // guess at them.
            if (FREEFORM_MODEL_PROVIDERS.has(provider)) {
              return (
                <>
                  <label className="fleet-wizard-label">Model ID (optional)</label>
                  <input
                    className="fleet-wizard-input"
                    value={selectedModel}
                    onChange={(e) => { setSelectedModel(e.currentTarget.value); setSaved(false); }}
                    placeholder="Leave blank to use the CLI's own default"
                  />
                  <p className="fleet-channel-expand-hint">
                    {codexModelCatalog.reason
                      || (effectiveGatewayId
                        ? `Couldn’t read this computer’s ${RUNTIME_LABELS[cliRuntime]} models right now — enter one only if you know it accepts it.`
                        : `Pick a computer below to see this account’s real, currently-usable ${RUNTIME_LABELS[cliRuntime]} models.`)}
                  </p>
                </>
              );
            }
            const options = modelsForProvider(provider).map((m) => ({ id: m, label: modelOptionLabel(provider, m) }));
            return (
              <>
                <label className="fleet-wizard-label">Model</label>
                <select className="fleet-wizard-input" value={selectedModel} onChange={(e) => { setSelectedModel(e.currentTarget.value); setSaved(false); }}>
                  {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                </select>
                <p className="fleet-channel-expand-hint">
                  {codexModelCatalog.reason
                    || (effectiveGatewayId
                      ? "Couldn't check this computer's actual models right now — this list may include models that have since been renamed or retired."
                      : "Pick a computer below to see this account's real, currently-usable models.")}
                </p>
                <ModelSizeWarning provider={provider} model={selectedModel} />
              </>
            );
          })()}
          {renderCliReasoningEffortPicker()}
          <div className="fleet-detail-section-title" style={{ marginTop: 16 }}>Brain runs on</div>
          <GatewayBoxPicker
            workspaceId={workspaceId}
            value={gatewayBinding}
            onChange={(id) => { setGatewayBinding(id); setSaved(false); }}
            requireRuntime={cliRuntime}
          />
          {inheritedGatewayLabel && (
            <p className="fleet-channel-expand-hint" style={{ margin: "6px 0 0" }}>
              No computer set on this agent — inherits {agentProject?.name || "this project"}&apos;s default: {inheritedGatewayLabel}.
            </p>
          )}
        </div>
      )}

      {mode === "local" && (
        <div className="fleet-channel-expand">
          <label className="fleet-wizard-label">Runtime</label>
          <select className="fleet-wizard-input" value={provider} onChange={(e) => { setProvider(e.currentTarget.value); setSaved(false); }}>
            {LOCAL_PROVIDERS.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          <p className="fleet-channel-expand-hint">{LOCAL_PROVIDERS.find((p) => p.id === provider)?.detail}</p>
          <label className="fleet-wizard-label">Ollama model</label>
          <select className="fleet-wizard-input" value={selectedModel} onChange={(e) => { setSelectedModel(e.currentTarget.value); setSaved(false); }}>
            {modelsForProvider("ollama").map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          <p className="fleet-channel-expand-hint">
            This turn only works if this model is actually pulled on the box below — run{" "}
            <code>ollama pull {selectedModel || "llama3.2"}</code> there first if you haven't.
          </p>
          {renderReasoningEffortUnsupportedNote()}
          <div className="fleet-detail-section-title" style={{ marginTop: 16 }}>Brain runs on</div>
          <GatewayBoxPicker workspaceId={workspaceId} value={gatewayBinding} onChange={(id) => { setGatewayBinding(id); setSaved(false); }} requireLocalModel />
          {inheritedGatewayLabel && (
            <p className="fleet-channel-expand-hint" style={{ margin: "6px 0 0" }}>
              No computer set on this agent — inherits {agentProject?.name || "this project"}&apos;s default: {inheritedGatewayLabel}.
            </p>
          )}
        </div>
      )}

      {agent && (
        <Disclosure label="Advanced — context policy">
          <ContextPolicySection workspaceId={workspaceId} agentId={agentId} agent={agent} />
        </Disclosure>
      )}
    </div>
  );
}

// ── Shared empty state ──────────────────────────────────────────────────────

function EmptyState({
  icon: Icon,
  title,
  body,
  action,
  onAction,
}: {
  icon?: LucideIcon;
  title: string;
  body: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="fleet-tab-state">
      {Icon && (
        <div className="fleet-empty-icon">
          <Icon size={20} strokeWidth={1.75} />
        </div>
      )}
      <div className="fleet-tab-state-title">{title}</div>
      <div className="fleet-tab-state-body">{body}</div>
      {action && onAction && (
        <div className="fleet-empty-actions">
          <button type="button" className="fleet-btn fleet-btn--accent" onClick={onAction}>
            {action}
          </button>
        </div>
      )}
    </div>
  );
}

