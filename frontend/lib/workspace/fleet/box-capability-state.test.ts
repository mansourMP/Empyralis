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
  ISOLATION_FULL_ACCESS_STATEMENT,
  ISOLATION_HOST_STATEMENT,
  ISOLATION_SANDBOX_STATEMENT,
  dockerStatusFromGatewayPayload,
  executionIsolationStatement,
  planExecutionIsolation,
  type ExecutionIsolation,
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

// ── Execution isolation: Docker chooses HOW, never WHETHER (2026-08-22) ──
//
// This whole section exists because the product used to treat Docker's
// absence as "I have no permission to run this". It now treats it as one of
// two true statements about what a computer does. These assertions are what
// fails if that regresses into a gate again.

const ISOLATIONS: readonly ExecutionIsolation[] = ["sandbox", "host", "full_access", "unknown"];

// Totality: every probe status yields exactly one documented isolation and
// never throws — same discipline as the sandbox/channel tables above.
for (const dockerStatus of ALL_PROBE_STATUSES) {
  const isolation = planExecutionIsolation({ dockerStatus });
  assert(
    ISOLATIONS.includes(isolation),
    `planExecutionIsolation(${String(dockerStatus)}) returns a documented isolation (got ${isolation})`,
  );
}

// The founder's rule in one assertion: a Docker probe that came back with a
// CONFIRMED answer always yields a runnable isolation. There is no probe
// result that means "nothing can run here".
assert(planExecutionIsolation({ dockerStatus: "ready" }) === "sandbox", `docker ready -> sandbox`);
for (const notReady of ["offline", "degraded", "blocked", "missing"] as const) {
  assert(
    planExecutionIsolation({ dockerStatus: notReady }) === "host",
    `docker ${notReady} -> host (a computer without a running sandbox still runs commands)`,
  );
}

// "Not probed yet" is a THIRD fact and must not be folded into either
// statement — the same law CLAUDE.md states for "empty" vs "could not load".
for (const unconfirmed of ["unknown", undefined] as const) {
  assert(
    planExecutionIsolation({ dockerStatus: unconfirmed }) === "unknown",
    `docker ${String(unconfirmed)} -> unknown, never guessed either way`,
  );
  assert(
    executionIsolationStatement(planExecutionIsolation({ dockerStatus: unconfirmed })) === null,
    `an unconfirmed probe renders NOTHING rather than a sentence that might be false`,
  );
}

// The three statements are distinguishable, and each is present exactly for
// its own state.
assert(executionIsolationStatement("sandbox") === ISOLATION_SANDBOX_STATEMENT, `sandbox statement`);
assert(executionIsolationStatement("host") === ISOLATION_HOST_STATEMENT, `host statement`);
assert(executionIsolationStatement("full_access") === ISOLATION_FULL_ACCESS_STATEMENT, `full_access statement`);
assert(
  new Set([ISOLATION_SANDBOX_STATEMENT, ISOLATION_HOST_STATEMENT, ISOLATION_FULL_ACCESS_STATEMENT]).size === 3,
  `the three statements never collapse into fewer`,
);

// CROSS-LANGUAGE PIN. These literals are duplicated in
// empyralis-gateway/src/shell/execution-isolation.ts (a different npm
// package — this frontend cannot import it). If someone edits the wording on
// one side only, the product says one thing on the Hardware surface and a
// different thing in the tool result for the same computer. Byte-for-byte.
assert(ISOLATION_SANDBOX_STATEMENT === "Commands run isolated in a container on this computer.", `sandbox literal pinned to the gateway's`);
assert(ISOLATION_HOST_STATEMENT === "Commands run directly on this computer, because Docker isn't running here.", `host literal pinned to the gateway's`);
assert(ISOLATION_FULL_ACCESS_STATEMENT === "Commands run directly on this computer, which has full access turned on.", `full_access literal pinned to the gateway's`);

// No statement may lecture, name a command, or read as a failure.
for (const statement of [ISOLATION_SANDBOX_STATEMENT, ISOLATION_HOST_STATEMENT, ISOLATION_FULL_ACCESS_STATEMENT]) {
  const lowered = statement.toLowerCase();
  for (const banned of ["docker run", "install", "brew", "apt", "systemctl", "open -a", "permission", "cannot", "can't", "unable", "failed", "error", "retry", "please"]) {
    assert(!lowered.includes(banned), `${JSON.stringify(statement)} must not contain ${JSON.stringify(banned)}`);
  }
}

// ── The full_access two-part opt-in, reflected honestly ──
// BOTH halves, or the label does not claim it. One half alone changes
// nothing about what the box actually does.
assert(
  planExecutionIsolation({ dockerStatus: "missing", runtimeAccessMode: "full_access", shellFullAccessLocallyEnabled: true }) === "full_access",
  `server-authorized AND locally enabled -> full_access`,
);
assert(
  planExecutionIsolation({ dockerStatus: "ready", runtimeAccessMode: "full_access", shellFullAccessLocallyEnabled: true }) === "full_access",
  `full_access wins over a ready Docker — the owner asked for the machine`,
);
assert(
  planExecutionIsolation({ dockerStatus: "ready", runtimeAccessMode: "full_access", shellFullAccessLocallyEnabled: false }) === "sandbox",
  `authorized server-side but OFF on the box -> the box sandboxes, and the label says so`,
);
assert(
  planExecutionIsolation({ dockerStatus: "offline", runtimeAccessMode: "full_access", shellFullAccessLocallyEnabled: false }) === "host",
  `authorized server-side but OFF on the box, no Docker -> host, not a false full_access claim`,
);
for (const unreported of [null, undefined] as const) {
  assert(
    planExecutionIsolation({ dockerStatus: "ready", runtimeAccessMode: "full_access", shellFullAccessLocallyEnabled: unreported }) === "unknown",
    `authorized server-side, box half UNREPORTED -> unknown (an older gateway build; guessing either way would be a lie)`,
  );
}
// A box with no full_access authorization at all ignores the local flag
// entirely — it cannot escalate itself.
assert(
  planExecutionIsolation({ dockerStatus: "offline", runtimeAccessMode: "default_guarded", shellFullAccessLocallyEnabled: true }) === "host",
  `local flag alone never yields full_access — the server half is required`,
);
assert(
  planExecutionIsolation({ dockerStatus: "offline", shellFullAccessLocallyEnabled: true }) === "host",
  `no runtime_access_mode at all never yields full_access`,
);

// ── Reading the docker status off a real registration payload shape ──
assert(
  dockerStatusFromGatewayPayload({ service_readiness: { docker: { status: "ready" } } }) === "ready",
  `prefers the backend's distilled service_readiness`,
);
assert(
  dockerStatusFromGatewayPayload({ metadata: { service_inventory: [{ id: "ollama", status: "ready" }, { id: "docker", status: "offline" }] } }) === "offline",
  `falls back to the raw service_inventory list`,
);
assert(
  dockerStatusFromGatewayPayload({ service_readiness: { docker: { status: "unknown" } }, metadata: { service_inventory: [{ id: "docker", status: "ready" }] } }) === "ready",
  `a distilled "unknown" defers to a real raw status rather than masking it`,
);
assert(
  dockerStatusFromGatewayPayload({ metadata: { service_inventory: [] } }) === undefined,
  `nothing reported -> undefined, never a guessed status`,
);
assert(dockerStatusFromGatewayPayload(null) === undefined, `a null payload -> undefined, never throws`);
assert(dockerStatusFromGatewayPayload(undefined) === undefined, `an undefined payload -> undefined, never throws`);

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
