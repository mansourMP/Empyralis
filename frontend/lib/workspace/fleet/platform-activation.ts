/**
 * MAN-149 ("Activation, not acquisition — signed-up customers exist and are
 * not using the platform") — the pure rules behind the operator page at
 * `/w/{workspaceId}/operator/activation`, which answers one question: is
 * anyone actually using Empyralis. Backed by
 * `GET /api/internal/platform-activation` (server_modules/routes_health.py,
 * `platform_activation_service.build_platform_activation_snapshot`).
 *
 * THE NUMBER THAT MATTERS. The founder had to SSH into production Postgres
 * to learn there are 105 signed-up users and 5 documents across the whole
 * platform. "How many people signed up" and "how many people have ever
 * DONE anything" are different facts, and a dashboard that shows only the
 * first is the acquisition report this page replaces, not the activation
 * one it is for. `platformActivationSummaryStats` is deliberately the
 * SMALL, activation-shaped set (signed-up users, users who ever created a
 * task or document, workspaces with more than one member, agents that have
 * actually run) — the raw totals (workspaces/agents/projects/tasks/
 * documents, agents that have NEVER run, signup counts) are
 * `platformActivationDetailStats`, secondary by design (CLAUDE.md: "the
 * summary before the detail"). Every `PlatformActivationStat`'s
 * `isActivation` flag is the one signal the page's own styling reads to
 * draw the "signed up" vs "actually did something" distinction the task
 * asked for — never inferred twice from two independently-drifting copies
 * of the same judgement.
 *
 * OUTCOME HONESTY, the same discipline work-ledger.ts's own
 * `planWorkLedgerView` already applies one surface over. A failed fetch
 * must never render as "0 users" — CLAUDE.md is explicit that this matters
 * MORE than usual here, since a broken RLS scope on the backend used to
 * silently produce exactly that. `planPlatformActivationView` is the one
 * place "loading vs. forbidden vs. could-not-load vs. has real numbers" is
 * decided, so the page component only ever renders what this function
 * returned rather than branching inline. `forbidden` is its own state
 * (401/403), not folded into the generic `error` — CLAUDE.md is explicit
 * `require_api_key` proves only "is someone logged in", and a caller who
 * is logged in but not an operator needs to be told THAT, not shown a
 * generic "could not load" message that reads as a bug.
 *
 * Pure, no React, no CSS import — same discipline as work-ledger.ts /
 * inbox-needs-you.ts, so platform-activation.test.ts drives the real rule
 * the page renders against instead of a re-typed copy of it.
 *
 * Run: npx tsx lib/workspace/fleet/platform-activation.test.ts
 */

export type PlatformActivationTotals = {
  users: number;
  workspaces: number;
  agents: number;
  projects: number;
  tasks: number;
  documents: number;
};

export type PlatformActivationSnapshot = {
  generated_at?: string | null;
  rls_bypass_verified?: boolean;
  totals: PlatformActivationTotals;
  activation: {
    users_with_activity: number;
    users_with_activity_pct: number;
    workspaces_with_multiple_members: number;
    workspaces_with_multiple_members_pct: number;
  };
  signups: {
    last_7_days: number;
    last_30_days: number;
  };
  agents_runtime: {
    total: number;
    with_runs: number;
    never_run: number;
  };
};

// ── Fetch outcome → render state ────────────────────────────────────────

export type PlatformActivationViewState =
  | { kind: "loading" }
  | { kind: "forbidden" }
  | { kind: "error"; message: string }
  | { kind: "ready"; snapshot: PlatformActivationSnapshot };

/** The one place a raw fetch outcome becomes a render state. `status` is
 *  whatever HTTP status the response carried (or `null` if the request
 *  never got a response at all — a network failure, not a server answer).
 *  401/403 read as `forbidden`, distinct from every other failure, because
 *  "you are not an operator" and "the server broke" are different facts a
 *  reader would act on differently (log in as an operator vs. try again
 *  later). A `snapshot` only ever reaches `ready` — there is no `empty`
 *  branch here the way work-ledger.ts's `planWorkLedgerView` has one for a
 *  genuinely empty row list, because every real response carries a full
 *  set of counts (even a legitimate all-zero platform is still "ready"
 *  data, never "nothing to show"). */
