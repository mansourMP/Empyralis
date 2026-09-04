import type { FleetAgent, StoppedState } from "./fleet-data";

/** This product has one language, English, everywhere — visitor-OS-locale
 *  date/number rendering was never a decision. Every toLocale*() call in the
 *  fleet UI goes through these three so nothing ever silently renders in
 *  whatever locale the visitor's machine happens to report. */
const LOCALE = "en-US";

export function formatDate(value: string | number | Date, opts?: Intl.DateTimeFormatOptions): string {
  const d = value instanceof Date ? value : new Date(value);
  return d.toLocaleDateString(LOCALE, opts);
}

export function formatDateTime(value: string | number | Date, opts?: Intl.DateTimeFormatOptions): string {
  const d = value instanceof Date ? value : new Date(value);
  return d.toLocaleString(LOCALE, opts);
}

export function formatTime(value: string | number | Date, opts?: Intl.DateTimeFormatOptions): string {
  const d = value instanceof Date ? value : new Date(value);
  return d.toLocaleTimeString(LOCALE, opts);
}

/** For a date-ONLY field — currently just task.due_at — never for a real
 *  instant. `_coerce_due_at` (server_modules/project_tasks_service.py)
 *  parses the bare "YYYY-MM-DD" a date input sends and stamps it midnight
 *  UTC; it has no meaningful time-of-day. Formatting that through
 *  formatDate/formatDateTime with no `timeZone` override — correct for
 *  every real instant on the fleet UI (created_at, updated_at, comment
 *  timestamps) — reinterprets midnight UTC in the viewer's local timezone,
 *  so anyone west of UTC sees the calendar day roll back by one: a date
 *  picked as "Aug 15" stores as 2026-08-15T00:00:00Z and renders as
 *  "Aug 14". Pinning `timeZone: "UTC"` here formats the SAME calendar date
 *  the value was stamped with, so it always agrees with the edit input
 *  (which reads the date via a raw ISO slice, not a localized Date) no
 *  matter which timezone the viewer is in. */
export function formatDueDate(value: string | number | Date, opts?: Intl.DateTimeFormatOptions): string {
  const d = value instanceof Date ? value : new Date(value);
  return d.toLocaleDateString(LOCALE, { ...opts, timeZone: "UTC" });
}

export function formatNumber(value: number): string {
  return value.toLocaleString(LOCALE);
}

/** One row of the full usage/cost attribution matrix — GET /fleet/usage's
 *  `matrix` field (server_modules/usage_events_repository.py:summarize_usage).
 *  Every row is a real (agent, provider, model, source) combination that was
 *  actually billed, with real summed input/output tokens and the real
 *  dollar cost pricing_registry_service computed for those tokens at that
 *  model's real per-1M rate — never a flat/blended estimate. `payer` is
 *  already canonicalized to the same four-source taxonomy the credit ledger
 *  uses (credit_ledger_contract.LEDGER_PAYERS); `pricing_known` is false
 *  when the source has no per-token price to charge against (a flat CLI
 *  subscription, a free local model) — that must read as "not priced", not
 *  a fabricated $0.00. */
export type UsageMatrixRow = {
  agent_install_id: string | null;
  provider: string | null;
  model: string | null;
  mode: string | null;
  payer: string | null;
  events?: number;
  tokens_in: number;
  tokens_out: number;
  // Cache token dimensions (usage_events.tokens_cache_creation/
  // tokens_cache_read — Anthropic's cache_creation_input_tokens/
  // cache_read_input_tokens, aka ModelUsage.cacheCreationInputTokens/
  // cacheReadInputTokens). Optional: rows recorded before this column
  // existed, or by a non-SDK engine, simply omit them — never fabricated
  // as 0 when genuinely unknown.
  tokens_cache_creation?: number;
  tokens_cache_read?: number;
  total_tokens?: number;
  usd_cost: number;
  pricing_known: boolean;
};

const USAGE_PAYER_LABEL: Record<string, string> = {
  platform_credits: "Platform credits",
  BYOK: "Your API key",
  subscription_passthrough: "Your subscription",
  local: "Self-hosted",
  unknown: "Unknown",
};

/** Human label for a usage-matrix row's canonicalized `payer` — the "who
 *  actually paid for this" column shown next to every model in the cost
 *  transparency matrix (Billing page + agent Properties panel). */
