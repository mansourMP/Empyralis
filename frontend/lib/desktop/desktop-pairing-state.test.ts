/**
 * Drives the REAL desktop pairing rule — never a copy of its thresholds.
 *
 * The assertions that matter most here are the NEGATIVE ones. Every
 * "connected" case below also passes under a naive `items.some(online)`
 * implementation; only the wrong-gateway and no-identity cases can tell the
 * two apart, and those are exactly the cases that would ship a confident
 * "This computer is connected" onto a Mac that never paired.
 */

import assert from "node:assert/strict";

import {
  classifyThisMachine,
  describePairing,
  findThisMachine,
  resolveStartOutcome,
  type DesktopPairingPhase,
  type GatewayRegistrationItem,
} from "./desktop-pairing-state";

const THIS_MACHINE = "gw_this_machine_0001";
const OTHER_MACHINE = "gw_production_vps_9999";

const online = (gateway_id: string): GatewayRegistrationItem => ({
  gateway_id,
  connection_status: "online",
  latest_session_status: "connected",
});

// ── Identity matching ────────────────────────────────────────────────────

assert.equal(classifyThisMachine(online(THIS_MACHINE), THIS_MACHINE), "connected");

// OBSERVED LIVE on a real first pairing: a freshly paired Mac reports
// execution_blocked (Docker not running) with a live session. It IS
// connected. Reading this as "absent" would report failure on success on the
// most common machine there is -- a Mac nobody has installed Docker on.
assert.equal(
  classifyThisMachine(
    { gateway_id: THIS_MACHINE, connection_status: "execution_blocked", latest_session_status: "connected" },
    THIS_MACHINE,
  ),
  "limited",
  "execution_blocked is a demotion from online, not a disconnection",
);
assert.equal(
  resolveStartOutcome({ machine: "limited", supervisorOk: true }).phase,
  "degraded",
  "a connected-but-blocked machine must not report as a failure to connect",
);
assert.notEqual(resolveStartOutcome({ machine: "limited", supervisorOk: true }).phase, "couldNotConfirm");

// Every other status is not a connected machine, and an unrecognised one
// fails closed rather than drifting into "connected" as the vocabulary grows.
for (const status of ["degraded", "reconnecting", "offline", "revoked", "something_new"]) {
  assert.equal(
    classifyThisMachine(
      { gateway_id: THIS_MACHINE, connection_status: status, latest_session_status: "connected" },
      THIS_MACHINE,
    ),
    "absent",
    `${status} must not read as connected`,
  );
}

// The load-bearing negative: the founder's own workspace holds a permanently
// online production VPS. "Any gateway online" would render Connected here.
assert.equal(
  classifyThisMachine(online(OTHER_MACHINE), THIS_MACHINE),
  "absent",
  "another workspace gateway being online must never count as this machine",
);
assert.equal(
  findThisMachine([online(OTHER_MACHINE)], THIS_MACHINE),
  "absent",
  "a workspace full of other online machines is still not THIS machine",
);
assert.equal(findThisMachine([online(OTHER_MACHINE), online(THIS_MACHINE)], THIS_MACHINE), "connected");

// A connected machine outranks a limited one in a mixed list.
assert.equal(
  findThisMachine(
    [
      { gateway_id: THIS_MACHINE, connection_status: "execution_blocked", latest_session_status: "connected" },
      online(THIS_MACHINE),
    ],
    THIS_MACHINE,
  ),
  "connected",
);

// No identity yet (never paired) must fail closed, never fall back to "any".
assert.equal(findThisMachine([online(OTHER_MACHINE)], null), "absent");
assert.equal(findThisMachine([online(OTHER_MACHINE)], "   "), "absent");