export function planPlatformActivationView(input: {
  loading: boolean;
  status: number | null;
  error: string | null;
  snapshot: PlatformActivationSnapshot | null;
}): PlatformActivationViewState {
  if (input.loading) return { kind: "loading" };
  if (input.status === 401 || input.status === 403) return { kind: "forbidden" };
  if (input.error) return { kind: "error", message: input.error };
  if (!input.snapshot) {
    return { kind: "error", message: "The server did not return any activation data." };
  }
  return { kind: "ready", snapshot: input.snapshot };
}

// ── Percent formatting ──────────────────────────────────────────────────

/** One decimal place, `%` suffix, never `NaN%`/`Infinity%` — the backend
 *  already rounds to one decimal (platform_activation_service._pct), this
 *  just guards a malformed/missing value rather than trusting the wire. */
export function formatActivationPercent(pct: number | null | undefined): string {
  const n = typeof pct === "number" && Number.isFinite(pct) ? pct : 0;
  return `${Math.round(n * 10) / 10}%`;
}

// ── Stat tiles ───────────────────────────────────────────────────────────

export type PlatformActivationStat = {
  key: string;
  label: string;
  value: number;
  /** Secondary descriptive text under the number, e.g. "4% of signed-up
   *  users" — null when the number needs no qualifier (a plain total). */
  caption: string | null;
  /** True for a number that means someone DID something (activation);
   *  false for a number that means someone merely EXISTS (acquisition).
   *  The one signal the page's styling reads to draw that distinction —
   *  see this file's header. */
  isActivation: boolean;
};

/** The summary row: the small set of numbers that actually answer "is
 *  anyone using this platform" — deliberately excludes the raw totals
 *  (workspace/project/task/document counts), which belong in the detail
 *  section below. Every value here has an activation-shaped denominator
 *  in its caption, so a bare "4" never has to be read against a total the
 *  reader has to go find elsewhere. */
export function platformActivationSummaryStats(
  snapshot: PlatformActivationSnapshot,
): PlatformActivationStat[] {
  return [
    {
      key: "signed-up-users",
      label: "Signed-up users",
      value: snapshot.totals.users,
      caption: null,
      isActivation: false,
    },
    {
      key: "activated-users",
      label: "Users who created a task or document",
      value: snapshot.activation.users_with_activity,
      caption: `${formatActivationPercent(snapshot.activation.users_with_activity_pct)} of signed-up users`,
      isActivation: true,
    },
    {
      key: "multi-member-workspaces",
      label: "Workspaces with more than one member",
      value: snapshot.activation.workspaces_with_multiple_members,
      caption: `${formatActivationPercent(snapshot.activation.workspaces_with_multiple_members_pct)} of workspaces`,
      isActivation: true,
    },
    {
      key: "agents-that-ran",
      label: "Agents that have actually run",
      value: snapshot.agents_runtime.with_runs,
      caption: `of ${snapshot.agents_runtime.total} created`,
      isActivation: true,
    },
  ];
}

/** The detail row: raw platform totals and the numbers that name the
 *  inverse of activation (never-run agents) rather than the activation
 *  itself — dense supporting facts, not the headline. */
export function platformActivationDetailStats(
  snapshot: PlatformActivationSnapshot,
): PlatformActivationStat[] {
  return [
    { key: "workspaces", label: "Workspaces", value: snapshot.totals.workspaces, caption: null, isActivation: false },
    { key: "agents", label: "Agents", value: snapshot.totals.agents, caption: null, isActivation: false },
    { key: "projects", label: "Projects", value: snapshot.totals.projects, caption: null, isActivation: false },
    { key: "tasks", label: "Tasks", value: snapshot.totals.tasks, caption: null, isActivation: false },
    { key: "documents", label: "Documents", value: snapshot.totals.documents, caption: null, isActivation: false },
    {
      key: "agents-never-run",
      label: "Agents that have never run",
      value: snapshot.agents_runtime.never_run,
      caption: null,
      isActivation: false,
    },
    {
      key: "signups-7d",
      label: "Signups, last 7 days",
      value: snapshot.signups.last_7_days,
      caption: null,
      isActivation: false,
    },
    {
      key: "signups-30d",
      label: "Signups, last 30 days",
      value: snapshot.signups.last_30_days,
      caption: null,
      isActivation: false,
    },
  ];
}
