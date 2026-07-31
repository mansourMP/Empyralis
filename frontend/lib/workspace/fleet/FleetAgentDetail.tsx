"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import {
  Brain,
  Check,
  ChevronDown,
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
  Settings,
  Sparkles,
  Square,
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
  resumeFleetAgent,
  stopFleetAgent,
  useFleetAgentActivity,
  useFleetAgentChannels,
  useFleetAgentConnectors,
  useFleetAgentTools,
  useFleetAgentCapabilities,
  useFleetAgentSchedule,
  previewFleetAgentSchedule,
  createFleetAgentSchedule,
  deleteFleetAgentSchedule,
  friendlyChannelOwnershipError,
  type FleetAgent,
  type FleetAgentActivity,
  type FleetChannel,
  type FleetTool,
  type FleetCapability,
  type FleetScheduleItem,
} from "./fleet-data";
import { timeAgo, formatDate, formatDateTime, formatTime, formatNumber, usagePayerLabel, type AgentStatusTone, type UsageMatrixRow } from "./fleet-presentation";
import { StatusChip, StatusDot } from "./fleet-indicators";
import { PanelSection, PanelRow, FleetRightPanel, type PanelValueTone } from "./FleetRightPanel";
import { UsageStat, bucketSeries, type UsageBucket } from "./fleet-sparkline";
import { HeaderAction } from "./Breadcrumbs";
import { CHANNEL_ICONS } from "./fleet-icons";
import { ConnectorPicker } from "./ConnectorPicker";
import { buildCookieAuthHeaders } from "@/lib/auth/csrf";
import { providerLabel } from "./fleet-provider-constants";
import { RUNTIME_LABELS } from "./gateway-box-picker";

import "./agent-configure-sheet.css";

/** Single source of truth for "what should this agent's Model summary say" —
 *  used by both the sidebar's permanent one-line Model row and the Model
 *  tab's own Provider/Model fields, so they can't independently drift the
 *  way they did before: the sidebar had no cli_subscription case at all and
 *  fell straight through to "Platform default" even when Codex was
 *  correctly bound to a paired Gateway. */
function resolveAgentModelSummary(modelConfig: Record<string, any> | undefined | null): {
  provider: string;
  model: string;
  isPlatformDefault: boolean;
  /** model_config.reasoning_effort, or "" when unset OR when the mode
   *  doesn't apply it (local — see REASONING_EFFORT_SUPPORTED_MODES).
   *  cli_subscription now applies it for real too (Phase 1: reasoning-
   *  effort control — the paired Gateway's llm.generate forwards it into
   *  the CLI's own --effort / -c model_reasoning_effort= flag), so it's
   *  included here rather than hidden. Kept off the summary only for
   *  local, which still has no reasoning-effort control at all today. */
  reasoningEffort: string;
} {
  const config = modelConfig || {};
  const mode = config.mode;
  const reasoningEffort = (REASONING_EFFORT_SUPPORTED_MODES.has(mode) || mode === "cli_subscription")
    ? String(config.reasoning_effort || "")
    : "";
  if (mode === "cli_subscription") {
    const runtime = normalizeCliRuntime(config.runtime);
    const provider = config.provider ? providerLabel(config.provider) : RUNTIME_LABELS[runtime];
    return { provider, model: config.model || "CLI default", isPlatformDefault: false, reasoningEffort };
  }
  if (mode === "local") {
    return { provider: "Local", model: config.model || "Ollama", isPlatformDefault: false, reasoningEffort };
  }
  if (config.provider || config.model || config.resolved_model) {
    return {
      provider: config.provider ? providerLabel(config.provider) : (config.resolved_provider_label || "Platform default"),
      model: config.model || config.resolved_model || "Default",
      isPlatformDefault: false,
      reasoningEffort,
    };
  }
  return { provider: "Platform default", model: "Platform default", isPlatformDefault: true, reasoningEffort };
}

function formatModelSummaryLine(summary: ReturnType<typeof resolveAgentModelSummary>): string {
  const base = summary.isPlatformDefault
    ? "Platform default"
    : summary.provider === summary.model
      ? summary.model
      : `${summary.provider} · ${summary.model}`;
  return summary.reasoningEffort ? `${base} · ${reasoningEffortLabel(summary.reasoningEffort)} reasoning` : base;
}

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

type TabId = "overview" | "work" | "channels" | "connectors" | "hardware" | "model" | "memory" | "tools" | "capabilities" | "chat";

// Single source of id/label/icon truth for every one of the nine sections —
// both the permanent top strip (three of these, TOP_TAB_IDS below) and the
// Configure sheet's GroupedRail groups (the other six, CONFIGURE_GROUPS
// below) read labels/icons from here so neither surface can drift from the
// other. Nine ids remain valid [tab] route segments regardless of which
// surface renders them (VALID_TABS, [tab]/page.tsx) — collapsing the tab
// BAR from nine to three is a rendering change, not a routing one. Order
// here no longer drives on-screen order (each surface picks its own
// members/grouping explicitly by id below), so it's just declaration order.
const TABS: { id: TabId; label: string; icon: LucideIcon }[] = [
  { id: "overview", label: "Overview", icon: LayoutGrid },
  { id: "model", label: "Model", icon: Sparkles },
  { id: "work", label: "Work", icon: Inbox },
  { id: "channels", label: "Channels", icon: Radio },
  { id: "connectors", label: "Connectors", icon: Plug },
  { id: "tools", label: "Tools", icon: Wrench },
  { id: "capabilities", label: "Capabilities", icon: Wand2 },
  { id: "hardware", label: "Hardware", icon: Cpu },
  { id: "memory", label: "Memory", icon: Brain },
];

// The three you actually watch day to day — permanent top strip. Everything
// else (six ids below) moved into the Configure sheet, opened by the
// trigger button next to this strip; nothing was deleted (founder: "the
// rest must not be deleted"), it just isn't equal-billing top-level nav
// anymore (UI-CONTRACT: "a surface must earn its place").
const TOP_TAB_IDS = new Set<TabId>(["overview", "work", "memory"]);

// Configure sheet groups — BRAIN (what it thinks with) / REACH (how it's
// reached, and what it can reach out to) / COMPUTE (what it runs on).
// Rendered via GroupedRail, the same component Settings uses, per the
// design's whole point: one rail component, two callers, not a rail built
// twice. Order within each group is the order the old flat tab strip had
// them in.
const CONFIGURE_GROUPS: { id: string; label: string; tabs: TabId[] }[] = [
  { id: "brain", label: "Brain", tabs: ["model", "capabilities"] },
  { id: "reach", label: "Reach", tabs: ["channels", "connectors", "tools"] },
  { id: "compute", label: "Compute", tabs: ["hardware"] },
];
const CONFIGURE_TAB_IDS = new Set<TabId>(CONFIGURE_GROUPS.flatMap((g) => g.tabs));