export function usagePayerLabel(payer: string | null | undefined): string {
  return USAGE_PAYER_LABEL[payer || "unknown"] || (payer || "Unknown");
}

/** "working" is the agent-lifecycle tone (deriveStatus() below never
 *  produces anything else for a running task). "online" is kept only for
 *  the Hardware page's own device-reachability chip — a different domain
 *  (is this paired computer reachable, not what is this agent doing) that
 *  happens to share the StatusChip/StatusDot components; deriveStatus()
 *  itself never returns it. */
export type AgentStatusTone = "working" | "online" | "ready" | "offline" | "unknown" | "error" | "stopped" | "degraded";

export type AgentSummary = {
  id: string;
  name: string;
  role: string;
  /** "master" for the workspace assistant, "specialist" for everything else.
   *  Optional because older callers construct AgentSummary without it; the
   *  structural signal isSageAgent needs, since `role` is measured to be
   *  "specialist" even on the master install. */
  agentKind?: string;
  preset: "customer_facing" | "internal_assistant" | "operator";
  runtimeTarget: string;
  hardwareStatus: string;
  // Real placement source (see resolveHardwarePlacement in gateway-box-picker.tsx)
  // — runtimeTarget/hardwareStatus above are display-legacy and must not be
  // used for "where does this agent run", only for online/offline status.
  hardwareAccess: string;
  preferredGatewayId: string;
  // Brain placement source for cli_subscription/local agents — see
  // resolveHardwarePlacement in gateway-box-picker.tsx. Threaded through
  // here so the list card reads the same Placement a given agent's own
  // detail page does, not just hardwareAccess/preferredGatewayId.
  modelConfig: Record<string, any> | null;
  lastActivity: string | null;
  /** Short verb ("Configured", "Created", ...) describing lastActivity —
   *  the same event fleet_tools' activity feed names. Card face pairs it
   *  with a relative time (timeAgo(lastActivity)) rather than rendering
   *  either alone. */
  activityPreview: string;
  tint: TintKey;
  stopped?: StoppedState;
};

export type TintKey = "blue" | "purple" | "amber" | "teal" | "coral" | "rose" | "sky" | "lime";

/* Identity tint GLYPH colours, as literals — the one thing that still has to
   be a real hex rather than a token: these are handed to chart and sparkline
   code (billing/page.tsx, fleet-sparkline.tsx) and painted onto the picker
   swatches, none of which can resolve a CSS var().

   The TILE surface is NOT here any more. It used to be a `bg` field holding a
   16%-alpha DARK base, which composited over the dark card to a near-grey —
   chroma spread of 13-20 out of 255 — and had no light-theme variant at all.
   That pair now lives in theme-tokens.css as --tint-<key>-bg/-fg, per theme,
   and .fleet-tile resolves it from data-tint. Keep it that way: a literal
   here can never follow the theme, which is exactly how light theme ended up
   painting a bright glyph on a pale wash.

   Values below are the DARK-theme glyph colours, so one colour identifies a
   project across its tile, its picker swatch and its chart series. All eight
   are light enough for the dark checkmark the picker draws over them.

   The "purple" key renders GREEN, deliberately: violet is the reserved
   accent and no identity tint may claim it. The key string is a data
   contract with PROJECT_TINTS in server_modules/projects_repository.py, so
   renaming it would desync every already-assigned project without a
   migration — TINT_LABELS below is what stops that lie reaching a person. */
export const TINTS: Record<TintKey, { fg: string }> = {
  blue: { fg: "#90c5ff" },
  purple: { fg: "#7fdd95" },
  amber: { fg: "#f7b755" },
  teal: { fg: "#34e0cf" },
  coral: { fg: "#ffa778" },
  rose: { fg: "#ff9ea8" },
  sky: { fg: "#49d6ff" },
  lime: { fg: "#b0d36e" },
};

/* What a person is actually told a swatch is called. The picker put the raw
   key in `title` and `aria-label`, so a green swatch announced itself as
   "purple" to every screen reader and every hover. The key is a backend
   contract and cannot move; the label can. */
export const TINT_LABELS: Record<TintKey, string> = {
  blue: "Blue",
  purple: "Green",
  amber: "Amber",
  teal: "Teal",
  coral: "Coral",
  rose: "Rose",
  sky: "Sky",
  lime: "Lime",
};

