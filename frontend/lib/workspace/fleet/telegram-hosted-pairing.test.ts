/**
 * The hosted-Telegram pairing states, driven through the REAL rule.
 *
 * Two things this file deliberately does NOT do:
 *   - it never rebuilds the precedence order locally (that would be a check
 *     transcribing its expectations from the thing it checks);
 *   - it never hardcodes the backend's 6-digit code length or its 600s TTL.
 *     Both are read off the response shape, which is the whole point of
 *     hostedPairingCodeIsTypeable / hostedPairingCodeExpiry.
 *
 * The response fixtures below are the BACKEND'S OWN SHAPES, copied from
 * routes_sage_telegram_hosted.py's two literal return branches (:76-92) rather
 * than invented — CLAUDE.md, "a fixture that invents its own input cannot
 * notice the real input is shaped differently".
 *
 * Run: npx tsx lib/workspace/fleet/telegram-hosted-pairing.test.ts
 */

import {
  planHostedPairing,
  hostedPairingCodeIsTypeable,
  hostedPairingCodeExpiry,
  hostedDoorSupported,
  telegramDoorDeploymentSupported,
  hostedPairingHeadline,
  type HostedPairingState,
  type HostedPairingStartResponse,
  type HostedPairingStatusResponse,
} from "./telegram-hosted-pairing";
import { CHANNEL_DOORS, planChannelDoors, isChannelDoorAvailable, channelDoorUnavailableReason } from "./channel-doors";

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

const NOW = 1_700_000_000_000;

function plan(over: Partial<Parameters<typeof planHostedPairing>[0]>): HostedPairingState {
  return planHostedPairing({
    status: null,
    statusError: null,
    start: null,
    startedAt: null,
    now: NOW,
    ...over,
  });
}

const ok: HostedPairingStatusResponse = {
  configured: true,
  has_pending_code: false,
  paired: false,
  suspended: false,
  needs_reauth: false,
};

// ── The two literal pair/start branches, verbatim from the backend ──────────
// Branch 1 (fresh): a 6-digit code AND a deep link built from a SEPARATE token.
const FRESH: HostedPairingStartResponse = {
  pairing_code: "482913",
  deep_link: "https://t.me/EmpyralisSageBot?start=kQKmtZCd4tbi0sFPyfSLbRXfJoggvni1",
  bot_username: "EmpyralisSageBot",
  status: "active",
  expires_in_seconds: 600,
};
// Branch 2 (a session already exists, token only): the LONG TOKEN is reused in
// the `pairing_code` field and the link is built from it. This exact shape is
// what the deleted panel rendered into its "type this code" box.
const EXISTING_TOKEN: HostedPairingStartResponse = {
  pairing_code: "kQKmtZCd4tbi0sFPyfSLbRXfJoggvni1",
  deep_link: "https://t.me/EmpyralisSageBot?start=kQKmtZCd4tbi0sFPyfSLbRXfJoggvni1",
  bot_username: "EmpyralisSageBot",
  status: "active",
};
// Branch 2 (a numeric code is already outstanding): pair/start returns it with
// deep_link EXPLICITLY None — the backend cannot rebuild a link from a code.
const EXISTING_CODE: HostedPairingStartResponse = {
  pairing_code: "482913",
  deep_link: null,
  bot_username: "EmpyralisSageBot",
  status: "active",
  expires_in_seconds: 600,
};

// ── Is the value in `pairing_code` a code, or the deep-link token? ──────────

