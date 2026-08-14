"use client";

import { fleetAuthorizedFetch } from "@/lib/workspace/fleet/fleet-authorized-fetch";

import { Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  Brain,
  Check,
  ChevronRight,
  Clock,
  Cpu,
  Inbox,
  LayoutGrid,
  Loader2,
  Lock,
  MessageSquare,
  PanelRightClose,
  PanelRightOpen,
  Pencil,
  Play,
  Plug,
  Radio,
  RefreshCw,
  Settings,
  Smartphone,
  Sparkles,
  Square,
  SquarePen,
  Trash2,
  Wand2,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";

import { WorkTab } from "./tabs/WorkTab";
import { HardwareTab } from "./tabs/HardwareTab";
import { MemoryTab } from "./tabs/MemoryTab";
import { AgentChat } from "./AgentChat";
import { GroupedRail, type GroupedRailGroup } from "./GroupedRail";
import {
  defaultAgentThreadId,
  newAgentThreadId,
  persistAgentThreadId,
  readPersistedAgentThreadId,
  useAgentConversations,
} from "./fleet-agent-conversations";

import {
  resumeFleetAgent,
  stopFleetAgent,
  useFleetAgentChannels,
  useFleetAgentConnectors,
  useFleetAgentTools,
  useFleetAgentCapabilities,
  useFleetAgentSchedule,
  previewFleetAgentSchedule,
  createFleetAgentSchedule,
  deleteFleetAgentSchedule,
  friendlyChannelOwnershipError,
  useFleetProjects,
  type FleetAgent,
  type FleetAgentSkill,
  type FleetChannel,
  type FleetTool,
  type FleetCapability,
  type FleetScheduleItem,
} from "./fleet-data";
import { timeAgo, formatDateTime, formatNumber, usagePayerLabel, type AgentStatusTone, type UsageMatrixRow } from "./fleet-presentation";
import { StatusChip, StatusDot } from "./fleet-indicators";
import { PanelSection, PanelRow, FleetRightPanel, type PanelValueTone } from "./FleetRightPanel";
import type { SageConversation } from "./SageConsolePanels";
import type { UsageBucket } from "./fleet-sparkline";
import { HeaderAction } from "./Breadcrumbs";
import { CHANNEL_ICONS } from "./fleet-icons";
import { ConnectorPicker } from "./ConnectorPicker";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { getErrorMessage } from "@/lib/ui/api-error";
import { RUNTIME_LABELS } from "./gateway-box-picker";
import { resolveAgentModelSummary, platformCreditsTierLabel } from "./fleet-model-config";
import { FleetToggleRowsSkeleton, FleetCardGridSkeleton } from "./fleet-states";

import "./agent-configure-sheet.css";

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
function isChannelConnected(
  channel: Pick<FleetChannel, "id" | "connected">,
  slackChannelBinding: string | null,
  telegramBotConnected: boolean,
): boolean {
  if (channel.id === "slack") return Boolean(slackChannelBinding);
  if (channel.id === "sage_telegram_hosted") return channel.connected || telegramBotConnected;
  return channel.connected;
}

type TabId = "general" | "work" | "channels" | "connectors" | "hardware" | "model" | "skills" | "memory" | "tools" | "capabilities" | "chat";

// Single source of id/label/icon truth for every one of the eleven
// sections — both the permanent top strip (two of these, TOP_TAB_IDS below)
// and the Configure sheet's GroupedRail groups (the other nine,
// CONFIGURE_GROUPS below) read labels/icons from here so neither surface
// can drift from the other. Eleven ids remain valid [tab] route segments
// regardless of which surface renders them (VALID_TABS, [tab]/page.tsx) —
// moving a tab between the two surfaces is a rendering change, not a
// routing one. Declared with "chat" first because TOP_TAB_IDS below renders
// in THIS array's order — chat is the agent's front door (an agent opens to
// Chat, not a config screen — see [tab]/page.tsx's own "chat" fallback), so
// it leads the strip. The Configure sheet ignores this order entirely (each
// group below picks its own members/grouping explicitly by id).
//
// "Overview" is GONE, not renamed (founder, 2026-08-13: "remove overview
// because it's something that we genuinely don't need inside this agent").
// Its former content is redistributed rather than deleted wholesale:
//   - agent rename (AgentTitle) + Persona + Schedule → moved here, into a
//     new "general" Configure tab (below) — set-once identity/behaviour
//     config, exactly what Configure already exists to hold.
//   - the one-line status sentence (NowStrip) → deleted outright. It was a
//     fourth restatement of the same status the Properties/Sessions panel's
//     own "Status" row, the agents rail's StatusDot, and the agents list
//     all already show — nobody has to "go looking for it" on a tab that no
//     longer exists when it is already one glance away everywhere else.
//   - the day-grouped activity feed → deleted outright, not folded into
//     Work. Work's own timeline (agent_trace_service's tool/plan/browser/
//     delegation/approval event taxonomy) is a strictly richer account of
//     "what this agent has actually done" than the activity_ledger_events
//     list Overview showed — removing the shallower duplicate does not
//     weaken Work, which is untouched.
const TABS: { id: TabId; label: string; icon: LucideIcon }[] = [
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "general", label: "General", icon: LayoutGrid },
  { id: "model", label: "Model", icon: Sparkles },
  { id: "skills", label: "Skills", icon: BookOpen },
  { id: "work", label: "Work", icon: Inbox },
  { id: "channels", label: "Channels", icon: Radio },
  { id: "connectors", label: "Connectors", icon: Plug },
  { id: "tools", label: "Tools", icon: Wrench },
  { id: "capabilities", label: "Capabilities", icon: Wand2 },
  { id: "hardware", label: "Hardware", icon: Cpu },
  { id: "memory", label: "Memory", icon: Brain },
];

// The two you actually watch day to day — permanent top strip, Chat leading
// it as the front door. Everything else moved into the Configure sheet,
// opened by the trigger button next to this strip; nothing was deleted
// (founder: "the rest must not be deleted" — true of every section except
// Overview itself, which the founder separately asked removed outright),
// it just isn't equal-billing top-level nav anymore (UI-CONTRACT: "a
// surface must earn its place"). Memory joins Configure here too (founder:
// "Memory moves inside Configure. It stops being a top-level tab.") — see
// CONFIGURE_GROUPS below for where it landed.
const TOP_TAB_IDS = new Set<TabId>(["chat", "work"]);

// Configure sheet groups — BRAIN (what it thinks with) / REACH (how it's
// reached, and what it can reach out to) / COMPUTE (what it runs on).
// Rendered via GroupedRail, the same component Settings uses, per the
// design's whole point: one rail component, two callers, not a rail built
// twice. Order within each group is the order the old flat tab strip had
// them in. "general" (identity/persona/schedule, ex-Overview) and "memory"
// both join Brain — both are "what/how it thinks and remembers", the same
// theme that already justified moving Model/Capabilities/Skills off the top
// strip, so neither needed a fourth group invented just to hold it
// (UI-CONTRACT: "most configuration is set once and does not deserve equal
// billing").
const CONFIGURE_GROUPS: { id: string; label: string; tabs: TabId[] }[] = [
  { id: "brain", label: "Brain", tabs: ["general", "model", "capabilities", "skills", "memory"] },
  { id: "reach", label: "Reach", tabs: ["channels", "connectors", "tools"] },
  { id: "compute", label: "Compute", tabs: ["hardware"] },
];
const CONFIGURE_TAB_IDS = new Set<TabId>(CONFIGURE_GROUPS.flatMap((g) => g.tabs));

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
 * Agent detail — a routed page (top tabs + a permanent properties panel that
 * never reflows the content column). Every tab has real data or an
 * intentional empty state with a working next action. No stubs.
 */
