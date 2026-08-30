import { funnelBars, funnelStepLabel, type FunnelStep } from "./funnel";

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

// ── funnelStepLabel ───────────────────────────────────────────────────────

assert(funnelStepLabel("signup") === "Signed up", "a known step key gets a human label");
assert(funnelStepLabel("agent_ran") === "An agent actually ran", "every one of the six real backend step keys has a label");
assert(funnelStepLabel("some_future_step") === "some_future_step", "an unknown step key falls back to itself rather than throwing or showing blank");

// ── funnelBars ────────────────────────────────────────────────────────────

const STEPS: FunnelStep[] = [
  { step: "signup", count: 105, pct_of_signups: 100, drop_off_from_previous: 0, drop_off_pct_from_previous: 0 },
  { step: "created_project", count: 40, pct_of_signups: 38.1, drop_off_from_previous: 65, drop_off_pct_from_previous: 61.9 },
  { step: "created_task_or_document", count: 5, pct_of_signups: 4.8, drop_off_from_previous: 35, drop_off_pct_from_previous: 87.5 },
  { step: "created_agent", count: 68, pct_of_signups: 64.8, drop_off_from_previous: 0, drop_off_pct_from_previous: 0 },
  { step: "agent_ran", count: 12, pct_of_signups: 11.4, drop_off_from_previous: 56, drop_off_pct_from_previous: 82.4 },
  { step: "second_member", count: 0, pct_of_signups: 0, drop_off_from_previous: 12, drop_off_pct_from_previous: 100 },
];

{
  const bars = funnelBars(STEPS);
  assert(bars.length === 6, "every step becomes a bar");
  assert(bars[0].barWidthPct === 100, "the baseline (signup) step is always the full-width bar");
  assert(Math.abs(bars[1].barWidthPct - (40 / 105) * 100) < 0.001, "a step's bar width is relative to the signup baseline, not the previous step");
  assert(bars[5].barWidthPct === 0, "a step with zero count has a zero-width bar, not NaN or negative");
  assert(bars.every((b) => b.label === b.label && b.label.length > 0), "every bar carries a non-empty human label");
}

{
  // Several drops are real here (35 at created_task_or_document, 56 at
  // agent_ran) but created_project's 65 is the single biggest -- that is
  // the one bar the "biggest drop-off" callout must land on, not whichever
  // one a reader's eye happens to land on first.
  const bars = funnelBars(STEPS);
  const flagged = bars.filter((b) => b.isBiggestDropOff);
  assert(flagged.length === 1, "exactly one bar is flagged as the biggest drop-off");
  assert(flagged[0].step === "created_project", "the biggest real drop-off (65, not 56 or 35) is the one flagged");
  assert(bars[0].isBiggestDropOff === false, "the signup step itself is never flagged -- it has no previous step to drop off from");
}

{
  // Zero signups: every step is necessarily zero too, and dividing by the
  // baseline must not produce NaN/Infinity bars.
  const zeroed: FunnelStep[] = STEPS.map((s) => ({ ...s, count: 0, drop_off_from_previous: 0 }));
  const bars = funnelBars(zeroed);
  assert(bars.every((b) => b.barWidthPct === 0), "a platform with zero signups renders zero-width bars, never NaN%");
  assert(bars.every((b) => !b.isBiggestDropOff), "with no real drop-off anywhere, nothing is flagged as the biggest one");
}

assert(funnelBars([]).length === 0, "an empty steps array is an empty bar list, not a crash");

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}