assert(hostedPairingCodeIsTypeable(FRESH), "a fresh 6-digit code is typeable");
assert(
  !hostedPairingCodeIsTypeable(EXISTING_TOKEN),
  "the long deep-link token reused as pairing_code is NOT typeable — the historical bug",
);
assert(
  hostedPairingCodeIsTypeable(EXISTING_CODE),
  "an outstanding numeric code with no link is still typeable",
);
assert(!hostedPairingCodeIsTypeable(null), "no start response, nothing to type");
assert(!hostedPairingCodeIsTypeable({ pairing_code: "  " }), "a blank code is not typeable");
// The rule must be structural, not a length constant: a code of ANY length is
// non-typeable exactly when the link is built from it.
assert(
  !hostedPairingCodeIsTypeable({ pairing_code: "12", deep_link: "https://t.me/b?start=12" }),
  "typeability is decided by the link, never by how long the value is (short token)",
);
assert(
  hostedPairingCodeIsTypeable({ pairing_code: "abcdefghijklmnop", deep_link: "https://t.me/b?start=OTHER" }),
  "typeability is decided by the link, never by how long the value is (long code)",
);

// ── Expiry is READ, never assumed ──────────────────────────────────────────

assert(hostedPairingCodeExpiry(FRESH, NOW) === NOW + 600_000, "expiry comes from the backend's own TTL field");
assert(
  hostedPairingCodeExpiry(EXISTING_TOKEN, NOW) === null,
  "a deep-link token has no expiry — the backend time-tracks numeric codes only",
);
assert(
  hostedPairingCodeExpiry({ ...FRESH, expires_in_seconds: null }, NOW) === null,
  "an older backend that reports no TTL gets NO countdown, never an invented one",
);
assert(hostedPairingCodeExpiry(FRESH, null) === null, "no start time, no countdown");

// ── Every state, and the precedence between them ───────────────────────────

assert(plan({}).kind === "checking", "status not back yet -> checking");
assert(
  plan({ statusError: "network" }).kind === "status_unknown",
  "a FAILED status call is status_unknown, never 'not connected'",
);
assert(
  plan({ status: { ...ok, configured: false } }).kind === "unsupported",
  "no hosted bot on this deployment -> unsupported",
);
assert(
  plan({ status: { paired: false } }).kind === "status_unknown",
  "a body that never says whether it is configured is unknown, not unsupported",
);
assert(plan({ status: ok }).kind === "idle", "configured, not paired, not started -> idle");
assert(
  plan({ status: { ...ok, paired: true } }).kind === "connected",
  "paired -> connected",
);
assert(
  plan({ status: { ...ok, suspended: true } }).kind === "unreachable",
  "the 401 circuit breaker -> unreachable, distinct from unsupported",
);
assert(
  plan({ status: { ...ok, needs_reauth: true } }).kind === "unreachable",
  "needs_reauth is the same fact as suspended",
);

// "Connected" and "connected but the shared bot's token is dead" are two
// different facts about a connection that still LOOKS fine.
const degraded = plan({ status: { ...ok, paired: true, suspended: true } });
assert(
  degraded.kind === "connected" && degraded.botUnreachable === true,
  "paired + suspended -> connected, flagged as not replying",
);
const healthy = plan({ status: { ...ok, paired: true } });
assert(
  healthy.kind === "connected" && healthy.botUnreachable === false,
  "paired + healthy -> connected, not flagged",
);
assert(
  plan({ status: { ...ok, paired: true, configured: false } }).kind === "connected",
  "an existing pairing outranks a deployment that has since dropped its token",
);

// ── Waiting, and what the customer is actually given ───────────────────────

const waiting = plan({ status: ok, start: FRESH, startedAt: NOW });
assert(waiting.kind === "waiting", "a live code/link -> waiting");
assert(
  waiting.kind === "waiting" && waiting.code === "482913" && waiting.deepLink !== null,
  "a fresh start offers BOTH the tap-link and the typeable code",
);
const waitingToken = plan({ status: ok, start: EXISTING_TOKEN, startedAt: NOW });
assert(
  waitingToken.kind === "waiting" && waitingToken.code === null && waitingToken.deepLink !== null,
  "an existing token session offers the link and NO code box",
);

// A code addressed to a bot nobody can name is not a way in. This is the state
// a deployment reaches when the token is set but getMe fails (revoked or
// placeholder) — pair/start still returns 200.
assert(
  plan({
    status: ok,
    start: { pairing_code: "482913", deep_link: null, bot_username: null },
    startedAt: NOW,
  }).kind === "unreachable",
  "a code with no resolvable bot username is unreachable, not a waiting screen",
);