export function FleetAgentDetail({
  workspaceId,
  agentId,
  agent,
  projectName,
  onChat,
  initialTab,
  onTabChange,
  onRenamed,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  /** Resolved project display name — passed by the routed page (from the URL's
   *  projectId, so it's available on first paint independent of the agents
   *  fetch). undefined = still resolving (shows a loading placeholder); pass
   *  "—" explicitly when there genuinely is no project (e.g. Sage). NEVER
   *  fall back to the raw project_id here — that's the "raw ids on first
   *  paint" bug. */
  projectName?: string;
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
  // Sessions (Part 2, "the right panel becomes sessions") — lifted out of
  // ChatTab, which used to own this alone for its own header New chat/
  // History controls (both deleted — the Sessions panel below is now the
  // one place conversations are started and browsed, not a second copy in
  // the tab header).
  // popover. It's now ALSO the source for the Sessions section of the
  // persistent right panel (below), which has to know the open thread and
  // the full conversation list regardless of which top tab is active — so
  // the state lives here, once, and both ChatTab and the panel read it.
  const [threadId, setThreadId] = useState<string>(
    () => readPersistedAgentThreadId(workspaceId, agentId) || defaultAgentThreadId(agentId),
  );
  const { conversations, refresh: refreshConversations } = useAgentConversations(workspaceId, agentId, true);
  useEffect(() => {
    persistAgentThreadId(workspaceId, agentId, threadId);
  }, [workspaceId, agentId, threadId]);
  // Synced from the `?thread=` URL param (ChatThreadSearchParamBridge,
  // mounted below) — a direct link or cmd-click lands on the right
  // conversation the moment Next resolves the param, same guarantee the old
  // ChatTab-local version made.
  const onThreadParamChange = useCallback((thread: string | null) => {
    if (thread) setThreadId(thread);
  }, []);
  const { channels, refresh: refreshChannels, telegramBotConnected, slackChannelBinding } = useFleetAgentChannels(workspaceId, agentId);
  const { connectors } = useFleetAgentConnectors(workspaceId, agentId);
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

  const connectedChannels = channels.filter((c) => isChannelConnected(c, slackChannelBinding, telegramBotConnected)).length;
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
  // deriveAgentStatus (needs `gateways`, hence computed here rather than up
  // top): a cli_subscription agent whose bound CLI isn't signed in reads
  // "Needs sign-in", never a false "Ready" — the header must never claim an
  // agent is runnable when its brain can't produce a turn.
  const status = deriveAgentStatus(agent ?? {}, gateways);
  // Role itself no longer has a Properties-panel row: every user-created
  // agent is seeded role="specialist" (see fleet_tools.py's create path)
  // and there's no UI to change it, so the row only ever read "Specialist"
  // — real backend concept (it gates fleet_tools.py's 5 platform-management
  // tools), just never-varying, unset-by-the-user information for the one
  // panel that's supposed to be this agent's day-to-day facts. isMaster is
  // still real and load-bearing below (it's what makes the workspace's one
  // operator/Sage agent skip the "Tools" row and its delete control).
  const isMaster = (agent?.role || "agent") === "operator";
  const { tools: agentTools } = useFleetAgentTools(workspaceId, agentId);
  // Truth Map B1 (mirrors ToolsTab's identical requiredConnector/
  // connectorMissing check): a tool bound behind requires_connector does
  // nothing until that connector is actually connected, so it shouldn't
  // count toward "customer access" just because it's enabled+granted.
  const connectorById = new Map(connectors.map((c) => [c.id, c]));
  const customerAccessCount = agentTools.filter((t) => {
    if (!(t.enabled && (t.audience_safe || t.mandate_granted))) return false;
    const requiredConnector = t.requires_connector ? connectorById.get(t.requires_connector) : undefined;
    const connectorMissing = Boolean(t.requires_connector) && !requiredConnector?.connected;
    return !connectorMissing;
  }).length;

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
  // Cold-loading /agents/{id}/tools lands here with initialTab="tools"
  // already resolved, so this is true on first paint, not after a second
  // click — the sheet opens on the right section immediately.
  const sheetOpen = CONFIGURE_TAB_IDS.has(activeTab);

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
  // A session/conversation always opens on Chat, regardless of which tab
  // the click came from — the Sessions panel is visible on every tab, so
  // clicking a row while on Work must still land in the actual
  // conversation. `?thread=` on the chat route is what ChatTab's own
  // key={threadId} remount and ChatThreadSearchParamBridge (below) key off.
  const threadHref = useCallback(
    (id: string) => `${tabHref("chat")}?${new URLSearchParams({ thread: id }).toString()}`,
    [tabHref],
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

  // Land keyboard/SR focus somewhere deliberate on mount rather than leaving
  // it on <body> — the active tab pill, since it's already the natural next
  // stop for arrow/tab navigation. Only on first mount, not on every tab
  // switch (activeTab already tracks the URL; native click/router
  // navigation already handles focus for those). Now a <Link> (real anchor),
  // not a <button> — same "primary navigation is real links" move as the
  // Configure sheet's GroupedRail items, so the ref target is an anchor.
  const activeTabRef = useRef<HTMLAnchorElement | null>(null);
  // DELIBERATELY NO focus() ON MOUNT (2026-08-13). This used to call
  // activeTabRef.current?.focus() so SPA navigation had a sensible landing
  // spot instead of <body>. The cost was invisible to whoever wrote it and
  // very visible to the founder: PROGRAMMATIC focus still satisfies
  // :focus-visible — the browser cannot tell it apart from keyboard focus —
  // so globals.css's `:focus-visible { box-shadow: var(--app-shadow-focus) }`
  // drew a 2px accent ring around the active tab pill on EVERY page load, for
  // every mouse user, and it stayed there until they happened to click
  // something else. His words: "I always have this purple thing on the ui."
  //
  // Removing it rather than suppressing the ring, because the ring is right:
  // it is the only thing telling a keyboard user where they are, and killing
  // the indicator to hide an unwanted trigger trades a visual annoyance for
  // an accessibility regression.
  //
  // The SPA focus-management this was reaching for is genuinely worth having
  // — but it belongs on the page's own HEADING, not on an interactive
  // control, which is exactly what TaskDetailView and DocumentDetailView
  // already do (`headingRef` on an `<h2 tabIndex={-1}>`). A heading focused
  // programmatically announces the new page without rendering as a focused
  // button. Breadcrumbs.tsx owns the <h1> here, so wiring that is its job,
  // not a second heading invented in this file — see the MAN-145 title-dedup
  // note further down for why a second one must not be added.
  // Whichever tab is active always scrolls fully into view within the
  // horizontal strip — not just on first mount. Without this, following a
  // direct link into a tab past the fold (e.g. Hardware) left the highlight
  // correct but invisible until the user found the strip scrollable; this
  // keeps "the highlighted tab" and "the tab you can see" the same claim on
  // every navigation, mouse or keyboard.
  useEffect(() => {
    activeTabRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [activeTab]);

  // Configure sheet's own mount focus — the cold-load-into-a-grouped-tab
  // case (/agents/{id}/tools) never assigns activeTabRef above (none of the
  // two top tabs is active), so without this, focus would fall through to
  // <body> instead of landing somewhere deliberate inside the sheet.
  const sheetRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (sheetOpen) sheetRef.current?.focus();
  }, [sheetOpen]);

  // Content for the Properties overlay below (FleetRightPanel) — same
  // PanelSection/PanelRow shape the project page's own Properties panel
  // renders, so the two surfaces can never drift apart in markup even
  // though each computes its own values.
  const propertiesContent = (
    <PanelSection title="Properties">
      <PanelRow label="Status" value={<StatusChip tone={status.tone} label={status.label} />} />
      <PanelRow label="Placement" value={placement.label} tone={HARDWARE_PLACEMENT_PANEL_TONE[placement.tone]} />
      {/* Model row removed (founder: "model picking some shit like this must
          not be on the right side, we already moved it to the bottom") —
          model selection now lives in the composer at the bottom of Chat,
          plus the full editor on the Configure > Model tab. Having a THIRD
          picker here duplicated both. AgentModelPickerRow (the popover this
          row used to open) had no other caller, so it was deleted with this
          row rather than left as dead code — see ModelTab below for the
          real editor. */}
      {/* "Tools", not "Customer access": the internal audience/mandate
          vocabulary that name used to expose. What this counts hasn't
          changed — tools an outside customer messaging this agent can
          actually invoke right now (enabled, granted, and not blocked on a
          missing connector) — only the label, into words a workspace owner
          recognizes without a tooltip. Still skipped for the operator agent
          (isMaster): it has no customer-facing surface to count.
          Also skipped for any owner-audience agent (Personal Assistant /
          internal_assistant preset, resolved server-side into
          agent.audience via fleet_tools.resolve_agent_audience): a
          "customer messages it" count is not a concept that applies to an
          agent nobody outside the workspace can ever reach — per founder
          direction, this row only means something for the external-audience
          (Customer Support / customer_facing) type. Absent, not zero. */}
      {!isMaster && agent?.audience === "external" && (
        <PanelRow
          label="Tools"
          value={`${customerAccessCount} ${customerAccessCount === 1 ? "tool" : "tools"}`}
          tone={customerAccessCount > 0 ? "default" : "muted"}
          hint="Tools this agent can use when someone outside your workspace messages it. You keep full access regardless."
        />
      )}
      {/* Zero is never shown as a row — an unconnected Channels/Connectors
          count is noise, not a fact worth a permanent line in a panel
          that's visible on every tab. Configure > Channels/Connectors is
          still the place to go connect one; this panel just stops
          announcing "0" for something not yet set up. */}
      {connectedChannels > 0 && <PanelRow label="Channels" value={connectedChannels} />}
      {connectedConnectors > 0 && <PanelRow label="Connectors" value={connectedConnectors} />}
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
          <span className="fleet-panel-row-value">${costToday.toFixed(4)}</span>
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
          value={row.pricing_known ? `$${row.usd_cost.toFixed(4)}` : "Not priced"}
          tone={row.pricing_known ? "default" : "muted"}
        />
      ))}
    </PanelSection>
  );
  // Part 2 — "instead of this right panel we must have sessions... at the
  // top... just like what we have right now, and below we are going to have
  // session histories." Properties (above) is unchanged; this is the new
  // region below it, in the SAME panel — one drawer, two stacked sections,
  // never two separate panels. Newest first (useAgentConversations already
  // sorts that way), a readable title + relative timestamp per row, the
  // open conversation visibly marked, and a one-click way to start a new
  // one — the bar the founder named explicitly: "just like Claude's own
  // conversation list." `.fleet-agent-sessions` gets its OWN scroll (see
  // fleet-theme.css) so a long history never drags the Properties section
  // above it out of view.
  const sessionsContent = (
    <PanelSection
      title="Sessions"
      className="fleet-agent-sessions"
      action={
        <Link
          href={threadHref(newAgentThreadId(agentId))}
          replace
          className="fleet-icon-btn"
          aria-label="New chat"
          title="New chat"
        >
          <SquarePen size={13} strokeWidth={1.75} />
        </Link>
      }
    >
      {conversations.length === 0 ? (
        <div className="fleet-panel-empty">No conversations yet.</div>
      ) : (
        <div className="fleet-agent-sessions-list" role="list">
          {conversations.map((c) => {
            const isActive = c.id === threadId;
            return (
              <Link
                key={c.id}
                href={threadHref(c.id)}
                replace
                role="listitem"
                className={`fleet-sage-history-row${isActive ? " is-active" : ""}`}
                aria-current={isActive ? "true" : undefined}
              >
                <span className="fleet-sage-history-row-title">{c.title}</span>
                <span className="fleet-sage-history-row-time">
                  {isActive ? "Current" : timeAgo(c.lastActivityAt)}
                </span>
              </Link>
            );
          })}
        </div>
      )}
    </PanelSection>
  );
  const inner = (
    <>
      {/* Reads the `?thread=` URL param and keeps `threadId` (above) in
          sync — page-level now, not scoped to ChatTab, since the Sessions
          panel below needs to know the open conversation on every tab, not
          just while Chat is active. Isolated under its own Suspense
          boundary (useSearchParams bails the calling tree out to the
          nearest one during prerender) so this doesn't drag the whole page
          into it. */}
      <Suspense fallback={null}>
        <ChatThreadSearchParamBridge onChange={onThreadParamChange} />
      </Suspense>
      {/* Tabs live at the TOP, under the breadcrumb — one navigation only.
          Sessions opens as a floating overlay (FleetRightPanel) via the
          toggle below — the SAME pattern the project/agents LIST pages use
          for their own Properties toggle (FleetToolbar's panelOpen), not a
          second implementation. This used to be a permanent collapsible
          RIGHT RAIL, a real flex sibling that reserved width whenever
          expanded — the founder's direction was explicit that the panel
          "must not be something that is merged on the user interface, it
          just should be something that appears just as a node", matching
          the reference already shipped on the project detail page. */}
      <div className="fleet-detail-tabbar">
        {/* Wrap is the non-scrolling fade anchor — .fleet-detail-toptabs
            itself is the horizontal scroller (overflow-x:auto). The old
            fade lived on .fleet-detail-tabbar::after (the WHOLE bar,
            properties-toggle icon included), so on a real 375px phone it
            sat on top of that icon button instead of the actual cut-off
            edge of Hardware/Memory — the tester saw no fade at all where
            the tabs cut off, which read as "no scroll affordance". Anchoring
            it to this wrap instead puts it exactly at the scroller's own
            right edge, before the toggle button starts. */}
        <div className="fleet-detail-toptabs-wrap">
          <nav className="fleet-detail-toptabs" aria-label="Agent sections">
            {/* The two you actually watch day to day — TOP_TAB_IDS. Real
                <Link>s (not onClick buttons) with `replace` so cmd-click/
                middle-click open a new tab while ordinary clicks keep the
                existing no-history-entry-per-tab behavior (router.replace,
                same as onTabChange always did). */}
            {TABS.filter((tab) => TOP_TAB_IDS.has(tab.id)).map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              return (
                <Link
                  key={tab.id}
                  href={tabHref(tab.id)}
                  replace
                  ref={isActive ? activeTabRef : undefined}
                  className={`fleet-detail-toptab${isActive ? " is-active" : ""}`}
                  aria-current={isActive ? "page" : undefined}
                >
                  <Icon size={15} strokeWidth={1.75} />
                  <span>{tab.label}</span>
                </Link>
              );
            })}
          </nav>
        </div>
        {/* Opens the Configure sheet (below) on the other nine sections —
            General, Model, Capabilities, Skills, Memory, Channels,
            Connectors, Tools, Hardware — grouped Brain/Reach/Compute via
            the same GroupedRail Settings uses. Real link: closed, it goes
            to the first Brain item (General); already open, it points at
            whatever section is active, so a second click/cmd-click is a
            same-URL no-op rather than a jump back to General. */}
        <Link
          href={tabHref(sheetOpen ? activeTab : CONFIGURE_GROUPS[0].tabs[0])}
          replace
          className="fleet-btn agent-configure-trigger"
          aria-haspopup="dialog"
          aria-expanded={sheetOpen}
        >
          <Settings size={14} strokeWidth={1.75} />
          <span className="fleet-btn-label">Configure</span>
        </Link>
        {/* Same is-active/aria-pressed idiom as FleetToolbar's own list-page
            Properties toggle — a neutral filled state while open, never an
            accent (this is a view toggle, not a primary action). Labelled
            "Sessions" now, not "Properties" — Properties is still the top
            region of what opens, but Sessions is the headline reason to
            open it (Part 2: "instead of this right panel we must have
            sessions"). */}
        <button
          type="button"
          className={`fleet-icon-btn fleet-detail-properties-toggle${propertiesOpen ? " is-active" : ""}`}
          onClick={() => setPropertiesOpen((v) => !v)}
          aria-label="Sessions"
          aria-pressed={propertiesOpen}
          title="Sessions"
        >
          {propertiesOpen ? <PanelRightClose size={16} strokeWidth={1.75} /> : <PanelRightOpen size={16} strokeWidth={1.75} />}
        </button>
      </div>

      {/* The relative anchor the Sessions overlay below floats against —
          .fleet-detail-body is this container's only normal-flow child and
          always renders at full width, whether the overlay (an absolutely-
          positioned child, out of flow entirely) is open or closed. Same
          contract as the list pages' .fleet-content-with-panel. */}
      <div className="fleet-detail-columns">
        <div className="fleet-detail-body">
          {/* Only the two top-level tabs (Chat, Work) render here now. The
              other nine render inside the Configure sheet below —
              configureSheet — same components, same props, moved rather
              than duplicated. */}
          {activeTab === "work" && <WorkTab workspaceId={workspaceId} agentId={agentId} agent={agent} onAgentChanged={onRenamed} />}
          {activeTab === "chat" && (
            <ChatTab
              key={agentId}
              workspaceId={workspaceId}
              agentId={agentId}
              agent={agent}
              threadId={threadId}
              onTurnComplete={refreshConversations}
              onAgentSaved={onRenamed}
            />
          )}
        </div>
        <FleetRightPanel open={propertiesOpen} onClose={() => setPropertiesOpen(false)} ariaLabel="Sessions">
          {propertiesContent}
          {sessionsContent}
        </FleetRightPanel>
      </div>
    </>
  );

  // Configure sheet — the other six sections (Model, Capabilities,
  // Channels, Connectors, Tools, Hardware). Same tab components as the
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
            {activeTab === "memory" && (
              <MemoryTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChat={() => onChat(agentId)} />
            )}
            {activeTab === "channels" && (
              <ChannelsTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChannelsChanged={refreshChannels} />
            )}
            {activeTab === "connectors" && (
              <ConnectorsTab workspaceId={workspaceId} agentId={agentId} agent={agent} />
            )}
            {activeTab === "tools" && (
              <ToolsTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChat={() => onChat(agentId)} />
            )}
            {activeTab === "hardware" && (
              <HardwareTab workspaceId={workspaceId} agentId={agentId} agent={agent} onSaved={onRenamed} />
            )}
          </div>
        </div>
      </div>
    </div>
  ) : null;

  return (
    <>
      {activeTab === "work" && (
        <HeaderAction>
          {/* Wrapped (not just two bare portaled children) so the mobile
              overlap fix below can scope its icon-only collapse to exactly
              these two controls — .fleet-topbar-action is a shared portal
              slot every fleet page reuses (e.g. AgentsList's "+ New agent"),
              so a bare `.fleet-topbar-action .fleet-btn` rule would have
              iconified those too. See UI-CONTRACT §4 + fleet-theme.css's
              .fleet-detail-header-actions block for why this exists at all:
              two full-label buttons here left the mobile breadcrumb only
              ~33px wide, so its non-shrinking back-link overflowed straight
              under "Stop agent" — cutting "‹ Drift" to "‹ Dri" on every tab. */}
          <div className="fleet-detail-header-actions">
            <StopAgentControl workspaceId={workspaceId} agentId={agentId} agent={agent} onChanged={onRenamed} />
            {/* The one accent-carrying control on Work — nothing else in
                that view claims it: StopAgentControl (above) is
                plain .fleet-btn (its own confirm dialog is the one place
                that gets a color, and it's --danger, a different hue for a
                different signal), and WorkTab's own rows use "accent" only
                as a neutral status tone, never a button. --accent-fill
                (solid violet, matching "New agent"/"New project") rather
                than the quiet --accent hairline: this is the single primary
                action of the page — the thing every other row and panel
                exists to lead to — so it gets the same weight those other
                top-line creation/connection CTAs get, not the softer
                treatment reserved for routine actions like Save/Invite. */}
            <button type="button" className="fleet-btn fleet-btn--accent-fill" onClick={() => onChat(agentId)}>
              <MessageSquare size={14} strokeWidth={1.75} />
              <span className="fleet-btn-label">Chat with this agent</span>
            </button>
          </div>
        </HeaderAction>
      )}
      <div
        className="fleet-detail fleet-detail--page"
        aria-label={`${agent?.label || "Agent"} details`}
        onKeyDown={onPageKeyDown}
      >
        {inner}
      </div>
      {configureSheet}
    </>
  );
}