const TINT_ORDER: TintKey[] = ["blue", "teal", "amber", "coral", "purple"];

/** Spread-out identity tint purely from a list position — used where there's
 *  no FleetAgent record to key off (e.g. the cost-by-agent panel pips). */
export function tintKeyForIndex(index: number): TintKey {
  return TINT_ORDER[((index % TINT_ORDER.length) + TINT_ORDER.length) % TINT_ORDER.length];
}

export function tintForAgent(agent: FleetAgent, index: number): TintKey {
  const r = (agent.role || "").toLowerCase();
  if (r === "customer_facing") return "teal";
  // Stable, spread-out tints for everything else.
  return TINT_ORDER[index % TINT_ORDER.length];
}

function presetForRole(role: string): AgentSummary["preset"] {
  const r = (role || "").toLowerCase();
  if (r === "sage" || r === "operator") return "operator";
  if (r === "customer_facing") return "customer_facing";
  return "internal_assistant";
}

export function toAgentSummary(agent: FleetAgent, index: number): AgentSummary {
  return {
    id: agent.agent_id,
    name: agent.label || "Unnamed agent",
    role: agent.role,
    agentKind: agent.agent_kind,
    // purpose_preset is set at creation time by the create-agent wizard
    // (step 2) and returned by fleet_list_agents. Older agents created
    // before that field existed fall back to a role-based guess.
    preset: agent.purpose_preset || presetForRole(agent.role),
    runtimeTarget: agent.runtime_target || "unknown",
    hardwareStatus: agent.hardware_status || "unknown",
    hardwareAccess: agent.hardware_access || "none",
    preferredGatewayId: agent.preferred_gateway_id || "",
    modelConfig: agent.model_config || null,
    lastActivity: agent.last_activity || null,
    activityPreview: agent.activity_preview || "",
    tint: tintForAgent(agent, index),
    stopped: agent.stopped,
  };
}

/** The workspace assistant — the one install every workspace gets, surfaced
 *  to customers as "Ask AI".
 *
 *  KEYED ON `agent_kind === "master"` FIRST, and that ordering is
 *  load-bearing rather than stylistic. The two clauses under it are LEGACY
 *  FALLBACKS for rows that predate `agent_kind` being emitted — and the
 *  name-substring clause used to be the ONLY one that matched anything.
 *  Measured live 2026-08-21 and recorded on FleetAgent.agent_kind's own
 *  declaration: the workspace operator comes back as `role: "specialist"`,
 *  `agent_kind: "master"`, so NEITHER role clause fires on real data.
 *
 *  That made the assistant identifiable only by its stored label containing
 *  "sage" — exactly the stale-string-matching failure CLAUDE.md documents.
 *  Returning null here does not degrade gracefully: it removes the Ask AI
 *  console entirely (SageLauncher returns null without an agent), stops
 *  excluding the assistant from PrimaryRail/Projects/Agents/Inbox, and
 *  breaks agent-count-shape's contract that it never counts. So the
 *  structural signal goes first and the legacy clauses stay, which is what
 *  makes renaming the assistant safe. */
export function isSageAgent(agent: AgentSummary): boolean {
  const r = agent.role.toLowerCase();
  return (
    (agent.agentKind || "").trim().toLowerCase() === "master" ||
    r === "sage" ||
    r === "operator" ||
    agent.name.toLowerCase().includes("sage")
  );
}

/** The customer-facing name of the workspace assistant. It is a plain
 *  assistant inside the platform, not a persona, and this is the only name
 *  it has. */
export const WORKSPACE_ASSISTANT_LABEL = "Ask AI";

/** What to PRINT for an agent — never `agent.label` directly.
 *
 *  The master install's stored label on every workspace created before
 *  2026-08-28 is literally "Sage", the persona name the founder removed from
 *  the product. That column is DATA (rewriting it across live rows is a
 *  migration, not a rename), so the fix is at the render seam: the assistant
 *  prints as "Ask AI" wherever its row appears, whatever the row says.
 *
 *  Every surface that shows an agent name should call this. The ones that
 *  motivated it are Billing/Usage — a live, linked page whose per-agent spend
 *  legend and attribution matrix both render the raw field, so the removed
 *  name was on screen for any workspace whose assistant had spent anything. */