// ── Expiry: only a code-ONLY pairing can actually run out ──────────────────

const afterTtl = NOW + 600_001;
const stillLinked = plan({ status: ok, start: FRESH, startedAt: NOW, now: afterTtl });
assert(
  stillLinked.kind === "waiting" && stillLinked.codeExpired === true && stillLinked.code === null,
  "an expired code drops the code box but keeps waiting — the deep-link token never expires",
);
assert(
  stillLinked.kind === "waiting" && stillLinked.deepLink !== null,
  "the link survives its sibling code expiring",
);
assert(
  plan({ status: ok, start: EXISTING_CODE, startedAt: NOW, now: afterTtl }).kind === "expired",
  "a code-only pairing that runs out IS expired — a distinct fact from 'waiting'",
);
assert(
  plan({ status: ok, start: EXISTING_CODE, startedAt: NOW }).kind === "waiting",
  "the same code-only pairing before the TTL is still waiting",
);
// "Nobody has tapped it yet" and "it can no longer be tapped" must never share
// a screen — the requirement this whole module exists to satisfy.
assert(
  plan({ status: ok, start: EXISTING_CODE, startedAt: NOW }).kind !==
    plan({ status: ok, start: EXISTING_CODE, startedAt: NOW, now: afterTtl }).kind,
  "waiting and expired are different states, not one spinner",
);

// ── Door availability: unknown is never rendered as unavailable ────────────

assert(hostedDoorSupported(null) === null, "not asked yet -> unknown");
assert(hostedDoorSupported({ configured: true }) === true, "configured -> supported");
assert(hostedDoorSupported({ configured: false }) === false, "not configured -> unsupported");
assert(hostedDoorSupported({}) === null, "a body that omits the field -> unknown, not false");

// ── The door itself, through the REAL door model ───────────────────────────

const telegramPlan = planChannelDoors("sage_telegram_hosted");
assert(
  telegramPlan.mode === "picker",
  "Telegram now has two real doors, so planDoors resolves it to a picker with no per-channel special case",
);
assert(telegramPlan.doors.length === 2, "exactly two real Telegram doors");
const hosted = telegramPlan.doors.find((d) => d.key === "hosted_bot");
const byo = telegramPlan.doors.find((d) => d.key === "byo_bot");
assert(!!hosted, "the hosted door exists");
assert(!!byo, "the BYO door still exists");
assert(!hosted?.requiresHardware, "the hosted door needs no computer — it is the zero-setup path");
assert(!byo?.requiresHardware, "the BYO door needs no computer either");

// THE HONESTY ASSERTION. The hosted bot answers as Sage, not as this agent
// (verified in routes_sage_telegram_hosted.telegram_webhook: no
// specialist_context is passed to dispatch_sage_reply_safe). A door face that
// implies otherwise is a lie no behavioural test can catch, because the
// pairing succeeds and a reply really does arrive.
assert(
  /workspace assistant/i.test(hosted?.body || ""),
  "the hosted door's face says it answers as the workspace assistant",
);
assert(
  /not (as )?this agent|rather than this agent/i.test(
    `${hosted?.body || ""} ${hosted?.consequence?.text || ""}`,
  ),
  "the hosted door's face says plainly that it is NOT this agent answering",
);
assert(
  /this agent/i.test(byo?.body || ""),
  "the BYO door's face still says it is this agent's own bot",
);
assert(
  !/BotFather|token/i.test(hosted?.body || ""),
  "the hosted door does not mention a token — its whole point is that there isn't one",
);
assert(
  /BotFather|token/i.test(byo?.body || "") || /BotFather|token/i.test(byo?.consequence?.text || ""),
  "the BYO door is the one that mentions a token",
);
// The picker only earns its place if the two faces differ.
assert(hosted?.body !== byo?.body, "the two doors say different things");

