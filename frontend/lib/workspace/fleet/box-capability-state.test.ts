/**
 * Imports the REAL mapping functions rather than re-deriving the rule table
 * here — same discipline agent-count-shape.test.ts and
 * openclaw-channel-copy.test.ts already apply, for the identical reason
 * (CLAUDE.md: "a check that derives its own expectations from the thing it
 * checks is blind, and reports 'passed'"). The expected set and the actual
 * set must come from two different places.
 *
 * Table-driven over the FULL cross product of ALL_PROBE_STATUSES rather than
 * one case per named state: the task this module exists for is "every
 * combination of probe results maps to exactly one displayed state, and
 * 'unknown' never renders as 'missing'" — so the invariants below are
 * checked for every input the gateway's probes could ever report, not just
 * the handful this file's author thought to name. A THIRD raw status a
 * future probe introduces is exercised automatically the moment it is added
 * to ALL_PROBE_STATUSES in box-capability-state.ts, with no new test case
 * required here.
 *
 * Run: npx tsx lib/workspace/fleet/box-capability-state.test.ts
 */

import {
  ALL_PROBE_STATUSES,
  channelTransportPill,
  channelTransportSummary,
  planChannelTransportState,
  planSandboxCapabilityState,
  sandboxCapabilityPill,
  type ChannelTransportState,
  type SandboxCapabilityState,
} from "./box-capability-state";

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

const SANDBOX_STATES: readonly SandboxCapabilityState[] = ["working", "not_working", "absent", "unknown"];
const CHANNEL_STATES: readonly ChannelTransportState[] = ["ready", "set_up", "unavailable", "unknown"];

// ── Totality: every input in the domain produces exactly one of the
//    documented states, never throws, never falls through to something
//    else. This is what makes the function safe to add a probe status to
//    later without silently producing an undocumented fifth value. ────────

for (const status of ALL_PROBE_STATUSES) {
  const state = planSandboxCapabilityState(status);
  assert(
    SANDBOX_STATES.includes(state),
    `planSandboxCapabilityState(${String(status)}) returns one of the four sandbox states, got ${state}`,
  );
}

for (const transportStatus of ALL_PROBE_STATUSES) {
  for (const pluginsStatus of ALL_PROBE_STATUSES) {
    const state = planChannelTransportState({ transportStatus, pluginsStatus });
    assert(
      CHANNEL_STATES.includes(state),
      `planChannelTransportState(${String(transportStatus)}, ${String(pluginsStatus)}) returns one of the four channel states, got ${state}`,
    );
  }
}

// ── THE central rule, checked exhaustively rather than for a handful of
//    hand-picked cases: "unknown" (couldn't be probed, or never reported at
//    all) must never be indistinguishable from — or collapse into — a
//    confirmed negative ("missing"/"absent"/"unavailable"). ───────────────

for (const status of ["unknown", undefined] as const) {
  assert(
    planSandboxCapabilityState(status) === "unknown",
    `sandbox: an unconfirmed status (${String(status)}) never renders as "absent" — got ${planSandboxCapabilityState(status)}`,
  );
}
assert(planSandboxCapabilityState("missing") === "absent", "sandbox: a CONFIRMED absence renders as absent, not unknown");

for (const transportStatus of ["unknown", undefined] as const) {
  for (const pluginsStatus of ALL_PROBE_STATUSES) {
    const state = planChannelTransportState({ transportStatus, pluginsStatus });
    assert(
      state === "unknown",
      `channels: an unconfirmed transport status (${String(transportStatus)}) never renders as "unavailable" regardless of ` +
        `pluginsStatus=${String(pluginsStatus)} — got ${state}`,
    );
  }
}
// The converse: a CONFIRMED absence is deterministic and always "unavailable",
// regardless of what the (irrelevant, nothing to enumerate plugins on) second
// fact says — this is what makes "unavailable" a trustworthy strong signal
// rather than one that only sometimes fires.
for (const pluginsStatus of ALL_PROBE_STATUSES) {
  assert(
    planChannelTransportState({ transportStatus: "missing", pluginsStatus }) === "unavailable",
    `channels: transportStatus="missing" always renders "unavailable" regardless of pluginsStatus=${String(pluginsStatus)}`,
  );
}

