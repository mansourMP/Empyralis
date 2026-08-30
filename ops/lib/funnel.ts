/**
 * Pure shaping for `/funnel` -- signup -> project -> task/document -> agent
 * -> agent ran -> invited a second person, the other of the two screens the
 * founder said matter most for MAN-149. Backed by
 * `GET /api/internal/operator/activation-funnel`
 * (server_modules/operator_console_service.build_activation_funnel) -- the
 * backend already computes count/pct_of_signups/drop_off per step; this
 * module's job is turning that into a bar's actual width (relative to the
 * biggest step, honestly handling the zero-signups case) and giving each
 * step key a name a person reads, not a SQL alias.
 *
 * Run: npx tsx lib/funnel.test.ts
 */

export type FunnelStepKey =
  | "signup"
  | "created_project"
  | "created_task_or_document"
  | "created_agent"
  | "agent_ran"
  | "second_member";

export type FunnelStep = {
  step: string;
  count: number;
  pct_of_signups: number;
  drop_off_from_previous: number;
  drop_off_pct_from_previous: number;
};

export type ActivationFunnel = {
  generated_at?: string | null;
  rls_bypass_verified?: boolean;
  steps: FunnelStep[];
};

export const FUNNEL_STEP_LABELS: Record<FunnelStepKey, string> = {
  signup: "Signed up",
  created_project: "Created a project",
  created_task_or_document: "Created a task or document",
  created_agent: "Installed an agent",
  agent_ran: "An agent actually ran",
  second_member: "Invited a second member",
};

export function funnelStepLabel(step: string): string {
  return FUNNEL_STEP_LABELS[step as FunnelStepKey] ?? step;
}

export type FunnelBar = FunnelStep & {
  label: string;
  /** Width relative to the FIRST step's count (signups) -- the honest
   *  denominator for "how much of the platform reached this step", not the
   *  previous step's count, which would make every bar's width relative to
   *  a different, shrinking baseline and impossible to compare visually. */
  barWidthPct: number;
  /** True only for the biggest drop-off in the whole funnel -- the one step
   *  worth calling out, computed once here rather than left for the page to
   *  scan the array itself. Never true for the `signup` step, which has no
   *  "previous" to drop off from. */
  isBiggestDropOff: boolean;
};

/** `steps` must be non-empty and start with the true baseline (`signup`) --
 *  callers passing the real API response satisfy this automatically since
 *  `build_activation_funnel` always returns all six steps in order. A
 *  malformed/empty array reads as no bars, never a divide-by-zero crash. */
export function funnelBars(steps: FunnelStep[]): FunnelBar[] {
  if (steps.length === 0) return [];
  const baseline = steps[0].count;

  let biggestDropOffIndex = -1;
  let biggestDropOff = -1;
  steps.forEach((s, i) => {
    if (i > 0 && s.drop_off_from_previous > biggestDropOff) {
      biggestDropOff = s.drop_off_from_previous;
      biggestDropOffIndex = i;
    }
  });

  return steps.map((s, i) => ({
    ...s,
    label: funnelStepLabel(s.step),
    barWidthPct: baseline > 0 ? Math.max(0, Math.min(100, (s.count / baseline) * 100)) : 0,
    isBiggestDropOff: i === biggestDropOffIndex && biggestDropOff > 0,
  }));
}