// ── Deployment availability flows through the shared door model ────────────

const hasHw = { hasHardware: true, doorConnected: false };
assert(
  isChannelDoorAvailable(hosted!, { ...hasHw, deploymentSupported: false }) === false,
  "a deployment with no hosted bot makes the door unavailable — no dead Connect button",
);
assert(
  isChannelDoorAvailable(hosted!, { ...hasHw, deploymentSupported: true }) === true,
  "a configured deployment leaves the door available",
);
assert(
  isChannelDoorAvailable(hosted!, { ...hasHw, deploymentSupported: null }) === true,
  "UNKNOWN never removes the door — 'we haven't checked' is not 'no'",
);
// The per-door-ness lives in the MAPPING, not in the shared availability
// function (which honours whatever it is handed). This is the rule that keeps
// a deployment with no hosted bot from making the BYO door look broken too.
const noBot: HostedPairingStatusResponse = { configured: false };
assert(
  telegramDoorDeploymentSupported("hosted_bot", noBot) === false,
  "the hosted door is the one that depends on this installation",
);
assert(
  telegramDoorDeploymentSupported("byo_bot", noBot) === undefined,
  "the BYO door has NO deployment dependency — the hosted bot's absence must not touch it",
);
assert(
  isChannelDoorAvailable(byo!, { ...hasHw, deploymentSupported: telegramDoorDeploymentSupported("byo_bot", noBot) }) === true,
  "so with no hosted bot configured, the BYO door is still fully available",
);
assert(
  isChannelDoorAvailable(hosted!, { ...hasHw, deploymentSupported: telegramDoorDeploymentSupported("hosted_bot", noBot) }) === false,
  "...while the hosted door on the same card is not",
);
assert(
  telegramDoorDeploymentSupported("hosted_bot", null) === null,
  "before the status lands, the hosted door's support is unknown, not false",
);
// Every pre-existing caller passes no deployment flag at all and must behave
// byte-for-byte as before.
assert(
  isChannelDoorAvailable(hosted!, hasHw) === true,
  "an omitted deployment flag is treated as supported — existing callers unchanged",
);
assert(
  /not set up on this deployment|isn't set up here/i.test(
    channelDoorUnavailableReason(hosted!, { deploymentSupported: false }),
  ),
  "the unavailable reason names the deployment, not a computer",
);
assert(
  /computer/i.test(channelDoorUnavailableReason({ ...hosted!, requiresHardware: true })),
  "the hardware reason is unchanged for a hardware door",
);

// ── Every state has copy, and no two states share a headline ──────────────

const allStates: HostedPairingState[] = [
  { kind: "checking" },
  { kind: "unsupported" },
  { kind: "unreachable" },
  { kind: "status_unknown", detail: "" },
  { kind: "idle" },
  { kind: "waiting", deepLink: null, code: "1", botUsername: "b", codeExpiresAt: null, codeExpired: false },
  { kind: "expired" },
  { kind: "connected", botUnreachable: false },
];
for (const state of allStates) {
  assert(hostedPairingHeadline(state).length > 0, `${state.kind} has a headline`);
}
const headlines = allStates.map(hostedPairingHeadline);
assert(
  new Set(headlines).size === headlines.length,
  "no two states share a headline — three facts are three messages",
);
assert(
  hostedPairingHeadline({ kind: "connected", botUnreachable: true }) !==
    hostedPairingHeadline({ kind: "connected", botUnreachable: false }),
  "a connection whose replies have stopped does not read as a healthy one",
);

// A canary: if CHANNEL_DOORS ever loses its Telegram entry entirely, every
// door assertion above would vacuously pass on `undefined`.
assert(
  Array.isArray(CHANNEL_DOORS.sage_telegram_hosted) && CHANNEL_DOORS.sage_telegram_hosted.length >= 2,
  "canary: the Telegram door list is really being read",
);

// --- Summary ---

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