// Owner-only stop control — the header-level "Stop agent" button, and once
// stopped, an honest chip naming who stopped it (never a bare "Stopped").
// kill_switch_gate.py enforces this on the backend before any turn runs;
// this is purely the control surface.
//
// Stopping is a real, disruptive action (the agent goes dark on every
// channel until resumed) so it's gated behind a confirm dialog — a blurred/
// dimmed backdrop (.fleet-detail-backdrop, the same primitive the channel-
// connect banner below uses) plus a small centered card (.fleet-small-dialog),
// Cancel (neutral) + Stop agent (.fleet-btn--danger, solid red). Resume is
// affirmative, not destructive — it stays a plain one-click button, no confirm.
function StopAgentControl({
  workspaceId, agentId, agent, onChanged,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null; onChanged?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const stopped = agent?.stopped;

  async function handleStop() {
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
  }

  async function handleResume() {
    setBusy(true);
    setError(null);
    const result = await resumeFleetAgent(workspaceId, agentId);
    setBusy(false);
    if (result.ok) onChanged?.();
    else setError(result.error || "Could not resume this agent.");
  }

  // Close the confirm dialog on Escape (own the key so it doesn't bubble to
  // the page's own "Esc goes back" handler — same reasoning as the channel
  // banner below).
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

  if (stopped?.active) {
    return (
      <div className="fleet-stop-control">
        <span className="fleet-stop-chip" title={stopped.reason || undefined}>
          <Square size={12} strokeWidth={2} />
          <span className="fleet-btn-label">Stopped by {stopped.stopped_by_label || "an owner"}</span>
        </span>
        <button type="button" className="fleet-btn" disabled={busy} onClick={handleResume} aria-label="Resume agent" title="Resume agent">
          {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Play size={14} strokeWidth={1.75} />}
          <span className="fleet-btn-label">Resume</span>
        </button>
        {error && <span className="fleet-stop-error">{error}</span>}
      </div>
    );
  }

  return (
    <div className="fleet-stop-control">
      <button
        type="button"
        className="fleet-btn"
        disabled={busy}
        onClick={() => { setError(null); setConfirmOpen(true); }}
        aria-label="Stop agent"
        title="Stop agent"
      >
        <Square size={14} strokeWidth={1.75} />
        <span className="fleet-btn-label">Stop agent</span>
      </button>
      {error && !confirmOpen && <span className="fleet-stop-error">{error}</span>}

      {confirmOpen && (
        <div
          className="fleet-detail-backdrop"
          onClick={() => { if (!busy) setConfirmOpen(false); }}
        >
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
                Are you sure you want to stop <strong>{agent?.label || "this agent"}</strong>?
                It stops responding on every channel until you resume it.
              </p>
              {error && (
                <p style={{ margin: 0, fontSize: 12, color: "var(--offline-text)" }}>{error}</p>
              )}
            </div>
            <div className="fleet-small-dialog-footer">
              <button type="button" className="fleet-btn" onClick={() => setConfirmOpen(false)} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="fleet-btn fleet-btn--danger" onClick={handleStop} disabled={busy}>
                {busy ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Square size={14} strokeWidth={1.75} />}
                {busy ? "Stopping…" : "Stop agent"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── General (Configure) — ex-Overview's identity + operating settings ──────
//
// Overview itself is gone (founder, 2026-08-13: "remove overview because
// it's something that we genuinely don't need inside this agent"). This is
// the part of it that survives, relocated: name, persona and schedule are
// all set-once configuration a person would go looking for under Configure,
// not glance at daily — the exact reasoning that already moved Model and
// Capabilities off the top tab strip. The status sentence and the day-
// grouped activity feed that used to sit alongside these did NOT move here;
// see TABS' own comment above for why each was dropped rather than moved.
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
        <PersonaEditor workspaceId={workspaceId} agentId={agentId} agent={agent} />
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

// ── Chat ────────────────────────────────────────────────────────────────────

// Runs as this specific agent — its own persona, model binding, and memory
// scope — over a per-agent thread, the same convention channel-bound turns
// use. See AgentChat's context_hints.metadata.active_agent_install_id, read
// by specialist_runtime_context.resolve_specialist_runtime_context.
//
// THREAD IS STATE, LIVING ONE LEVEL UP NOW
// ------------------------------------------
// Used to be the single permanent id `thread_agent_{agentId}`
// (defaultAgentThreadId) forever — one conversation per agent, with no way
// to leave a bad one behind. A fabricated turn recorded on it stayed in
// every future turn's history as established fact, with no escape. New
// chat mints a genuinely different id (newAgentThreadId — never the same id
// with history hidden). The state itself (threadId, the conversation list,
// the URL sync) now lives in FleetAgentDetail, not here — Part 2 made the
// Sessions panel the one place conversations are started and browsed, and
// that panel is visible on every tab, not just Chat, so the state had to
// move up. This component just renders whatever thread it's handed.
//
// THREAD IS A URL PARAM, NOT JUST COMPONENT STATE
// -------------------------------------------------
// Session rows are real `<a href>` links (cmd-click/middle-click must open
// a new tab on that exact conversation — this codebase's own "primary
// navigation is real links" rule), so the open thread has to be something
// a URL can name. `?thread=<id>` on the chat route does that (see
// FleetAgentDetail's threadHref); ChatThreadSearchParamBridge below keeps
// FleetAgentDetail's threadId state in sync with it.
function ChatThreadSearchParamBridge({ onChange }: { onChange: (thread: string | null) => void }) {
  // Isolated in its own component under a local Suspense boundary (mirrors
  // FleetTabsProvider's SearchParamsBridge in FleetTabs.tsx) — calling
  // useSearchParams() bails the calling tree out to its nearest Suspense
  // boundary during prerender, so it's called HERE, under a boundary that
  // renders nothing, rather than dragging the whole page into one.
  const searchParams = useSearchParams();
  const thread = searchParams.get("thread");
  useEffect(() => {
    onChange(thread);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [thread]);
  return null;
}

function ChatTab({
  workspaceId, agentId, agent, threadId, onTurnComplete, onAgentSaved,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  threadId: string;
  onTurnComplete?: () => void;
  onAgentSaved?: () => void;
}) {
  const label = agent?.label || "this agent";

  return (
    <div className="fleet-agent-chat-panel">
      <AgentChat
        key={threadId}
        workspaceId={workspaceId}
        threadId={threadId}
        agentInstallId={agentId}
        agent={agent}
        emptyIcon={MessageSquare}
        emptyTitle={`Message ${label}`}
        emptyBody={`Your owner test chat with ${label} — full access, not what a real customer would see.`}
        placeholder={`Message ${label}…`}
        sourceTag="fleet_agent_chat"
        onTurnComplete={onTurnComplete}
        onAgentSaved={onAgentSaved}
      />
    </div>
  );
}

// ── Channels ────────────────────────────────────────────────────────────────

import { IMessageSetupPanel } from "./IMessageSetupPanel";
import {
  CredentialForm,
  GroupAllowlistForm,
  StateChip,
  useOpenClawChannelSetup,
} from "./OpenClawChannelsPanel";
import { channelCardPill, formatChannelList, openclawObservedErrorBanner } from "./openclaw-channel-copy";
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
import { compareChannelsByPopularity } from "./channel-popularity";
import { PersonalChannelConnectPanel } from "./PersonalChannelConnectPanel";
import {
  isPersonalChannelStatusActive,
  useGatewayPersonalChannelSurfaces,
  usePersonalChannelStatus,
  type GatewayPersonalChannelSurfaceItem,
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

// Local-bridge channels (Signal and iMessage today; WeChat has no bridge to
// check at all) have no in-app pairing step — the bridge runs on hardware
// the user configures themselves. This shows the REAL health snapshot for
// one channel_key on one gateway; there is no client-invented "connected"
// state, and no button that claims to "connect" anything.
//
// iMessage is the one exception as of the imsg-based setup panel
// (IMessageSetupPanel.tsx) — it has its own no-gateway copy inline, since
// unlike Signal/WeChat its setup (installing imsg, checking Full Disk
// Access) genuinely does happen in-app once a gateway exists.
const LOCAL_BRIDGE_NO_GATEWAY_HINT: Record<string, string> = {
  signal_personal: "This agent has no computer of its own yet — set one up on the Hardware tab first, then point it at a signal-cli bridge.",
};

//
// TAKES THE SURFACES AS PROPS. IT DOES NOT FETCH.
// ----------------------------------------------
// This used to call useGatewayPersonalChannelSurfaces itself, which meant a
// fresh request with `loading: true` on every MOUNT — i.e. every time the
// customer clicked the Signal card. Against an unreachable box that is a 2-3
// second spinner between the click and anything appearing, while a transported
// card opened instantly on state the tab already had. ChannelsTab now holds
// the one subscription for the whole tab and hands the result down, so a card
// opens with what is already known and the shared poll refreshes underneath.
// The honest "state unknown" rendering is untouched: an unreachable box still
// produces no item, and that still reads as not connected with the reason.
function LocalBridgeChannelStatus({
  channelKey,
  gatewayId,
  items,
  loading,
}: {
  channelKey: string;
  gatewayId: string | null;
  items: GatewayPersonalChannelSurfaceItem[];
  loading: boolean;
}) {
  if (!gatewayId) {
    return (
      <p className="fleet-channel-expand-hint">
        {LOCAL_BRIDGE_NO_GATEWAY_HINT[channelKey] || "This agent has no computer of its own yet — set one up on the Hardware tab first, then point it at this channel's local bridge."}
      </p>
    );
  }
  if (loading) {
    // A bare 14px spinner reserved no height, so the panel visibly grew the
    // instant the fetch resolved into either `.fleet-channel-expand-success`
    // (icon+1 line) or up to three stacked `.fleet-channel-expand-hint`
    // lines — which of those it resolves to isn't knowable ahead of time, so
    // this reserves ONE hint-shaped line, the minimum either real state
    // renders (the success state is also one line tall).
    return <p className="fleet-channel-expand-hint"><span className="fleet-skeleton-bar" style={{ width: "70%", height: 13 }} /></p>;
  }

  const item = items.find((i) => i.channel_key === channelKey) || null;
  if (item?.connected) {
    return (
      <div className="fleet-channel-expand-success">
        <Check size={16} strokeWidth={2} /> Connected{item.connected_identity ? ` — ${item.connected_identity}` : ""}
      </div>
    );
  }
  return (
    <>
      <p className="fleet-channel-expand-hint">{item?.detail || "This channel runs through a local bridge on this agent's own gateway."}</p>
      <p className="fleet-channel-expand-hint">{item?.next_step || "Configure the bridge on this agent's gateway, then this status updates on its own."}</p>
      <p className="fleet-channel-expand-hint" style={{ color: "var(--text-tertiary)" }}>{item?.status_label || "Not connected yet"}</p>
    </>
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
  workspaceId, agentId, agent, onChannelsChanged,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
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
  // ONE subscription to the local-bridge surfaces for the whole tab, started
  // when the tab opens rather than when a card is clicked. Every card that
  // needs it reads from here (LocalBridgeChannelStatus takes it as props;
  // IMessageSetupPanel's own call now hits the same shared store), so opening
  // a first-party channel costs no request and shows no spinner — the fix for
  // "clicking Signal takes 2-3 seconds while a transported card is instant".
  const localBridge = useGatewayPersonalChannelSurfaces(agentGatewayId);
  // Which transported PLATFORM's panel is open, held as its base channel_key
  // (not the entry object) so an open panel re-reads the LIVE row after a
  // provision or a credential save instead of showing a snapshot from click
  // time.
  const [openclawDetailKey, setOpenclawDetailKey] = useState<string | null>(null);
  // Which VARIANT of that platform is being set up — the transported half of
  // the same door pick the first-party panel makes with `selectedDoor`.
  const [openclawDoorKey, setOpenclawDoorKey] = useState<string | null>(null);

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
    /** A card that cannot be opened at all is rendered `disabled` rather than
     *  clicked-into-a-dead-end (product law: no dead controls). Only
     *  first-party "Not configured here" is that — every transported card
     *  opens into something that at minimum explains itself. */
    disabled: boolean;
    active: boolean;
    open: () => void;
  };

  const legacyCards: UnifiedChannelCard[] = CHANNEL_GRID_PLATFORMS.map((platform) => {
    const pill = channelStatePill(byId.get(platform.id));
    return {
      key: `first_party:${platform.id}`,
      label: platform.label,
      iconSrc: CHANNEL_ICONS[platform.id],
      pill,
      waysNote: channelDoorChoiceNote(planChannelDoors(platform.id)),
      disabled: pill.tone === "locked",
      active: expanded === platform.id,
      open: () => handleCardClick(platform, pill),
    };
  });

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
      // 17 of the 19 do (see the provenance table in fleet-icons.ts); IRC and
      // Yuanbao have no obtainable official mark and fall through to the
      // neutral monogram tile, which is also what a channel the transport
      // adds tomorrow will get. Never a guessed or hand-drawn logo.
      iconSrc: CHANNEL_ICONS[platform.iconKey],
      pill: pillRow
        ? channelCardPill(pillRow.remediation)
        : { label: "Unknown", tone: "locked" as const },
      waysNote: channelDoorChoiceNote(planDoors(platform.doors)),
      // "Needs Gateway" stays clickable, exactly like the first-party cards
      // in the same state (channelStatePill / handleCardClick) — pairing a
      // Gateway is a real, actionable next step, so this is never `disabled`.
      disabled: false,
      active: openclawDetailKey === platform.key,
      open: () => {
        setOpenclawDetailKey(platform.key);
        setOpenclawDoorKey(null);
      },
    };
  });

  // Sorted by how many people actually use the platform, not by its first
  // letter — alphabetical opened the grid with ClickClack above Discord and
  // Nostr above WhatsApp. The ranking is authored (it exists in no data we
  // hold) but it is a PREFIX, never a membership test: a channel the transport
  // ships tomorrow is simply unranked and lands at the bottom in alphabetical
  // order, with no code change. See channel-popularity.ts.
  const unifiedChannelCards = [...legacyCards, ...openclawCards].sort(compareChannelsByPopularity);

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

  // OpenClaw's own catalog carries every channel that overlaps a first-party
  // platform too (channel_lane_contract_service.OPENCLAW_SUPERSEDED_CHANNELS)
  // so the owner can be told WHY a channel they've heard of is missing from
  // the transported cards above. Most of those are already real cards via
  // legacyCards now (Telegram/WhatsApp/Discord/Signal/iMessage/Slack) — this
  // is only the leftover that has no first-party card anywhere in this tab
  // (SMS today: it is a Studio business connector, not a per-agent channel).
  // Computed, never a second hand-typed list.
  const legacyLabels = new Set(CHANNEL_GRID_PLATFORMS.map((p) => p.label));
  const unmappedSupersededChannels = openclaw.alreadyAvailable.filter((label) => !legacyLabels.has(label));

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
      {/* Channels vs. Connectors reads as one undifferentiated "integrations"
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
              className={`fleet-btn ${openclaw.repairable.length > 0 ? "fleet-btn--accent-fill" : ""}`}
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

      <div className="fleet-channel-grid">
        {unifiedChannelCards.map((card) => (
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
            {/* The card offers a CHOICE, said on the face so the picker behind
                it is not a surprise. Deliberately not a second pill competing
                with the status one — one line, smaller and dimmer, and absent
                entirely on the single-door cards (most of them). */}
            {card.waysNote ? <span className="fleet-channel-card-ways">{card.waysNote}</span> : null}
          </button>
        ))}
      </div>

      {agentGatewayId && openclaw.loading ? (
        <p className="fleet-subtitle">Reading this agent&apos;s computer…</p>
      ) : null}

      {unmappedSupersededChannels.length > 0 ? (
        <p className="fleet-subtitle openclaw-elsewhere-note">
          {formatChannelList(unmappedSupersededChannels)}{" "}
          {unmappedSupersededChannels.length === 1 ? "connects" : "connect"} elsewhere in Empyralis and{" "}
          {unmappedSupersededChannels.length === 1 ? "isn't" : "aren't"} shown here.
        </p>
      ) : null}

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
                {CHANNEL_ICONS[openclawPlatform.iconKey]
                  ? <img src={CHANNEL_ICONS[openclawPlatform.iconKey]} alt="" width={24} height={24} />
                  : openclawPlatform.label.charAt(0)}
              </span>
              <span className="fleet-channel-banner-title" id="channel-detail-heading">
                {openclawPlatform.label}
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
              {/* The SAME picker a first-party card opens, on the SAME rule:
                   a platform reachable one way opens straight into it, a
                   platform reachable several ways asks first. Nothing here
                   names a channel — the door count is the whole input. */}
              {openclawDoorPlan.mode === "picker" && !openclawActiveDoor ? (
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
                        onClick={() => setOpenclawDoorKey(door.key)}
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
                        {hardwareNote ? (
                          <span className="fleet-wizard-option-note">
                            <Cpu size={11} strokeWidth={2} aria-hidden /> {hardwareNote}
                          </span>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              ) : null}

              {openclawActiveDoor && openclawDoorPlan.mode === "picker" ? (
                <ChosenDoorBar
                  door={openclawActiveDoor}
                  compact
                  onChange={() => setOpenclawDoorKey(null)}
                />
              ) : null}

              {openclawDetail ? (
                <>
                  {openclawDoorPlan.mode === "direct" ? (
                    <p className="fleet-subtitle" style={{ marginTop: 0 }}>
                      {openclawDetail.entry.selection_label}
                    </p>
                  ) : null}

                  {/* No computer bound to this agent at all — never a form
                      the customer could not possibly complete. The generated
                      chips/detail/button below all read a live device's state
                      (`openclawDetail.observed`), which does not exist yet
                      when there is no gateway; showing them here would print
                      invented facts ("not installed", "off") about a box that
                      was never asked. This is the SAME interaction the
                      first-party gateway-less cards already use
                      (LocalBridgeChannelStatus's no-gateway hint below) —
                      one honest sentence pointing at the Hardware tab, no
                      dead control. */}
                  {agentGatewayId ? (
                    <>
                      <div className="openclaw-chips">
                        {openclawDetail.entry.requires_plugin ? (
                          <StateChip ok={Boolean(openclawDetail.observed?.installed)} on="installed" off="not installed" />
                        ) : (
                          <span className="fleet-badge openclaw-chip openclaw-chip--ok" style={{ marginLeft: 0 }}>
                            bundled
                          </span>
                        )}
                        {openclawDetail.entry.connect_method === "credential" ? (
                          <StateChip ok={Boolean(openclawDetail.observed?.configured)} on="credential set" off="no credential" />
                        ) : null}
                        <StateChip ok={Boolean(openclawDetail.observed?.enabled)} on="on" off="off" />
                      </div>

                      {/* Only when there is something to say the chips and the
                          button cannot already say — see Remediation's own doc
                          comment. An empty detail renders nothing at all rather
                          than an empty paragraph holding space open. */}
                      {openclawDetail.remediation.detail ? (
                        <p className="openclaw-channel-detail">{openclawDetail.remediation.detail}</p>
                      ) : null}

                      {/* One control, and only the one this state actually needs. A
                          credential state renders the generated form inline (never a
                          second stacked modal); install/enable render a control that
                          DOES the work and verify-polls the box until its own state
                          catches up, then disappears; the two states with no browser
                          action say so instead of rendering a control that submits
                          nothing. */}
                      {openclawDetail.remediation.kind === "credential" || openclawDetail.remediation.kind === "ready" ? (
                        <div style={{ marginTop: "var(--space-4)" }}>
                          <CredentialForm
                            gatewayId={agentGatewayId}
                            entry={openclawDetail.entry}
                            observed={openclawDetail.observed}
                            onCancel={() => setOpenclawDetailKey(null)}
                            onSaved={async () => {
                              await openclaw.refresh({ silent: true });
                            }}
                          />
                          {/* Only once the channel itself works — configuring
                              which groups may receive replies before there is a
                              working identity to reply WITH has nothing to act
                              on yet. */}
                          {openclawDetail.remediation.kind === "ready" ? (
                            <GroupAllowlistForm
                              gatewayId={agentGatewayId}
                              agentId={agentId}
                              channelKey={openclawDetail.entry.channel_key}
                              channelLabel={openclawDetail.entry.label}
                            />
                          ) : null}
                        </div>
                      ) : openclawDetail.remediation.kind === "install" || openclawDetail.remediation.kind === "enable" ? (
                        <div style={{ marginTop: "var(--space-4)" }}>
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
                                    : openclawDetail.remediation.label}
                              </button>
                            );
                          })()}
                        </div>
                      ) : openclawDetail.remediation.kind === "elsewhere" ? (
                        <p className="openclaw-ready openclaw-ready--muted" style={{ marginTop: "var(--space-4)" }}>
                          <Smartphone size={14} aria-hidden /> Link this one directly on the computer — there is nothing to
                          paste here.
                        </p>
                      ) : null}
                    </>
                  ) : (
                    <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
                      {openclawDetail.remediation.detail}
                    </p>
                  )}
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
                {CHANNEL_ICONS[activePlatform.id]
                  ? <img src={CHANNEL_ICONS[activePlatform.id]} alt="" width={24} height={24} />
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

              {/* Telegram / WhatsApp: full-account (real MTProto / Baileys session),
                   bound to this agent's own gateway via agentGatewayId — not
                   Sage's workspace-wide Connect tab. */}
              {activePlatform.id === "sage_telegram_hosted" && setupDoorKey === "full_account" && (
                <div style={{ marginTop: 12 }}>
                  <PersonalChannelConnectPanel
                    workspaceId={workspaceId}
                    channelKey="telegram_personal"
                    label="Telegram"
                    agentGatewayId={agentGatewayId}
                    agentId={agentId}
                    onConnected={handleChannelsChanged}
                    onDone={() => setExpanded(null)}
                  />
                </div>
              )}
              {activePlatform.id === "whatsapp_personal" && setupDoorKey === "full_account" && (
                <div style={{ marginTop: 12 }}>
                  <PersonalChannelConnectPanel
                    workspaceId={workspaceId}
                    channelKey="whatsapp_personal"
                    label="WhatsApp"
                    agentGatewayId={agentGatewayId}
                    agentId={agentId}
                    onConnected={handleChannelsChanged}
                    onDone={() => setExpanded(null)}
                  />
                </div>
              )}

              {/* Signal: same local-bridge shape as iMessage — no phone/code/QR
                   step of its own, the bridge (signal-cli) lives on hardware the
                   user runs themselves, configured via env vars on this agent's
                   gateway. The only honest thing to show is real bridge health,
                   not a fake "connect" button. */}
              {activePlatform.id === "signal_personal" && setupDoorKey === "full_account" && (
                <div style={{ marginTop: 12 }}>
                  <LocalBridgeChannelStatus
                    channelKey="signal_personal"
                    gatewayId={agentGatewayId}
                    items={localBridge.items}
                    loading={localBridge.loading}
                  />
                </div>
              )}

              {/* iMessage: unlike Signal/WeChat, setup genuinely happens in-app —
                   imsg runs on this agent's own gateway Mac, and the gateway's
                   layered probe (binary / rpc / Full Disk Access / private API)
                   is surfaced live with inline fixes and a Re-check button. The
                   one truly manual step is Full Disk Access, which macOS will
                   not let any process grant to itself — see
                   IMessageSetupPanel.tsx. */}
              {activePlatform.id === "imessage_personal" && setupDoorKey === "full_account" && (
                <div style={{ marginTop: 12 }}>
                  <IMessageSetupPanel gatewayId={agentGatewayId} />
                </div>
              )}

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

function ConnectorsTab({
  workspaceId, agentId, agent,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const projectId = agent?.project_id || "";

  // Same one-liner treatment as ChannelsTab's own subtitle just above, kept
  // visible across every body state (loading/empty/picker) — the whole
  // point is telling the two tabs apart at a glance, not just once loaded.
  const subtitle = <p className="fleet-tab-subtitle">Apps this agent can use</p>;

  if (!agent) {
    // Matches ConnectorPicker's OWN loading shape below it (`.fleet-
    // connector-picker`'s real 2-col grid of title/description/button
    // cards) rather than `FleetCardGridSkeleton`'s 4-col square-icon-card
    // grid (`.fleet-channel-grid`) — this tab never renders that grid, only
    // ConnectorPicker's wide picker-item cards, so the two placeholders
    // shown in sequence (this one, then ConnectorPicker's own once `agent`
    // resolves) used to visibly change shape mid-load.
    return (
      <div>
        {subtitle}
        <div className="fleet-connector-picker" aria-busy="true" aria-label="Loading connectors">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="fleet-connector-picker-item">
              <div className="fleet-skeleton-bar" style={{ width: "40%", height: 13 }} />
              <div className="fleet-skeleton-bar" style={{ width: "90%", height: 10, opacity: 0.7 }} />
              <div className="fleet-skeleton-bar" style={{ width: "60%", height: 10, opacity: 0.7 }} />
              <div className="fleet-skeleton-bar" style={{ width: 76, height: 26, marginTop: 4 }} />
            </div>
          ))}
        </div>
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
          body="Connectors are shared per project — assign one before connecting apps."
        />
      </div>
    );
  }

  return (
    <div>
      {subtitle}
      <ConnectorPicker workspaceId={workspaceId} projectId={projectId} agentId={agentId} />
    </div>
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

// ── Tools ───────────────────────────────────────────────────────────────────

function ToolCustomerAccess({
  tool, busy, onGrant, onRevoke,
}: { tool: FleetTool; busy: boolean; onGrant: () => void; onRevoke: () => void }) {
  const muted = !tool.enabled;
  if (tool.audience_safe) {
    return (
      <span
        className={`fleet-badge${muted ? " fleet-badge--muted" : ""}`}
        style={{ marginLeft: 0 }}
        title={muted ? "Enabled required to run" : "The platform marks this tool safe for anyone to trigger."}
      >
        Safe by default
      </span>
    );
  }
  if (tool.mandate_granted) {
    return (
      <button
        type="button"
        className={`fleet-badge fleet-badge--lock fleet-badge--action${muted ? " fleet-badge--muted" : ""}`}
        style={{ marginLeft: 0 }}
        disabled={busy}
        onClick={onRevoke}
        title={muted ? "Enabled required to run — click to revoke customer access" : "Customers can trigger this. Click to revoke."}
      >
        Granted by you
      </button>
    );
  }
  return (
    <button
      type="button"
      className="fleet-badge fleet-badge--action"
      style={{ marginLeft: 0 }}
      disabled={busy}
      onClick={onGrant}
      title="Only the workspace owner can trigger this. Click to grant customer access."
    >
      Owner only
    </button>
  );
}

function ToolsTab({
  workspaceId, agentId, agent, onChat,
}: { workspaceId: string; agentId: string; agent: FleetAgent | null; onChat: () => void }) {
  const { tools, coreTools, isMaster, loading, refresh } = useFleetAgentTools(workspaceId, agentId);
  // Truth Map B1: a handful of tools (Calendar/Task Runner/Email/CRM) are
  // real but execution_mode="manual" with no direct executor — the toggle
  // above does nothing until the connector named in requires_connector is
  // actually connected. Cross-referencing the same connector list the
  // Connectors tab already fetches, so this stays accurate if a deployment
  // configures Google Workspace OAuth later.
  const { connectors: toolConnectors, loading: connectorsLoading } = useFleetAgentConnectors(workspaceId, agentId);
  const [pending, setPending] = useState<string | null>(null);
  const [mandateBusy, setMandateBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggle(toolId: string, next: boolean) {
    setPending(toolId);
    setError(null);
    try {
      const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { tool_toggles: { [toolId]: next } } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update this tool.");
    } finally {
      setPending(null);
    }
  }

  async function setMandate(toolId: string, grant: boolean) {
    setMandateBusy(toolId);
    setError(null);
    // mandate.audience_tools is replace-semantics server-side, so reconstruct
    // the full desired set from what's currently visible on this tab.
    const nextGranted = new Set(tools.filter((t) => t.mandate_granted).map((t) => t.id));
    if (grant) nextGranted.add(toolId);
    else nextGranted.delete(toolId);
    try {
      const res = await fleetAuthorizedFetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { mandate: { audience_tools: Array.from(nextGranted) } } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(getErrorMessage(data, `HTTP ${res.status}`));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update customer access for this tool.");
    } finally {
      setMandateBusy(null);
    }
  }

  if (loading || connectorsLoading) {
    return <FleetToggleRowsSkeleton rows={6} trailing="switch" label="Loading tools" />;
  }

  const connectorById = new Map(toolConnectors.map((c) => [c.id, c]));

  if (tools.length === 0 && coreTools.length === 0) {
    return (
      <EmptyState
        icon={Wrench}
        title="No tools available"
        body="Ask AI to configure tools for this agent."
        action="Chat to configure"
        onAction={onChat}
      />
    );
  }

  const enabledCount = tools.filter((t) => t.enabled).length;

  return (
    <div className="fleet-config">
      {/* Founder feedback (copy discipline pass): this used to open with a
          2-sentence policy paragraph explaining the audience model before
          any control appeared — "a professional tool labels, it does not
          lecture." The badge legend right below already IS the label: every
          tool row wears one of these three badges, so what "customer" vs
          "owner" access means is shown at the point of use, not read once
          and forgotten above the fold. The `hint` tooltip on the Properties
          panel's "Customer access" row (this file, PanelRow) still carries
          the one-sentence version for whoever hovers it. */}
      <div
        className="fleet-subtitle"
        style={{ marginTop: 0, marginBottom: 12, display: "flex", flexWrap: "wrap", gap: "6px 16px", alignItems: "center" }}
      >
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className="fleet-badge" style={{ marginLeft: 0 }}>Safe by default</span> anyone can use it
        </span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className="fleet-badge fleet-badge--lock" style={{ marginLeft: 0 }}>Granted by you</span> you opened it up
        </span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className="fleet-badge" style={{ marginLeft: 0 }}>Owner only</span> the default — click to grant
        </span>
      </div>
      {isMaster ? (
        <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
          This is the operator agent — it has unrestricted tool access, not gated by these toggles.
        </p>
      ) : (
        <>
          <div className="fleet-detail-section-title" style={{ marginTop: 0 }}>
            {enabledCount} of {tools.length} tools enabled
          </div>
        </>
      )}
      {tools.map((t) => {
        const requiredConnector = t.requires_connector ? connectorById.get(t.requires_connector) : undefined;
        const connectorMissing = Boolean(t.requires_connector) && !requiredConnector?.connected;
        return (
        <div key={t.id} className="fleet-toggle-row">
          <div style={{ minWidth: 0 }}>
            <div className="fleet-toggle-row-label">{t.label}</div>
            {t.description && <div className="fleet-toggle-row-desc">{t.description}</div>}
            {!connectorMissing && !t.enabled && (t.audience_safe || t.mandate_granted) && (
              <div className="fleet-toggle-row-desc">Enabled required to run</div>
            )}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
            {!isMaster && (
              <ToolCustomerAccess
                tool={t}
                busy={mandateBusy === t.id}
                onGrant={() => setMandate(t.id, true)}
                onRevoke={() => setMandate(t.id, false)}
              />
            )}
            <button
              type="button"
              role="switch"
              aria-checked={t.enabled}
              aria-label={`${t.enabled ? "Disable" : "Enable"} ${t.label}`}
              className={`fleet-toggle${t.enabled ? " is-on" : ""}`}
              disabled={isMaster || pending === t.id}
              onClick={() => toggle(t.id, !t.enabled)}
            />
          </div>
        </div>
        );
      })}
      {error && <p className="fleet-channel-expand-error">{error}</p>}
      {coreTools.length > 0 && (
        <Disclosure label={`${coreTools.length} core ${coreTools.length === 1 ? "tool" : "tools"} — always on`}>
          <p className="fleet-channel-expand-hint" style={{ marginTop: 0 }}>
            Plumbing every agent needs to function — there&apos;s no toggle for these because there&apos;s nothing to turn off. Anything with its own real toggle is listed above instead.
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {coreTools.map((name) => (
              <span key={name} className="fleet-badge" style={{ marginLeft: 0 }}>{name}</span>
            ))}
          </div>
        </Disclosure>
      )}
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
  CLI_REASONING_EFFORT_OPTIONS_BY_RUNTIME, type CliSubscriptionRuntime,
} from "./fleet-provider-constants";
import {
  GatewayBoxPicker,
  deriveAgentStatus,
  gatewayRuntimeReady,
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
} from "./fleet-model-config";

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
        <span className="fleet-config-value">{cost === null ? "…" : `$${cost.toFixed(4)}`}</span>
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

  // cli_subscription's own picker (Phase 1: reasoning-effort control) — the
  // paired Gateway's llm.generate now forwards this into the CLI's own
  // --effort (claude_code) / -c model_reasoning_effort= (codex) /
  // --reasoning-effort (grok_build) flag, so unlike platform_credits/byok_api
  // this can't share REASONING_EFFORT_OPTIONS: each CLI accepts a genuinely
  // different value set (verified live against each CLI's own --help/docs —
  // see CLI_REASONING_EFFORT_OPTIONS_BY_RUNTIME's own docstring). Cursor CLI
  // has no reasoning-effort control at all (empty options list), so this
  // falls back to the same unsupported note "local" gets rather than
  // rendering an empty, misleading <select>. Gated by cliRuntime, which
  // already tracks the Subscription <select> above, so switching the
  // subscription provider swaps the option list (or the note) live.
  function renderCliReasoningEffortPicker() {
    const options = CLI_REASONING_EFFORT_OPTIONS_BY_RUNTIME[cliRuntime];
    if (options.length === 0) {
      return (
        <p className="fleet-channel-expand-hint">
          {RUNTIME_LABELS[cliRuntime]} has no reasoning-effort control today.
        </p>
      );
    }
    return (
      <>
        <label className="fleet-wizard-label">Reasoning effort</label>
        <select
          className="fleet-wizard-input"
          value={reasoningEffort}
          onChange={(e) => { setReasoningEffort(e.currentTarget.value); setSaved(false); }}
        >
          {options.map((o) => (
            <option key={o.value || "unset"} value={o.value}>{o.label}</option>
          ))}
        </select>
        <p className="fleet-channel-expand-hint">
          Higher effort can solve harder problems but costs more and replies slower. Passed straight to {RUNTIME_LABELS[cliRuntime]}’s own reasoning control.
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
          ) : (
            <>
              <label className="fleet-wizard-label">Model</label>
              <select className="fleet-wizard-input" value={selectedModel} onChange={(e) => { setSelectedModel(e.currentTarget.value); setSaved(false); }}>
                {modelsForProvider(provider).map((m) => <option key={m} value={m}>{modelOptionLabel(provider, m)}</option>)}
              </select>
              <ModelSizeWarning provider={provider} model={selectedModel} />
            </>
          )}
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
          {FREEFORM_MODEL_PROVIDERS.has(provider) ? (
            <>
              <label className="fleet-wizard-label">Model ID (optional)</label>
              <input
                className="fleet-wizard-input"
                value={selectedModel}
                onChange={(e) => { setSelectedModel(e.currentTarget.value); setSaved(false); }}
                placeholder="Leave blank to use the CLI's own default"
              />
              <p className="fleet-channel-expand-hint">
                {RUNTIME_LABELS[cliRuntime]} has no published model-id catalog — enter one only if you know it accepts it.
              </p>
            </>
          ) : (() => {
            // URGENT fix (2026-08-14): for Codex, show what the paired box's
            // OWN CLI reports it can actually run right now, not the static
            // MODELS_BY_PROVIDER mirror — that mirror is exactly what let
            // "gpt-5.4" (retired by OpenAI, unusable under this account's
            // auth mode) sit in this list looking like a normal choice. See
            // useCodexModelCatalog's doc comment in fleet-model-config.ts.
            const liveModels = provider === "openai-codex" ? visibleCodexModels(codexModelCatalog) : null;
            const options = liveModels
              ? liveModels.map((m) => ({ id: m.id, label: m.isDefault ? `${m.displayName} (Recommended)` : m.displayName }))
              : modelsForProvider(provider).map((m) => ({ id: m, label: modelOptionLabel(provider, m) }));
            const showStaleNote = provider === "openai-codex" && !liveModels;
            return (
              <>
                <label className="fleet-wizard-label">Model</label>
                <select className="fleet-wizard-input" value={selectedModel} onChange={(e) => { setSelectedModel(e.currentTarget.value); setSaved(false); }}>
                  {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                </select>
                {showStaleNote && (
                  <p className="fleet-channel-expand-hint">
                    {effectiveGatewayId
                      ? "Couldn't check this computer's actual Codex models right now — this list may include models that have since been renamed or retired."
                      : "Pick a computer below to see this account's real, currently-usable Codex models."}
                  </p>
                )}
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