// Both conditions are required — a stale registration with a dead session is
// not a connected machine.
assert.equal(
  classifyThisMachine(
    { gateway_id: THIS_MACHINE, connection_status: "online", latest_session_status: "disconnected" },
    THIS_MACHINE,
  ),
  "absent",
  "an online registration with a dead session must not read as connected",
);
assert.equal(
  classifyThisMachine(
    { gateway_id: THIS_MACHINE, connection_status: "execution_blocked", latest_session_status: "disconnected" },
    THIS_MACHINE,
  ),
  "absent",
  "a live session is required even for the limited state",
);
assert.equal(classifyThisMachine({}, THIS_MACHINE), "absent");

// ── Outcome resolution: three facts, three phases ────────────────────────

assert.equal(resolveStartOutcome({ machine: "connected", supervisorOk: true }).phase, "connected");

// Started but never seen by the control plane is "I lost track of it",
// never "failed" — the process is still running and may yet connect.
assert.equal(resolveStartOutcome({ machine: "absent", supervisorOk: true }).phase, "couldNotConfirm");
assert.notEqual(resolveStartOutcome({ machine: "absent", supervisorOk: true }).phase, "failed");

// A failed supervisor install is good news with a caveat, not a failure.
assert.equal(resolveStartOutcome({ machine: "connected", supervisorOk: false }).phase, "degraded");

// An unconfirmed connection outranks every caveat: there is no point telling
// someone their machine will not restart cleanly when we cannot say it is
// connected at all.
assert.equal(resolveStartOutcome({ machine: "absent", supervisorOk: false }).phase, "couldNotConfirm");

// ── What each phase is allowed to say ────────────────────────────────────

const ALL_PHASES: DesktopPairingPhase[] = [
  "inactive",
  "checking",
  "pairing",
  "starting",
  "connected",
  "degraded",
  "couldNotConfirm",
  "failed",
];

// Only a clean success may fade away. Every other resting state carries a
// fact the owner has not acted on yet.
assert.equal(describePairing("connected").transient, true);
for (const phase of ["degraded", "couldNotConfirm", "failed"] as const) {
  assert.equal(describePairing(phase).transient, false, `${phase} must not fade away unseen`);
  assert.equal(describePairing(phase).canRetry, true, `${phase} must offer a way out`);
}

// A retry control on a phase that is still working is a dead control.
for (const phase of ["checking", "pairing", "starting"] as const) {
  assert.equal(describePairing(phase).canRetry, false, `${phase} must not offer retry while working`);
}

// "Connecting…" must not be reachable from a finished attempt — a spinner
// that never resolves is the exact thing this surface exists to prevent.
for (const machine of ["connected", "limited", "absent"] as const) {
  for (const supervisorOk of [true, false]) {
    const phase = resolveStartOutcome({ machine, supervisorOk }).phase;
    assert.ok(
      !["checking", "pairing", "starting"].includes(phase),
      "a finished attempt must never resolve to a still-working phase",
    );
  }
}

// No mechanism in customer-facing prose. A person who does not know what an
// agent is reads these sentences.
const BANNED = ["gateway", "npm", "node", "process", "token", "daemon", "binary", "sidecar", "pid"];
const sentences = [
  ...ALL_PHASES.map((phase) => describePairing(phase).title),
  resolveStartOutcome({ machine: "connected", supervisorOk: true }).detail,
  resolveStartOutcome({ machine: "connected", supervisorOk: false }).detail,
  resolveStartOutcome({ machine: "limited", supervisorOk: true }).detail,
  resolveStartOutcome({ machine: "absent", supervisorOk: true }).detail,
];
for (const sentence of sentences) {
  for (const word of BANNED) {
    assert.ok(
      !sentence.toLowerCase().includes(word),
      `customer-facing copy must not name mechanism (${word}): ${JSON.stringify(sentence)}`,
    );
  }
}

// Canary: if the phase list is ever emptied or the describe function starts
// returning blanks, the loops above would assert nothing at all.
assert.ok(ALL_PHASES.length >= 8, "phase list canary");
assert.ok(
  ALL_PHASES.filter((phase) => phase !== "inactive").every((phase) => describePairing(phase).title.length > 0),
  "every visible phase must have something to say",
);

console.log("desktop-pairing-state.test.ts: all assertions passed");