export function agentDisplayLabel(
  agent: Pick<FleetAgent, "label" | "role" | "agent_kind"> | null | undefined,
): string {
  if (!agent) return "Unnamed agent";
  const kind = (agent.agent_kind || "").trim().toLowerCase();
  const role = (agent.role || "").trim().toLowerCase();
  const label = (agent.label || "").trim();
  // Same discriminator ladder as findSageAgent, and for the same reason:
  // agent_kind is the structural fact, the rest are legacy fallbacks.
  if (kind === "master" || role === "sage" || role === "operator") {
    return WORKSPACE_ASSISTANT_LABEL;
  }
  if (label.toLowerCase().includes("sage")) return WORKSPACE_ASSISTANT_LABEL;
  return label || "Unnamed agent";
}

/** Same match as isSageAgent, over the raw FleetAgent list — used anywhere
 *  that needs the actual agent record (id, project_id, …) rather than the
 *  display-only AgentSummary. */
export function findSageAgent(agents: FleetAgent[]): FleetAgent | null {
  const kind = (a: FleetAgent) => (a.agent_kind || "").trim().toLowerCase();
  const r = (a: FleetAgent) => (a.role || "").toLowerCase();
  return (
    agents.find((a) => kind(a) === "master") ||
    agents.find((a) => r(a) === "sage" || r(a) === "operator") ||
    agents.find((a) => (a.label || "").toLowerCase().includes("sage")) ||
    null
  );
}

/** Status tone + label — the ONE status vocabulary (contract), used by the
 *  Agents list, Project rows, the Overview config row, the Properties panel,
 *  and the Now strip alike so an agent never reads differently in two
 *  places. Exactly five labels exist — "Active" and "Idle" are not among
 *  them:
 *  Ready (calm, hardware-reachable, not currently executing — a fresh
 *  agent's honest first state AND a healthy agent between tasks; there is
 *  no separate "has run before" label) · Working (currently executing) ·
 *  Stopped (owner-initiated, distinct from Offline — the agent isn't down,
 *  it's deliberately paused) · Offline (hardware unreachable) ·
 *  Error.
 *  `stopped` wins over every other signal: an agent that's hardware-online
 *  but owner-stopped must still read Stopped everywhere. `working`
 *  (pass `Boolean(agent.current_run_id)`) is what separates Working from
 *  Ready — it's "is it executing right now", not "has it ever done
 *  anything" — so a fresh agent and a healthy idle veteran both honestly
 *  read Ready. */
export function deriveStatus(
  hardwareStatus: string,
  stopped?: boolean,
  working?: boolean,
): { tone: AgentStatusTone; label: string } {
  if (stopped) return { tone: "stopped", label: "Stopped" };
  if (hardwareStatus === "online") {
    return working ? { tone: "working", label: "Working" } : { tone: "ready", label: "Ready" };
  }
  if (hardwareStatus === "offline") return { tone: "offline", label: "Offline" };
  if (hardwareStatus === "error") return { tone: "error", label: "Error" };
  return { tone: "unknown", label: "Not deployed" };
}

export function statusClass(tone: AgentStatusTone): string {
  return tone === "working" ? "is-working" : tone === "ready" ? "is-ready" : tone === "offline" ? "is-offline" : tone === "stopped" ? "is-stopped" : tone === "error" ? "is-error" : tone === "degraded" ? "is-degraded" : "";
}

/** Compact relative time for list rows ("2h ago", "3d ago"). "—" when unknown. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diffMs = Date.now() - then;
  if (diffMs < 0) return "just now";
  const mins = Math.floor(diffMs / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return formatDate(iso, { month: "short", day: "numeric" });
}

/** The breadcrumb path's trailing count ("Agents · 4", "Projects · 1
 *  project", "General · 3 agents") — U3-E moved this off its own toolbar
 *  row onto the breadcrumb line. The unit word is dropped only when it
 *  would exactly repeat the crumb label sitting right next to it ("Agents ·
 *  4", not the redundant "Agents · 4 agents"); everywhere else — including
 *  the grammatically-needed singular at count === 1 — it's spelled out. */
export function breadcrumbCount(count: number, singular: string, plural: string, lastCrumbLabel: string): string {
  const unit = count === 1 ? singular : plural;
  if (unit.toLowerCase() === lastCrumbLabel.trim().toLowerCase()) return String(count);
  return `${count} ${unit}`;
}