// Same localStorage-persisted-collapse idiom as the primary rail's
// COLLAPSED_KEY (fleet-preferences.ts) — a distinct key because this is a
// per-page (agent detail), not per-account, preference.
const PROPERTIES_COLLAPSED_KEY = "fleet:agent-detail-properties-collapsed";

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
  // unresolved-defaults-to-"overview" coercion combined to silently stomp
  // whatever tab the user had just clicked back to Overview. Deriving
  // instead of storing means there is no local copy to desync — whatever
  // the URL says is what renders. [tab]/page.tsx now passes `undefined`
  // (never a manufactured "overview") while a navigation is still
  // resolving, so the "overview" fallback below only ever fires for a
  // genuine first paint before routing has resolved at all.
  const activeTab: TabId = initialTab || "overview";
  // Chat tab's mobile-only properties drawer (see propertiesContent below) —
  // every other tab keeps the permanent column, so this stays false and unused there.
  const [mobilePropertiesOpen, setMobilePropertiesOpen] = useState(false);
  // Desktop/tablet Properties RAIL collapse — same persisted-preference
  // idiom as the primary rail's own collapse (fleet-preferences.ts's
  // COLLAPSED_KEY). Default when nothing is saved yet: CLOSED.
  //
  // This page is genuinely SSR'd (a hard reload or a direct link hits the
  // server, not just client-side navigation), which rules out the seemingly
  // obvious "read localStorage straight in the useState initializer, guarded
  // by typeof window" approach: verified in this exact build that it
  // produces a real, permanently-stuck-wrong render for a returning visitor
  // who'd previously left the rail OPEN. The server has no localStorage, so
  // it always emits the closed markup; hydrating with a client initializer
  // that reads "open" from localStorage makes React's *state* correct
  // immediately, but React's hydration reconciler does not patch that class
  // of attribute/child mismatch to match it — confirmed via the dev
  // console's own "This won't be patched up" hydration warning, and by
  // inspecting the live DOM afterward: the aside stayed visually collapsed
  // (0-width) with the state already reporting expanded, un-fixable by any
  // later render that merely reaches the same value again (React bails out
  // on a same-value setState, so nothing ever re-triggers the patch).
  //
  // The fix that's actually hydration-safe: the initializer always returns
  // the SSR-identical default (closed) — server and first client render
  // agree, so hydration has nothing to patch and no warning fires — and a
  // useLayoutEffect (not useEffect) performs the real localStorage read as
  // a genuine value transition immediately after mount, synchronously
  // before the browser's first paint. That's still the "no open-then-close
  // flip" contract the initializer alone was meant to deliver (nothing is
  // visible before this runs), it's just done as an honest post-mount state
  // change instead of folding it into the value hydration already
  // committed — so React actually applies it. A no-op on every ordinary
  // in-app navigation between agents (Link clicks never involve SSR/
  // hydration at all — the previous value simply carries over or the fresh
  // instance's effect reads the same localStorage a soft nav would've too).
  const [propertiesCollapsed, setPropertiesCollapsed] = useState(true);
  useLayoutEffect(() => {
    try {
      const stored = window.localStorage.getItem(PROPERTIES_COLLAPSED_KEY);
      const shouldBeCollapsed = stored === null ? true : stored === "1";
      setPropertiesCollapsed((prev) => (prev === shouldBeCollapsed ? prev : shouldBeCollapsed));
    } catch {
      /* localStorage unavailable — keep the default (closed) */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const togglePropertiesCollapsed = useCallback(() => {
    setPropertiesCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(PROPERTIES_COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);
  const { events, loading: activityLoading } = useFleetAgentActivity(workspaceId, agentId);
  const { channels, refresh: refreshChannels, telegramBotConnected, slackChannelBinding } = useFleetAgentChannels(workspaceId, agentId);
  const { connectors } = useFleetAgentConnectors(workspaceId, agentId);
  const [costPeriod, setCostPeriod] = useState<CostPeriod>("day");
  const [costToday, setCostToday] = useState<number | null>(null);
  const [costBuckets, setCostBuckets] = useState<UsageBucket[]>([]);
  const [costMatrix, setCostMatrix] = useState<UsageMatrixRow[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=agent&id=${encodeURIComponent(agentId)}&period=${costPeriod}`, { credentials: "include" })
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
  const resolvedModel = formatModelSummaryLine(resolveAgentModelSummary(agent?.model_config));
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
  const role = agent?.role || "agent";
  const isMaster = role === "operator";
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
  // the (now three) top tabs and the Configure sheet's GroupedRail items —
  // real hrefs so cmd-click/middle-click open a new tab, per the "primary
  // navigation is real links" rule GroupedRail.tsx itself already follows.
  const pathname = usePathname();
  const tabHref = useCallback((tab: TabId) => pathname.replace(/\/[^/]+$/, `/${tab}`), [pathname]);

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
  useEffect(() => {
    activeTabRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
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
  // three top tabs is active), so without this, focus would fall through to
  // <body> instead of landing somewhere deliberate inside the sheet.
  const sheetRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (sheetOpen) sheetRef.current?.focus();
  }, [sheetOpen]);

  // Permanent — space is ALWAYS reserved, a real flex sibling of the tab
  // body, never an overlay/toggle (that pattern stays on LIST pages only;
  // see FleetToolbar's panelOpen/onTogglePanel props). Fills what used to be
  // a blank right half at every viewport width instead of hiding behind a
  // click. The one exception is the Chat tab's mobile layout (<=768px):
  // that tab needs its full viewport height for the pinned composer (see
  // fleet-theme.css's Chat-tab mobile override), so there isn't room for a
  // stacked-below column like every other tab gets. Rather than hide
  // Properties with no access route at all, that combination alone swaps
  // to the toggle-opened drawer below (mobilePropertiesOpen) — same content,
  // reused via propertiesContent so both surfaces can never drift apart.
  const propertiesContent = (
    <PanelSection title="Properties">
      <PanelRow label="Status" value={<StatusChip tone={status.tone} label={status.label} />} />
      <PanelRow label="Placement" value={placement.label} tone={HARDWARE_PLACEMENT_PANEL_TONE[placement.tone]} />
      <PanelRow label="Role" value={<span style={{ textTransform: "capitalize" }}>{role}</span>} />
      {!isMaster && (
        <PanelRow
          label="Customer access"
          value={`${customerAccessCount} ${customerAccessCount === 1 ? "tool" : "tools"}`}
          tone={customerAccessCount > 0 ? "default" : "muted"}
          hint="Everyone who messages this agent is a customer at support-tier — they can request, not command. You, the owner, keep full access."
        />
      )}
      {/* Read-only fact for now — subagents_enabled is already in every
          FleetAgent API response and was read by nothing. The functional
          on/off control is being wired separately (backend tool registry +
          delegation engine); this row exists so the current value is at
          least visible, and is a straight swap for that real toggle later. */}
      <PanelRow
        label="Sub-agents"
        value={agent?.subagents_enabled ? "Enabled" : "Disabled"}
        tone={agent?.subagents_enabled ? "default" : "muted"}
        hint="Whether this agent can delegate work to sub-agents it spins up itself."
      />
      {/* Not a plain PanelRow: the value is a real picker trigger (opens
          AgentModelPickerRow's popover), which needs `overflow: visible` on
          its wrapper to avoid getting clipped by the generic value span's
          ellipsis styling — see fleet-panel-row-value--interactive in
          fleet-theme.css. */}
      <div className="fleet-panel-row">
        <span className="fleet-panel-row-label">
          <span>Model</span>
        </span>
        <span className="fleet-panel-row-value fleet-panel-row-value--interactive">
          <AgentModelPickerRow
            workspaceId={workspaceId}
            agentId={agentId}
            agent={agent}
            resolvedModel={resolvedModel}
            onSaved={onRenamed}
          />
        </span>
      </div>
      <UsageStat
        label={costPeriodLabel(costPeriod)}
        total={costToday ?? 0}
        formattedTotal={costToday === null ? "…" : `$${costToday.toFixed(4)}`}
        values={bucketSeries(costBuckets, "usd_cost")}
        action={<CostPeriodToggle period={costPeriod} onChange={setCostPeriod} />}
      />
      {/* Full attribution, not just a total: every real model/source this
          agent has actually billed against, all-time — real tokens at that
          model's real per-1M provider price, whoever pays for it. "Not
          priced" (never a fabricated $0.00) when the source has no per-token
          price to charge, e.g. a flat CLI subscription turn. */}
      {[...costMatrix]
        .sort((a, b) => b.usd_cost - a.usd_cost)
        .slice(0, 5)
        .map((row, i) => {
          const modelLabel = [row.provider, row.model].filter(Boolean).join(" · ") || "Unknown model";
          return (
            <PanelRow
              key={`${row.provider}:${row.model}:${row.mode}:${i}`}
              label={modelLabel}
              hint={`${usagePayerLabel(row.payer)} · ${formatNumber(row.tokens_in)} in / ${formatNumber(row.tokens_out)} out`}
              value={row.pricing_known ? `$${row.usd_cost.toFixed(4)}` : "Not priced"}
              tone={row.pricing_known ? "default" : "muted"}
            />
          );
        })}
      <PanelRow label="Channels" value={connectedChannels} />
      <PanelRow label="Connectors" value={connectedConnectors} />
    </PanelSection>
  );
  // No suppressHydrationWarning needed here: propertiesCollapsed's useState
  // default (true/closed) is identical on the server and the first client
  // render (see above), so there is nothing for hydration to mismatch on —
  // the useLayoutEffect correction happens strictly after hydration commits,
  // as an ordinary client-side state update.
  const propertiesPanel = (
    <aside
      className={`fleet-detail-properties${propertiesCollapsed ? " fleet-detail-properties--collapsed" : ""}`}
      aria-label="Properties"
      aria-hidden={propertiesCollapsed || undefined}
    >
      {propertiesContent}
    </aside>
  );

  const inner = (
    <>
      {/* Tabs live at the TOP, under the breadcrumb — one navigation only.
          The properties column to the right is a collapsible RIGHT RAIL
          (fleet-detail-properties-rail-toggle below), mirroring the primary
          rail's own collapse (PrimaryRail.tsx's fleet-rail-control-btn--
          collapse) rather than the list pages' overlay toggle (FleetToolbar).
          The lone exception is a mobile-only Properties DRAWER toggle on the
          Chat tab (see propertiesContent above) — CSS keeps it hidden except
          at <=768px, where it replaces the rail toggle for that one tab. */}
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
            {/* The three you actually watch day to day — TOP_TAB_IDS. Real
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
        {/* Opens the Configure sheet (below) on the other six sections —
            Model, Capabilities, Channels, Connectors, Tools, Hardware —
            grouped Brain/Reach/Compute via the same GroupedRail Settings
            uses. Real link: closed, it goes to the first Brain item
            (Model); already open, it points at whatever section is active,
            so a second click/cmd-click is a same-URL no-op rather than a
            jump back to Model. */}
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
        <button
          type="button"
          className="fleet-icon-btn fleet-detail-properties-rail-toggle"
          onClick={togglePropertiesCollapsed}
          aria-label={propertiesCollapsed ? "Show properties" : "Hide properties"}
          aria-expanded={!propertiesCollapsed}
          title={propertiesCollapsed ? "Show properties" : "Hide properties"}
        >
          {propertiesCollapsed ? <PanelRightOpen size={16} strokeWidth={1.75} /> : <PanelRightClose size={16} strokeWidth={1.75} />}
        </button>
        {activeTab === "chat" && (
          <button
            type="button"
            className="fleet-icon-btn fleet-detail-properties-toggle"
            onClick={() => setMobilePropertiesOpen(true)}
            aria-label="Show properties"
            title="Properties"
          >
            <PanelRightOpen size={16} strokeWidth={1.75} />
          </button>
        )}
      </div>

      {/* Columns: main content sheet + the properties rail, a flex sibling
          that reserves its width while expanded and collapses to zero (the
          sheet reclaiming that width) via propertiesCollapsed/the rail
          toggle above — same collapse contract as the primary rail, just on
          the right edge (desktop/tablet, and every mobile tab except Chat —
          see propertiesContent above). */}
      <div className="fleet-detail-columns">
        <div className="fleet-detail-body">
          {/* Only the three top-level tabs (plus Chat, reached via
              onChat/"Chat with this agent" rather than this bar) render
              here now. The other six render inside the Configure sheet
              below — configureSheet — same components, same props, moved
              rather than duplicated. */}
          {activeTab === "overview" && (
            <OverviewTab
              workspaceId={workspaceId}
              agentId={agentId}
              agent={agent}
              status={status}
              isMaster={isMaster}
              events={events}
              loading={activityLoading}
              onChat={() => onChat(agentId)}
              onRenamed={onRenamed}
            />
          )}
          {activeTab === "work" && <WorkTab workspaceId={workspaceId} agentId={agentId} agent={agent} onAgentChanged={onRenamed} />}
          {activeTab === "memory" && (
            <MemoryTab workspaceId={workspaceId} agentId={agentId} agent={agent} onChat={() => onChat(agentId)} />
          )}
          {activeTab === "chat" && <ChatTab workspaceId={workspaceId} agentId={agentId} agent={agent} />}
        </div>
        {propertiesPanel}
        {activeTab === "chat" && (
          <FleetRightPanel open={mobilePropertiesOpen} onClose={() => setMobilePropertiesOpen(false)}>
            {propertiesContent}
          </FleetRightPanel>
        )}
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

  // Closing goes to Overview — the natural top-level default — mirroring
  // [tab]/page.tsx's own convention of coercing an unresolved/invalid tab to
  // "overview" (see its rawTab comment). Same selectTab→onTabChange→
  // router.replace path every other tab switch in this file already uses,
  // not a one-off router call.
  const closeSheet = () => selectTab("overview");

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
            {activeTab === "model" && <ModelTab workspaceId={workspaceId} agentId={agentId} agent={agent} onSaved={onRenamed} />}
            {activeTab === "capabilities" && (
              <CapabilitiesTab workspaceId={workspaceId} agentId={agentId} agent={agent} />
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
      {(activeTab === "overview" || activeTab === "work") && (
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
            <button type="button" className="fleet-btn fleet-btn--accent" onClick={() => onChat(agentId)}>
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

// ── Overview ────────────────────────────────────────────────────────────────

/** One compact status LINE, one sentence, one status vocabulary
 *  (Ready / Working / Stopped / Offline / Error) — the exact same `status`
 *  the Agents list, Project rows, the inline config row, and the Properties
 *  panel all derive from, so this never reads differently from any of them.
 *  No second chip here: a dot (color) plus a sentence (words), never two
 *  competing labels for the same field. Every clause is honest-when-empty —
 *  a never-run agent reads "Ready · no activity yet", not a stale claim. */
function NowStrip({ agent, status }: { agent: FleetAgent | null; status: { tone: AgentStatusTone; label: string } }) {
  if (!agent) return null;
  const sentence = (() => {
    if (status.tone === "working") return "Working on a task";
    if (status.tone === "stopped") return `Stopped by ${agent.stopped?.stopped_by_label || "an owner"}`;
    if (status.tone === "ready") {
      return agent.last_activity ? `Ready · last active ${timeAgo(agent.last_activity)}` : "Ready · no activity yet";
    }
    if (status.tone === "offline") {
      return agent.last_heartbeat ? `Offline · last heartbeat ${timeAgo(agent.last_heartbeat)}` : "Offline · never heartbeat";
    }
    return status.label; // Error, Not deployed
  })();
  return (
    <div className="fleet-now-strip" aria-label="Current status">
      <StatusDot tone={status.tone} size={7} />
      <span className="fleet-now-meta">{sentence}</span>
    </div>
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

// U3-H: the warn dot used to fire on anything status !== "logged" — backwards
// for a routine "completed" event (specialist/sage turns log "logged";
// nothing else does, so every other successful status read as a warning).
// An explicit bad-status allowlist instead: only genuinely bad outcomes turn
// the dot red.
const BAD_ACTIVITY_STATUSES = new Set(["error", "failed", "blocked"]);
function isBadActivityStatus(status: string): boolean {
  return BAD_ACTIVITY_STATUSES.has(status.toLowerCase());
}

/** activity_ledger_events rows are already ORDER BY created_at DESC from the
 *  backend — grouping by day in iteration order preserves that ordering
 *  without a separate sort. */
function groupActivityByDay(events: FleetAgentActivity[]): { day: string; items: FleetAgentActivity[] }[] {
  const groups: { day: string; items: FleetAgentActivity[] }[] = [];
  const byDay = new Map<string, FleetAgentActivity[]>();
  for (const event of events) {
    const parsed = new Date(event.created_at);
    const day = Number.isNaN(parsed.getTime())
      ? "Unknown date"
      : formatDate(parsed, { weekday: "long", month: "long", day: "numeric" });
    if (!byDay.has(day)) {
      byDay.set(day, []);
      groups.push({ day, items: byDay.get(day)! });
    }
    byDay.get(day)!.push(event);
  }
  return groups;
}

function OverviewTab({
  workspaceId,
  agentId,
  agent,
  status,
  isMaster,
  events,
  loading,
  onRenamed,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  status: { tone: AgentStatusTone; label: string };
  isMaster: boolean;
  events: FleetAgentActivity[];
  loading: boolean;
  onChat: () => void;
  onRenamed?: () => void;
}) {
  const dayGroups = groupActivityByDay(events);

  return (
    <div className="fleet-detail-overview">
      {agent && (
        <AgentTitle workspaceId={workspaceId} agentId={agentId} label={agent.label || ""} onRenamed={onRenamed} />
      )}
      <NowStrip agent={agent} status={status} />

      {/* Minimal main column — Status/Placement/Role/Customer access now
          live in the permanent properties column (see FleetAgentDetail's
          propertiesPanel); this column is just the THING itself: persona,
          schedule, activity. */}
      {!isMaster && agent && (
        <PersonaEditor workspaceId={workspaceId} agentId={agentId} agent={agent} />
      )}

      {!isMaster && agent && (
        <ScheduleSection workspaceId={workspaceId} agentId={agentId} />
      )}

      <div className="fleet-detail-section-title">Recent activity</div>
      {loading ? (
        <div className="fleet-activity-skeleton" aria-label="Loading activity">
          {[68, 52, 60].map((w, i) => (
            <div key={i} className="fleet-skeleton-row">
              <div className="fleet-skeleton-bar" style={{ width: 8 }} />
              <div style={{ flex: 1 }}>
                <div className="fleet-skeleton-bar" style={{ width: `${w}%`, marginBottom: 6 }} />
                <div className="fleet-skeleton-bar" style={{ width: `${w - 24}%`, opacity: 0.6 }} />
              </div>
            </div>
          ))}
        </div>
      ) : events.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title="No activity yet"
          body="Events appear here after this agent processes its first turn. Use “Chat with this agent” to send the first one."
        />
      ) : (
        <div className="fleet-activity">
          {dayGroups.map((group) => (
            <div key={group.day} className="fleet-activity-day-group">
              <div className="fleet-activity-day-heading">{group.day}</div>
              {group.items.map((event) => (
                <div key={event.event_id} className="fleet-activity-item">
                  <div className={`fleet-activity-dot${isBadActivityStatus(event.status) ? " is-warn" : ""}`} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="fleet-activity-title">{event.title}</div>
                    <div className="fleet-activity-meta">
                      <span>{event.event_class}</span>
                      <span>·</span>
                      <span>{event.action}</span>
                      {event.channel && (
                        <>
                          <span>·</span>
                          <span>via {event.channel}</span>
                        </>
                      )}
                      <span>·</span>
                      <span className="fleet-activity-time">
                        {formatTime(event.created_at, { hour: "numeric", minute: "2-digit" })}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
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
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { display_name: next } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
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
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { instructions: draft } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
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
        placeholder="What this agent is and how it should behave — e.g. “You handle customer refund requests. Be concise, and always confirm the order number before acting.”"
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
        <div className="fleet-activity-skeleton" aria-label="Loading schedule"><div className="fleet-skeleton-bar" style={{ width: "50%" }} /></div>
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
// scope — over a per-agent thread ("thread_agent_{agentId}"), the same
// convention channel-bound turns use. See AgentChat's context_hints.metadata.
// active_agent_install_id, read by specialist_runtime_context.resolve_
// specialist_runtime_context.
function ChatTab({ workspaceId, agentId, agent }: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const label = agent?.label || "this agent";
  return (
    <div className="fleet-agent-chat-panel">
      <AgentChat
        workspaceId={workspaceId}
        threadId={`thread_agent_${agentId}`}
        agentInstallId={agentId}
        emptyIcon={MessageSquare}
        emptyTitle={`Message ${label}`}
        emptyBody={`Your owner test chat with ${label} — full access, not what a real customer would see (this tab always runs at owner tier). It replies as itself, using whatever's configured on the Model and Tools tabs.`}
        placeholder={`Message ${label}…`}
        sourceTag="fleet_agent_chat"
      />
    </div>
  );
}

// ── Channels ────────────────────────────────────────────────────────────────

import { IMessageSetupPanel } from "./IMessageSetupPanel";
import { PersonalChannelConnectPanel } from "./PersonalChannelConnectPanel";
import {
  isPersonalChannelStatusActive,
  useGatewayPersonalChannelSurfaces,
  usePersonalChannelStatus,
  type PersonalChannelKey,
} from "./personal-channel-pairing";

// Fixed platform order the grid renders in, mapped to the backend
// connection id that carries that platform's live status.
const CHANNEL_GRID_PLATFORMS: { label: string; id: string }[] = [
  { label: "Telegram", id: "sage_telegram_hosted" },
  { label: "Slack", id: "slack" },
  { label: "Discord", id: "discord_bot" },
  { label: "WhatsApp", id: "whatsapp_personal" },
  { label: "Signal", id: "signal_personal" },
  { label: "iMessage", id: "imessage_personal" },
  { label: "WeChat / WeCom", id: "wechat_official" },
];

// `requiresHardware` doors bind to THIS agent's own gateway (a paired
// computer / VPS) — a "full account" login runs a real client process on that
// box, which a cloud-only agent has nowhere to run. When the agent has no
// gateway, the door renders disabled + "Hardware required" (never a pickable
// door that would fail once opened), so the customer understands up front.
type ChannelDoor = { key: string; label: string; body: string; real: boolean; requiresHardware?: boolean };

// Each channel shows only the connection MODES that are real, safe, and built
// today for that platform — never a door that fails, and never a second mode
// standing in for one that doesn't exist yet (contract rule #4). Telegram is
// the one channel with a genuine two-mode choice (Chatbot vs. Full account);
// every other channel has exactly one real path, so its door auto-selects
// with no picker step. "Full account" doors bind to THIS agent's own
// preferred_gateway_id (see ChannelsTab's `agentGatewayId`), never to a
// workspace-wide/Sage-routed session.
const CHANNEL_DOORS: Record<string, ChannelDoor[]> = {
  sage_telegram_hosted: [
    { key: "byo_bot", label: "Chatbot", body: "Agent replies as a separate bot — paste the token BotFather gave you. No control of your own account.", real: true },
    { key: "full_account", label: "Full account", body: "This agent's own Telegram number — phone, code, and 2FA if enabled — running on this agent's own gateway.", real: true, requiresHardware: true },
  ],
  slack: [
    { key: "oauth", label: "App", body: "Connect a Slack workspace — signed mentions and DMs route to your AI.", real: true },
  ],
  discord_bot: [
    { key: "byo_bot", label: "Bot", body: "Give this agent its own Discord bot — paste the token from Discord's developer portal. Discord's Terms forbid automating a real user account, so this is the only path.", real: true },
  ],
  whatsapp_personal: [
    { key: "full_account", label: "Full account", body: "This agent's own WhatsApp number — scan a QR code or use a pairing code — running on this agent's own gateway. There is no chatbot/business-API mode.", real: true, requiresHardware: true },
  ],
  signal_personal: [
    { key: "full_account", label: "Full account", body: "This agent's own Signal, via a signal-cli bridge running on hardware you control as this agent's gateway. Requires a real signal-cli install — there is no cloud path for Signal.", real: true, requiresHardware: true },
  ],
  imessage_personal: [
    { key: "full_account", label: "Full account", body: "This agent's own iMessage, via imsg — a small CLI that talks to Messages.app directly on a Mac running as this agent's gateway. Requires a real Mac — there is no cloud path for iMessage. Setup (installing imsg, checking Full Disk Access) happens right here, no terminal required.", real: true, requiresHardware: true },
  ],
  wechat_official: [
    { key: "app_credential_pair", label: "Official Account / WeCom", body: "This agent's own WeChat Official Account or WeChat Work (WeCom) bot — paste the AppID/AppSecret (or CorpID/CorpSecret/AgentId) from your own WeChat/WeCom admin console. Bidirectional: inbound messages route to this agent, replies send as this bot.", real: true },
  ],
};

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

function LocalBridgeChannelStatus({ channelKey, gatewayId }: { channelKey: string; gatewayId: string | null }) {
  const { items, loading } = useGatewayPersonalChannelSurfaces(gatewayId);

  if (!gatewayId) {
    return (
      <p className="fleet-channel-expand-hint">
        {LOCAL_BRIDGE_NO_GATEWAY_HINT[channelKey] || "This agent has no computer of its own yet — set one up on the Hardware tab first, then point it at this channel's local bridge."}
      </p>
    );
  }
  if (loading) {
    return <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />;
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
      const res = await fetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-channels/${channel}?agent_id=${encodeURIComponent(agentId)}`,
        {
          method: "POST",
          credentials: "include",
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify({ token: byoToken.trim() }),
        },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
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
      const res = await fetch(
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
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
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
      await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
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
      const res = await fetch(`/api/connections/${encodeURIComponent(id)}/setup/start`, {
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
        throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
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
      const res = await fetch(
        `/api/w/${encodeURIComponent(workspaceId)}/fleet/agent-channels/slack?agent_id=${encodeURIComponent(agentId)}`,
        {
          method: "POST",
          credentials: "include",
          headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
          body: JSON.stringify({ slack_channel_id: slackChannelId.trim() }),
        },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
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
  const doors = expanded ? CHANNEL_DOORS[expanded] || [] : [];
  // A lone real door needs no picker — it auto-activates. Otherwise the user's
  // click on a specific (real) door decides which one is active.
  const activeDoor = doors.length === 1 && doors[0].real ? doors[0] : doors.find((d) => d.key === selectedDoor) || null;

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
  const hasFullAccountDoor = doors.some((d) => d.key === "full_account" && d.real);
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
  // The doors picker's actual render list: collapsed to just the in-flight
  // door while pairingActive, otherwise the full set (unchanged behavior).
  const displayDoors = pairingActive && activeDoor ? [activeDoor] : doors;

  // Only the TRUE first load (no channels fetched yet) gets the full-tab
  // skeleton — refresh() (called after every connect-success, see
  // handleChannelsChanged above) also flips `loading` true/false, and
  // gating on `loading` alone replaced whatever the user just saw (a
  // "Saved"/"Connected" confirmation, an open banner) with this skeleton on
  // every single action.
  if (loading && channels.length === 0) {
    return <div className="fleet-activity-skeleton" aria-label="Loading channels"><div className="fleet-skeleton-bar" style={{ width: "80%" }} /></div>;
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

  return (
    <div>
      {/* Channels vs. Connectors reads as one undifferentiated "integrations"
          blob otherwise — this one-liner is the whole fix: it's how people
          reach the agent, not what the agent can use. */}
      <p className="fleet-tab-subtitle">Where people can message this agent</p>
      <div className="fleet-channel-grid">
        {CHANNEL_GRID_PLATFORMS.map((platform) => {
          const channel = byId.get(platform.id);
          const pill = channelStatePill(channel);
          const icon = CHANNEL_ICONS[platform.id];
          const isExpanded = expanded === platform.id;
          return (
            <button
              key={platform.id}
              type="button"
              className={`fleet-channel-card${isExpanded ? " fleet-channel-card--active" : ""}`}
              onClick={() => handleCardClick(platform, pill)}
              disabled={pill.tone === "locked"}
            >
              <span className="fleet-channel-card-icon">
                {icon ? <img src={icon} alt="" width={32} height={32} /> : platform.label.charAt(0)}
              </span>
              <span className="fleet-channel-card-label">{platform.label}</span>
              <span className={`fleet-channel-card-pill fleet-channel-card-pill--${pill.tone}`}>
                {pill.tone === "connected" ? <span className="fleet-channel-card-dot" /> : null}
                {pill.label}
              </span>
            </button>
          );
        })}
      </div>

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
              {/* While a full_account pairing is actively mid-flight (a
                   code/QR/password step with real typed input at stake —
                   see pairingActive/displayDoors above), the picker collapses
                   to JUST the active door, rendered the same inert way a
                   single-door channel already is below (no onClick). Tapping
                   an alternate-door button right now would unmount
                   PersonalChannelConnectPanel below and silently discard
                   whatever's been entered — this is what stops that. */}
              {displayDoors.length >= 1 && (
                <div className="fleet-wizard-options">
                  {displayDoors.map((door) => {
                    const isOnlyRealDoor = displayDoors.length === 1 && door.real;
                    // Which door is ALREADY connected, so the picker itself
                    // says so instead of making the founder click into a door
                    // just to discover it's the one he already paired (the
                    // reported bug: Telegram's "Full account" showed
                    // "Connected as Mansur阿龙" only once you opened it).
                    // Chatbot's connected state rides in on telegramBotConnected
                    // (see isChannelConnected's doc above — same "separate
                    // catalog item from the grid pill" shape); full_account's
                    // comes from the live pairing status polled just above,
                    // already scoped to whichever channel is on screen.
                    const doorConnected =
                      activePlatform.id === "sage_telegram_hosted" && door.key === "byo_bot" ? telegramBotConnected
                        : door.key === "full_account" ? fullAccountDoorConnected
                        : false;
                    const connectedBadge = doorConnected ? (
                      <span className="fleet-wizard-option-connected">
                        <span className="fleet-channel-card-dot" /> Connected
                      </span>
                    ) : null;
                    if (!door.real) {
                      return (
                        <button key={door.key} type="button" disabled className="fleet-wizard-option fleet-wizard-option--soon">
                          <span className="fleet-wizard-option-label">{door.label}</span>
                          <span className="fleet-wizard-option-body">{door.body}</span>
                          <span className="fleet-wizard-option-note"><Lock size={11} strokeWidth={2} /> Not available on this deployment yet</span>
                        </button>
                      );
                    }
                    // A full-account login runs a real client process on THIS
                    // agent's own gateway. No gateway (a cloud-only agent) → the
                    // door is inert + colorless, labeled "Hardware required", so
                    // the customer sees up front they must connect a computer —
                    // never a door that only fails once opened.
                    if (door.requiresHardware && !agentGatewayId && !doorConnected) {
                      return (
                        <button key={door.key} type="button" disabled className="fleet-wizard-option fleet-wizard-option--soon">
                          <span className="fleet-wizard-option-label">{door.label}</span>
                          <span className="fleet-wizard-option-body">{door.body}</span>
                          <span className="fleet-wizard-option-note"><Cpu size={11} strokeWidth={2} /> Hardware required — connect a computer for this agent first</span>
                        </button>
                      );
                    }
                    if (isOnlyRealDoor) {
                      return (
                        <div key={door.key} className="fleet-wizard-option is-selected" style={{ cursor: "default" }}>
                          <span className="fleet-wizard-option-label">{door.label}{connectedBadge}</span>
                          <span className="fleet-wizard-option-body">{door.body}</span>
                        </div>
                      );
                    }
                    return (
                      <button
                        key={door.key}
                        type="button"
                        className={`fleet-wizard-option${selectedDoor === door.key ? " is-selected" : ""}`}
                        onClick={() => setSelectedDoor(selectedDoor === door.key ? null : door.key)}
                      >
                        <span className="fleet-wizard-option-label">{door.label}{connectedBadge}</span>
                        <span className="fleet-wizard-option-body">{door.body}</span>
                      </button>
                    );
                  })}
                </div>
              )}

              {/* Telegram: BYO bot token */}
              {activePlatform.id === "sage_telegram_hosted" && activeDoor?.key === "byo_bot" && (
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
              {activePlatform.id === "discord_bot" && activeDoor?.key === "byo_bot" && (
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
              {activePlatform.id === "slack" && activeDoor && (
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
              {activePlatform.id === "sage_telegram_hosted" && activeDoor?.key === "full_account" && (
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
              {activePlatform.id === "whatsapp_personal" && activeDoor?.key === "full_account" && (
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
              {activePlatform.id === "signal_personal" && activeDoor?.key === "full_account" && (
                <div style={{ marginTop: 12 }}>
                  <LocalBridgeChannelStatus channelKey="signal_personal" gatewayId={agentGatewayId} />
                </div>
              )}

              {/* iMessage: unlike Signal/WeChat, setup genuinely happens in-app —
                   imsg runs on this agent's own gateway Mac, and the gateway's
                   layered probe (binary / rpc / Full Disk Access / private API)
                   is surfaced live with inline fixes and a Re-check button. The
                   one truly manual step is Full Disk Access, which macOS will
                   not let any process grant to itself — see
                   IMessageSetupPanel.tsx. */}
              {activePlatform.id === "imessage_personal" && activeDoor?.key === "full_account" && (
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
              {activePlatform.id === "wechat_official" && activeDoor?.key === "app_credential_pair" && (
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
    return (
      <div>
        {subtitle}
        <div className="fleet-activity-skeleton" aria-label="Loading connectors"><div className="fleet-skeleton-bar" style={{ width: "70%" }} /></div>
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
          body="This agent has no project — connectors are shared per project. Assign a project before connecting apps."
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
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { tool_toggles: { [toolId]: next } } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
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
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { mandate: { audience_tools: Array.from(nextGranted) } } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update customer access for this tool.");
    } finally {
      setMandateBusy(null);
    }
  }

  if (loading || connectorsLoading) {
    return <div className="fleet-activity-skeleton" aria-label="Loading tools"><div className="fleet-skeleton-bar" style={{ width: "60%" }} /></div>;
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
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { capability_config: { [capabilityId]: { mode, provider } } } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
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
        const res = await fetch(
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
        const res = await fetch(
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
    return <div className="fleet-activity-skeleton" aria-label="Loading capabilities"><div className="fleet-skeleton-bar" style={{ width: "60%" }} /></div>;
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

function resolveDisplayMode(config: Record<string, any>): ProviderMode {
  const mode = config.mode;
  if (mode === "byok_api") return "byok_api";
  if (mode === "cli_subscription") return "cli_subscription";
  if (mode === "local") return "local";
  return "platform_credits";
}

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

/** What `selectedModel` should start as for a given mode+provider+saved value
 *  — the saved value always wins; otherwise the provider's Recommended pick,
 *  or "" for a freeform provider (never fabricate a value the CLI/API
 *  wouldn't recognize) or a mode with no model concept (platform_credits). */
function seedSelectedModel(mode: ProviderMode, provider: string, savedModel: string): string {
  if (savedModel) return savedModel;
  if (mode !== "byok_api" && mode !== "cli_subscription" && mode !== "local") return "";
  const effectiveProvider = provider || (mode === "local" ? "ollama" : "");
  if (!effectiveProvider || FREEFORM_MODEL_PROVIDERS.has(effectiveProvider)) return "";
  return defaultModelForProvider(effectiveProvider);
}

// Phase 7B: preset / hardware-lock / context-policy / today's cost, shown at
// the top of the Model tab so the agent's governance + spend are visible.
function AgentModelSummary({ workspaceId, agentId, agent }: { workspaceId: string; agentId: string; agent: FleetAgent | null }) {
  const [costPeriod, setCostPeriod] = useState<CostPeriod>("day");
  const [cost, setCost] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/usage?scope=agent&id=${encodeURIComponent(agentId)}&period=${costPeriod}`, { credentials: "include" })
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
      const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
        method: "PATCH",
        credentials: "include",
        headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
        body: JSON.stringify({ patch: { context_policy: { max_context_tokens: parsed, on_context_full: onFull } } }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.ok === false) throw new Error(data?.error || data?.detail || `HTTP ${res.status}`);
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

/** A pending (unsaved) edit to an agent's model_config — the shape both the
 *  Model tab's own editor and the Properties panel's compact picker collect
 *  locally before handing off to the single shared save path below. */
type ModelConfigDraft = {
  mode: ProviderMode;
  provider: string;
  selectedModel: string;
  apiKey: string;
  gatewayBinding: string;
  reasoningEffort: string;
};

/** The ONE save path for an agent's model_config — used by both the Model
 *  tab's own editor (ModelTab.save(), below) and the Properties panel's
 *  compact picker (AgentModelPickerRow, above), so the two surfaces can
 *  never independently drift the way resolveAgentModelSummary's doc
 *  comment already warns about for the read side. Validates the draft
 *  (throws a user-facing Error on failure — callers own their own
 *  try/catch + saving/error state), writes a new BYOK vault credential
 *  first when a fresh API key is entered exactly like the previous
 *  ModelTab-only version did, then PATCHes model_config. */
async function saveAgentModelConfig(
  workspaceId: string,
  agentId: string,
  currentConfig: Record<string, any>,
  agentLabel: string | undefined,
  draft: ModelConfigDraft,
): Promise<void> {
  const { mode, provider, selectedModel, apiKey, gatewayBinding, reasoningEffort } = draft;
  if (COMING_SOON_MODES.has(mode)) {
    throw new Error(`${COMING_SOON_NOTE}. This option can’t be saved yet.`);
  }
  if (mode === "local" && !gatewayBinding.trim()) {
    throw new Error("Pick a computer (with Ollama) to run this agent’s local model.");
  }
  if (mode === "cli_subscription" && !gatewayBinding.trim()) {
    throw new Error("Pick a computer to run this agent’s subscription CLI.");
  }
  // A blank key is only safe to save when THIS provider already has a
  // credential in the vault — i.e. byok_api was already persisted for this
  // exact provider. Otherwise there is no known credential, and patching
  // mode=byok_api anyway would silently persist a broken config.
  const hasExistingCredentialForProvider = currentConfig.mode === "byok_api" && currentConfig.provider === provider;
  if (mode === "byok_api" && !apiKey.trim() && !hasExistingCredentialForProvider) {
    throw new Error("Enter your API key for this provider — none is saved yet.");
  }
  const reasoningEffortSupported = REASONING_EFFORT_SUPPORTED_MODES.has(mode);
  const canSaveReasoningEffort = reasoningEffortSupported || mode === "cli_subscription";

  async function patchModelConfig(): Promise<void> {
    const patch: Record<string, any> = { mode };
    if (mode === "byok_api" || mode === "cli_subscription" || mode === "local") {
      patch.provider = provider;
    }
    // BUG FIX (2026-07-30): this used to read `mode === "byok_api" ||
    // mode === "local"` — cli_subscription was silently excluded, so even
    // when the picker above collected a model choice, Save never included it
    // in the PATCH body. Confirmed live: agent "Compass" (production,
    // ws_c4601e47c95a) has mode: cli_subscription, a real gateway_binding,
    // and no `model` key at all — this is why "I can't choose a model" was
    // reported. The Gateway side (cli-runner.ts buildInvocation) has always
    // forwarded model_config.model into each CLI's real --model flag; this
    // was purely a frontend gap between "collected" and "saved".
    if ((mode === "byok_api" || mode === "cli_subscription" || mode === "local") && selectedModel.trim()) {
      patch.model = selectedModel.trim();
    }
    // BYO-brain Phase 0: forward-wire which box + runtime.
    if (mode === "cli_subscription" || mode === "local") {
      if (gatewayBinding) patch.gateway_binding = gatewayBinding;
      const rt = runtimeForProvider(provider);
      if (rt) patch.runtime = rt;
    }
    // Only for the modes that actually consume it at turn time — this patch
    // REPLACES model_config wholesale (fleet_tools.py's fleet_configure_agent
    // does `meta["model_config"] = dict(patch)`, not a merge), so switching
    // to local and saving correctly drops any previously-set
    // reasoning_effort instead of leaving a stale, inert value behind.
    if (canSaveReasoningEffort && reasoningEffort) {
      patch.reasoning_effort = reasoningEffort;
    }
    const res = await fetch(`/api/w/${encodeURIComponent(workspaceId)}/fleet/agents/${encodeURIComponent(agentId)}`, {
      method: "PATCH",
      credentials: "include",
      headers: buildCookieAuthHeaders("PATCH", { "Content-Type": "application/json" }),
      body: JSON.stringify({ patch: { model_config: patch } }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${res.status}`);
  }

  if (mode === "byok_api") {
    if (!apiKey.trim()) {
      // Reusing existing vault key (hasExistingCredentialForProvider
      // guaranteed true above) — only patch config.
      await patchModelConfig();
    } else {
      // See the identical comment in FleetCreateAgentWizard.tsx's
      // submitBrain(): /credentials/vault stores + validates the secret
      // against the real provider adapter and returns a credential_id;
      // /providers/profiles is the separate routing layer that makes it
      // discoverable at turn time. /api/connectors/vault (used here
      // previously) is the unrelated third-party-app connector vault and
      // 400s "Unsupported connector" for every LLM provider.
      const label = `${providerLabel(provider)} — ${agentLabel || "agent"}`;
      const credRes = await fetch("/api/credentials/vault", {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({
          workspace_id: workspaceId,
          provider,
          label,
          mode: "byok",
          credentials: { api_key: apiKey.trim() },
        }),
      });
      const credData = await credRes.json().catch(() => ({}));
      if (!credRes.ok) throw new Error(credData?.detail || credData?.error || `HTTP ${credRes.status}`);

      const profileRes = await fetch("/api/providers/profiles", {
        method: "POST",
        credentials: "include",
        headers: buildCookieAuthHeaders("POST", { "Content-Type": "application/json" }),
        body: JSON.stringify({
          workspace_id: workspaceId,
          provider,
          label,
          credential_id: credData?.id,
          enabled: true,
        }),
      });
      const profileData = await profileRes.json().catch(() => ({}));
      if (!profileRes.ok) throw new Error(profileData?.detail || profileData?.error || `HTTP ${profileRes.status}`);
      await patchModelConfig();
    }
  } else {
    await patchModelConfig();
  }
}

/** Properties panel's compact Model picker — clicking the "Model" row opens
 *  a small popover (same anchored-popover pattern as FleetToolbar's
 *  filter/sort popover: .fleet-toolbar-popover, click-outside + Escape to
 *  dismiss) that lets the owner pick provider + model from the FULL
 *  catalogue (all BYOK_PROVIDERS, including xai/Grok) without leaving the
 *  panel, or switch mode entirely (platform credits / own key / own
 *  subscription / local). Saves through the exact same saveAgentModelConfig
 *  path as the Model tab — no separate PATCH logic here. Kept intentionally
 *  smaller than the full ModelTab editor (no "Current state" block, no
 *  capability-preset/context-policy section) since this is a quick-switch
 *  surface, not a replacement for the Model tab. */
function AgentModelPickerRow({
  workspaceId, agentId, agent, resolvedModel, onSaved,
}: {
  workspaceId: string;
  agentId: string;
  agent: FleetAgent | null;
  /** Pre-formatted "{provider} · {model}" summary — same value already
   *  shown elsewhere, so the closed-state trigger never drifts from it. */
  resolvedModel: string;
  onSaved?: () => void;
}) {
  const config = agent?.model_config || {};
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<ProviderMode>(resolveDisplayMode(config));
  const [provider, setProvider] = useState<string>(config.provider || "");
  const [selectedModel, setSelectedModel] = useState<string>(() =>
    seedSelectedModel(resolveDisplayMode(config), config.provider || "", config.model || ""),
  );
  const [apiKey, setApiKey] = useState("");
  const [gatewayBinding, setGatewayBinding] = useState<string>(config.gateway_binding || "");
  const [reasoningEffort, setReasoningEffort] = useState<string>(config.reasoning_effort || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { gateways: cliGateways } = useWorkspaceGateways(workspaceId);
  const cliRuntime = normalizeCliRuntime(runtimeForProvider(provider));
  const ref = useRef<HTMLDivElement | null>(null);

  // Re-seed the draft from the agent's real current config every time the
  // popover opens — mirrors ModelTab's own hydration guard in spirit, but
  // simpler: this popover fully unmounts its edits on close (no "unsaved
  // draft survives a close" concern), so a fresh open is always the source
  // of truth rather than whatever was left over from a previous open.
  useEffect(() => {
    if (!open) return;
    const fresh = agent?.model_config || {};
    const freshMode = resolveDisplayMode(fresh);
    setMode(freshMode);
    setProvider(fresh.provider || "");
    setSelectedModel(seedSelectedModel(freshMode, fresh.provider || "", fresh.model || ""));
    setApiKey("");
    setGatewayBinding(fresh.gateway_binding || "");
    setReasoningEffort(fresh.reasoning_effort || "");
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Click-outside + Escape to dismiss — identical pattern to
  // FleetToolbar.tsx's own popover so this behaves exactly like every other
  // anchored popover in Fleet.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function onModeChange(next: ProviderMode) {
    setMode(next);
    setError(null);
    if (next === "byok_api") {
      const p = provider || "anthropic";
      setProvider(p);
      setSelectedModel(seedSelectedModel(next, p, ""));
    } else if (next === "cli_subscription") {
      const p = provider || "claude_code_cli";
      setProvider(p);
      setSelectedModel(seedSelectedModel(next, p, ""));
    } else if (next === "local") {
      const p = provider || "ollama";
      setProvider(p);
      setSelectedModel(seedSelectedModel(next, p, ""));
    }
  }

  function onProviderChange(next: string) {
    setProvider(next);
    setError(null);
    if (mode === "byok_api" || mode === "cli_subscription") {
      setSelectedModel(FREEFORM_MODEL_PROVIDERS.has(next) ? "" : defaultModelForProvider(next));
    } else if (mode === "local") {
      setSelectedModel(defaultModelForProvider(next || "ollama"));
    }
  }

  const reasoningEffortSupported = REASONING_EFFORT_SUPPORTED_MODES.has(mode);
  const localNeedsBox = mode === "local" && !gatewayBinding.trim();
  // Brain-bound modes (cli_subscription / local) can only run on a paired
  // computer — there is no machine in "cloud" for a subscription CLI or Ollama
  // to run on. So don't offer them when the workspace has zero paired boxes
  // (the founder's rule: cloud never offers "Your subscription"). The agent's
  // currently-saved mode is always kept in the list so the <select> can render
  // its own value even if the hardware backing it later went away.
  const brainModesAvailable = cliGateways.length > 0;
  const modeOptions: ProviderMode[] = ["platform_credits", "byok_api"];
  if (brainModesAvailable) modeOptions.push("cli_subscription", "local");
  if (!modeOptions.includes(mode)) modeOptions.push(mode);
  const cliSubscriptionNeedsBox = mode === "cli_subscription" && !gatewayBinding.trim();

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await saveAgentModelConfig(workspaceId, agentId, config, agent?.label, {
        mode, provider, selectedModel, apiKey, gatewayBinding, reasoningEffort,
      });
      setOpen(false);
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fleet-model-picker" ref={ref}>
      <button
        type="button"
        className="fleet-model-picker-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Change this agent's model"
      >
        <span>{resolvedModel}</span>
        <ChevronDown size={13} strokeWidth={2} />
      </button>
      {open && (
        <div className="fleet-toolbar-popover fleet-model-picker-popover" role="dialog" aria-label="Change model">
          <div className="fleet-toolbar-popover-group">
            <div className="fleet-toolbar-popover-label">Payment</div>
            <select
              className="fleet-wizard-input"
              value={mode}
              onChange={(e) => onModeChange(e.currentTarget.value as ProviderMode)}
            >
              {modeOptions.map((m) => (
                <option key={m} value={m}>{MODE_LABELS[m]}</option>
              ))}
            </select>
            {!brainModesAvailable && (
              <p className="fleet-channel-expand-hint" style={{ margin: "4px 0 0" }}>
                Running on your own Claude/Codex subscription needs a paired computer — add one under Hardware.
              </p>
            )}
          </div>

          {mode === "platform_credits" && (
            <p className="fleet-channel-expand-hint" style={{ margin: 0 }}>
              DeepSeek, on the platform. Empyralis pays — nothing to pick here.
            </p>
          )}

          {mode === "byok_api" && (
            <div className="fleet-toolbar-popover-group">
              <div className="fleet-toolbar-popover-label">Provider</div>
              <select
                className="fleet-wizard-input"
                value={provider}
                onChange={(e) => onProviderChange(e.currentTarget.value)}
              >
                {BYOK_PROVIDERS.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
              {FREEFORM_MODEL_PROVIDERS.has(provider) ? (
                <input
                  className="fleet-wizard-input"
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.currentTarget.value)}
                  placeholder={provider === "azure_openai" ? "e.g. my-gpt4-deployment" : "e.g. llama-3-70b"}
                />
              ) : (
                <select
                  className="fleet-wizard-input"
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.currentTarget.value)}
                >
                  {modelsForProvider(provider).map((m) => <option key={m} value={m}>{modelOptionLabel(provider, m)}</option>)}
                </select>
              )}
              <ModelSizeWarning provider={provider} model={selectedModel} />
              <input
                className="fleet-wizard-input"
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(e) => setApiKey(e.currentTarget.value)}
                placeholder={
                  config.mode === "byok_api" && config.provider === provider
                    ? "API key (leave blank to keep existing)"
                    : "API key — required for this provider"
                }
              />
            </div>
          )}

          {mode === "cli_subscription" && (
            <div className="fleet-toolbar-popover-group">
              <div className="fleet-toolbar-popover-label">Subscription</div>
              <select
                className="fleet-wizard-input"
                value={provider}
                onChange={(e) => onProviderChange(e.currentTarget.value)}
              >
                {SUBSCRIPTION_PROVIDERS.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
              {FREEFORM_MODEL_PROVIDERS.has(provider) ? (
                <input
                  className="fleet-wizard-input"
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.currentTarget.value)}
                  placeholder="Model id (optional — blank uses the CLI's own default)"
                />
              ) : (
                <select
                  className="fleet-wizard-input"
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.currentTarget.value)}
                >
                  {modelsForProvider(provider).map((m) => <option key={m} value={m}>{modelOptionLabel(provider, m)}</option>)}
                </select>
              )}
              <ModelSizeWarning provider={provider} model={selectedModel} />
              <p className="fleet-channel-expand-hint" style={{ margin: 0 }}>{cliSubscriptionHint(cliGateways)}</p>
              <GatewayBoxPicker
                workspaceId={workspaceId}
                value={gatewayBinding}
                onChange={setGatewayBinding}
                requireRuntime={cliRuntime}
              />
            </div>
          )}

          {mode === "local" && (
            <div className="fleet-toolbar-popover-group">
              <div className="fleet-toolbar-popover-label">Ollama model</div>
              <select
                className="fleet-wizard-input"
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.currentTarget.value)}
              >
                {modelsForProvider("ollama").map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
              <GatewayBoxPicker
                workspaceId={workspaceId}
                value={gatewayBinding}
                onChange={setGatewayBinding}
                requireLocalModel
              />
            </div>
          )}

          {reasoningEffortSupported && (
            <div className="fleet-toolbar-popover-group">
              <div className="fleet-toolbar-popover-label">Reasoning effort</div>
              <select
                className="fleet-wizard-input"
                value={reasoningEffort}
                onChange={(e) => setReasoningEffort(e.currentTarget.value)}
              >
                {REASONING_EFFORT_OPTIONS.map((o) => (
                  <option key={o.value || "unset"} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          )}

          {error && <p className="fleet-channel-expand-error" style={{ margin: 0 }}>{error}</p>}

          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button type="button" className="fleet-btn" onClick={() => setOpen(false)} disabled={saving}>
              Cancel
            </button>
            <button
              type="button"
              className="fleet-btn fleet-btn--accent"
              onClick={handleSave}
              disabled={saving || localNeedsBox || cliSubscriptionNeedsBox}
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
          <p className="fleet-channel-expand-hint" style={{ margin: 0 }}>
            Full editor, including context policy, lives on the{" "}
            <strong>Model</strong> tab.
          </p>
        </div>
      )}
    </div>
  );
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

      {/* platform_credits has no provider/model fields (fixed to the
          platform default) — reasoning effort is the one thing left to
          configure here, so it gets its own expand block instead of living
          bare under the mode buttons. */}
      {mode === "platform_credits" && (
        <div className="fleet-channel-expand">
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
          ) : (
            <>
              <label className="fleet-wizard-label">Model</label>
              <select className="fleet-wizard-input" value={selectedModel} onChange={(e) => { setSelectedModel(e.currentTarget.value); setSaved(false); }}>
                {modelsForProvider(provider).map((m) => <option key={m} value={m}>{modelOptionLabel(provider, m)}</option>)}
              </select>
              <ModelSizeWarning provider={provider} model={selectedModel} />
            </>
          )}
          {renderCliReasoningEffortPicker()}
          <div className="fleet-detail-section-title" style={{ marginTop: 16 }}>Brain runs on</div>
          <GatewayBoxPicker
            workspaceId={workspaceId}
            value={gatewayBinding}
            onChange={(id) => { setGatewayBinding(id); setSaved(false); }}
            requireRuntime={cliRuntime}
          />
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

