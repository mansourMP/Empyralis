/**
 * MAN-149 operator activation page — proven against the REAL rules
 * platform-activation.ts exports, same discipline work-ledger.test.ts /
 * inbox-needs-you.test.ts already apply (no re-typed copy of the logic
 * under test).
 *
 * The assertions that matter most are the NEGATIVE ones: a failed or
 * forbidden fetch must never reach the `ready` branch with a snapshot full
 * of zeros — that is the exact bug CLAUDE.md calls out for this page
 * specifically (an RLS-scoped connection silently returning 0 for every
 * table), now guarded at the frontend's own render-state boundary too.
 *
 * Run: npx tsx lib/workspace/fleet/platform-activation.test.ts
 */

import {
  formatActivationPercent,
  planPlatformActivationView,
  platformActivationDetailStats,
  platformActivationSummaryStats,
  type PlatformActivationSnapshot,
} from "./platform-activation";

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

const SNAPSHOT: PlatformActivationSnapshot = {
  generated_at: "2026-08-30T00:00:00Z",
  rls_bypass_verified: true,
  totals: { users: 105, workspaces: 105, agents: 68, projects: 166, tasks: 36, documents: 5 },
  activation: {
    users_with_activity: 4,
    users_with_activity_pct: 3.8,
    workspaces_with_multiple_members: 0,
    workspaces_with_multiple_members_pct: 0,
  },
  signups: { last_7_days: 2, last_30_days: 9 },
  agents_runtime: { total: 68, with_runs: 12, never_run: 56 },
};

// ── planPlatformActivationView: outcome honesty ──────────────────────────

assert(planPlatformActivationView({ loading: true, status: null, error: null, snapshot: null }).kind === "loading", "still loading, no verdict yet");

assert(
  planPlatformActivationView({ loading: false, status: 403, error: "Forbidden", snapshot: null }).kind === "forbidden",
  "a 403 reads as forbidden, not a generic error",
);
assert(
  planPlatformActivationView({ loading: false, status: 401, error: null, snapshot: null }).kind === "forbidden",
  "a 401 also reads as forbidden — require_api_key alone was never authorization",
);

{
  const state = planPlatformActivationView({ loading: false, status: 503, error: "Control-plane database is unavailable", snapshot: null });
  assert(state.kind === "error", "a 503 (pool unavailable) is an error, never a silent empty/zero snapshot");
  assert(
    state.kind === "error" && state.message === "Control-plane database is unavailable",
    "the real server message reaches the render state, not a generic string",
  );
}

{
  const state = planPlatformActivationView({ loading: false, status: 500, error: "app.rls_bypass was not 'on'", snapshot: null });
  assert(state.kind === "error", "a broken RLS scope surfaces as an error");
}

assert(
  planPlatformActivationView({ loading: false, status: null, error: "Network error", snapshot: null }).kind === "error",
  "a request that never got a response (status null) is still an error, not treated as forbidden or ready",
);

{
  // The exact regression this whole page exists to prevent: a 200 whose
  // body genuinely carries zeros must still reach `ready` — a real empty
  // platform is honest data, not a symptom to hide. Distinguishing "empty"
  // from "broken" is the BACKEND's job (the rls_bypass canary); once a
  // snapshot object reaches this function at all, it is already verified.
  const zeroed: PlatformActivationSnapshot = {
    ...SNAPSHOT,
    totals: { users: 0, workspaces: 0, agents: 0, projects: 0, tasks: 0, documents: 0 },
    activation: { users_with_activity: 0, users_with_activity_pct: 0, workspaces_with_multiple_members: 0, workspaces_with_multiple_members_pct: 0 },
  };
  const state = planPlatformActivationView({ loading: false, status: 200, error: null, snapshot: zeroed });
  assert(state.kind === "ready", "a genuinely all-zero snapshot still renders as ready data, not hidden as an error");
}

{
  const state = planPlatformActivationView({ loading: false, status: 200, error: null, snapshot: SNAPSHOT });
  assert(state.kind === "ready", "a normal successful fetch reaches ready");
  assert(state.kind === "ready" && state.snapshot.totals.users === 105, "the real snapshot passes through untouched");
}

assert(
  planPlatformActivationView({ loading: false, status: 200, error: null, snapshot: null }).kind === "error",
  "a 200 with no body is still an error — never fabricated as ready with nothing in it",
);

// ── formatActivationPercent ───────────────────────────────────────────────

assert(formatActivationPercent(3.8) === "3.8%", "one decimal place, percent suffix");
assert(formatActivationPercent(0) === "0%", "zero formats plainly");
assert(formatActivationPercent(100) === "100%", "100 formats plainly, no trailing .0 noise beyond the raw number");
assert(formatActivationPercent(null) === "0%", "a missing percent never renders as NaN%");
assert(formatActivationPercent(undefined) === "0%", "an undefined percent never renders as undefined%");
assert(formatActivationPercent(Number.NaN) === "0%", "an actual NaN never leaks through as text");

// ── platformActivationSummaryStats ────────────────────────────────────────

{
  const stats = platformActivationSummaryStats(SNAPSHOT);
  assert(stats.length === 4, "the summary is a small, fixed set of activation-shaped numbers");

  const byKey = Object.fromEntries(stats.map((s) => [s.key, s]));

  assert(byKey["signed-up-users"]?.value === 105, "signed-up users total is present");
  assert(byKey["signed-up-users"]?.isActivation === false, "signed-up users is acquisition, not activation");

  assert(byKey["activated-users"]?.value === 4, "activated-user count is the real activation number, not the signup count");
  assert(byKey["activated-users"]?.isActivation === true, "activated users is flagged as activation");
  assert(
    byKey["activated-users"]?.caption === "3.8% of signed-up users",
    "the activation caption carries a real denominator, not a bare percentage",
  );

  assert(byKey["multi-member-workspaces"]?.value === 0, "MAN-149's own headline fact: nobody has invited anybody");
  assert(byKey["multi-member-workspaces"]?.isActivation === true, "multi-member workspaces is an activation signal");

  assert(byKey["agents-that-ran"]?.value === 12, "agents that ran uses the runtime count, not the total install count");
  assert(byKey["agents-that-ran"]?.caption === "of 68 created", "the caption gives the denominator agents were measured against");

  assert(
    stats.every((s) => !("workspaces" in s) && !("tasks" in s)),
    "raw totals (workspaces/tasks/etc.) do not leak into the activation summary",
  );
}

// ── platformActivationDetailStats ─────────────────────────────────────────

{
  const stats = platformActivationDetailStats(SNAPSHOT);
  const byKey = Object.fromEntries(stats.map((s) => [s.key, s]));

  assert(byKey["workspaces"]?.value === 105, "raw workspace total is in the detail set");
  assert(byKey["tasks"]?.value === 36, "raw task total is in the detail set");
  assert(byKey["documents"]?.value === 5, "raw document total is in the detail set — the founder's own headline number");
  assert(byKey["agents-never-run"]?.value === 56, "never-run agent count is derived correctly (68 total - 12 with runs)");
  assert(byKey["signups-7d"]?.value === 2, "7-day signup trend is present");
  assert(byKey["signups-30d"]?.value === 9, "30-day signup trend is present");

  assert(
    stats.every((s) => s.isActivation === false),
    "every detail stat is a plain total, never flagged as activation — that distinction lives only in the summary",
  );

  assert(
    !stats.some((s) => s.key === "activated-users"),
    "the activation numbers themselves are never duplicated into the detail set",
  );
}

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}
