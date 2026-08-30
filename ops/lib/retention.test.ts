import { retentionStats, type OperatorRetention } from "./retention";

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

const RETENTION: OperatorRetention = {
  generated_at: "2026-08-30T00:00:00Z",
  rls_bypass_verified: true,
  total_users: 105,
  active: {
    last_1_day: { count: 3, pct_of_users: 2.9 },
    last_7_days: { count: 8, pct_of_users: 7.6 },
    last_30_days: { count: 20, pct_of_users: 19.0 },
  },
};

{
  const stats = retentionStats(RETENTION);
  const byKey = Object.fromEntries(stats.map((s) => [s.key, s]));

  assert(byKey["total-users"]?.value === "105", "the total-users denominator is present");
  assert(byKey["total-users"]?.isActivation === false, "the raw total is acquisition, not activation");

  assert(byKey["active-1d"]?.value === "3", "1-day active count is present");
  assert(byKey["active-1d"]?.caption === "2.9% of users", "1-day active caption carries the real percent");
  assert(byKey["active-1d"]?.isActivation === true, "being active is flagged as activation");

  assert(byKey["active-7d"]?.value === "8", "7-day active count is present");
  assert(byKey["active-30d"]?.value === "20", "30-day active count is present");
  assert(byKey["active-30d"]?.caption === "19% of users", "a whole-number percent has no trailing .0 noise");
}

{
  // A platform with zero users: every window is necessarily zero too, and
  // the stats must still render as ready data (0%), never NaN%.
  const empty: OperatorRetention = {
    total_users: 0,
    active: {
      last_1_day: { count: 0, pct_of_users: 0 },
      last_7_days: { count: 0, pct_of_users: 0 },
      last_30_days: { count: 0, pct_of_users: 0 },
    },
  };
  const stats = retentionStats(empty);
  assert(stats.every((s) => !s.caption?.includes("NaN")), "zero users never produces a NaN% caption");
}

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}