// ── Soundness: "ready" (the strongest positive claim — a card that says
//    "Ready" with no button) requires BOTH underlying facts to be
//    confirmed good. Neither fact alone is enough. ─────────────────────────

for (const transportStatus of ALL_PROBE_STATUSES) {
  for (const pluginsStatus of ALL_PROBE_STATUSES) {
    const state = planChannelTransportState({ transportStatus, pluginsStatus });
    if (state === "ready") {
      assert(transportStatus === "ready", `channels: "ready" implies the transport was confirmed ready (was ${String(transportStatus)})`);
      assert(pluginsStatus === "ready", `channels: "ready" implies plugins were confirmed ready (was ${String(pluginsStatus)})`);
    }
  }
}

// ── The five screenshotted scenarios from the task brief, as concrete named
//    cases — the exhaustive checks above prove the RULE; these prove the
//    rule produces the right answer for the specific situations a person
//    will actually see. ────────────────────────────────────────────────────

// A healthy box: everything confirmed good.
assert(planSandboxCapabilityState("ready") === "working", "healthy box: sandbox working");
assert(
  planChannelTransportState({ transportStatus: "ready", pluginsStatus: "ready" }) === "ready",
  "healthy box: channels ready",
);

// A box with no sandbox: Docker confirmed absent (the install script never
// installed it) — sandbox reads absent, independent of channel state.
assert(planSandboxCapabilityState("missing") === "absent", "no-sandbox box: sandbox absent");

// A box with no transport: OpenClaw confirmed absent (the install script
// never installed it either — the actual incident this module exists for).
assert(
  planChannelTransportState({ transportStatus: "missing", pluginsStatus: "missing" }) === "unavailable",
  "no-transport box: channels unavailable",
);
const unavailableSummary = channelTransportSummary("unavailable");
assert(unavailableSummary.headline === "Channels unavailable.", "no-transport box: honest headline, no mechanism named");
assert(!/docker|npm|plugin|binary|openclaw/i.test(unavailableSummary.headline), "no-transport headline names no mechanism");
assert(unavailableSummary.hasSetupAction, "no-transport box: a working Set up action is offered, per the task brief");

// A box whose capabilities could not be probed at all: heartbeat present but
// every fact unknown/undefined (an old build, or a probe that never
// resolved) — both cards read "unknown", never "absent"/"unavailable".
assert(planSandboxCapabilityState(undefined) === "unknown", "unprobable box: sandbox unknown, not absent");
assert(
  planChannelTransportState({ transportStatus: undefined, pluginsStatus: undefined }) === "unknown",
  "unprobable box: channels unknown, not unavailable",
);
const unknownSummary = channelTransportSummary("unknown");
assert(!unknownSummary.hasSetupAction, "unprobable box: no dead Set up control when nothing is actually confirmed broken");

// ── Copy: mechanism-free pills (CLAUDE.md's mechanical guard for the
//    channel-card surface, applied identically here), and pill tone always
//    drawn from the established four-tone vocabulary. ─────────────────────

const VALID_TONES = new Set(["connected", "gateway", "locked", "setup"]);
for (const state of SANDBOX_STATES) {
  const pill = sandboxCapabilityPill(state);
  assert(VALID_TONES.has(pill.tone), `sandboxCapabilityPill(${state}) uses a known tone, got ${pill.tone}`);
  assert(!/docker|npm|plugin|binary/i.test(pill.label), `sandboxCapabilityPill(${state}) label "${pill.label}" names no mechanism`);
}
for (const state of CHANNEL_STATES) {
  const pill = channelTransportPill(state);
  assert(VALID_TONES.has(pill.tone), `channelTransportPill(${state}) uses a known tone, got ${pill.tone}`);
  assert(
    !/docker|npm|plugin|binary|openclaw/i.test(pill.label),
    `channelTransportPill(${state}) label "${pill.label}" names no mechanism`,
  );
  const summary = channelTransportSummary(state);
  assert(
    !/docker|npm|plugin|binary|openclaw/i.test(summary.headline),
    `channelTransportSummary(${state}) headline "${summary.headline}" names no mechanism`,
  );
}
// "Ready" never carries a setup action — nothing to fix, no dead control by
// omission of a needed one either.
assert(!channelTransportSummary("ready").hasSetupAction, `"ready" carries no setup action`);

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
