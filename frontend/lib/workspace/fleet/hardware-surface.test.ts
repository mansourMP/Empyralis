/**
 * The Hardware surface — its two rules, and the four defects that got past
 * every behavioural test this repo had.
 *
 * The SOURCE SCANS at the bottom are the half that matters. A behavioural
 * test can only exercise the states that exist today; what actually shipped
 * on these two pages was:
 *
 *   · a raw shell diagnostic rendered as customer copy, which the backend's
 *     own secret redactor then ate a file path out of ("[redacted-secret]"
 *     inside an ordinary ~/… path);
 *   · `plutil` / `launchctl` / `sudo tee` command lines printed at a
 *     customer;
 *   · a provider card that could only fail after the click;
 *   · two different facts ("could not check" and "nothing set up") sitting
 *     next to each other reading as one.
 *
 * Every one of those type-checks perfectly and renders without error, so
 * only a scan catches them coming back.
 *
 * Run: npx tsx lib/workspace/fleet/hardware-surface.test.ts
 */

import { readFileSync } from "node:fs";

import {
  cloudProviderUnavailableReason,
  hasUsableAddOption,
  planHardwareAddOptions,
  type CloudProviderInput,
} from "./hardware-add-options";
import {
  OLLAMA_MIN_MEMORY_GB,
  boxHasProblem,
  planBoxHealth,
  planBoxProblems,
  planChannelCapabilityRow,
  planCliCapabilityRow,
  planLocalModelCapabilityRow,
  planSandboxCapabilityRow,
  type CapabilityRow,
  type ProbeStatus,
} from "./hardware-detail-shape";
import { ALL_PROBE_STATUSES, planChannelTransportState } from "./box-capability-state";

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

/** Strip comments so a banned token DOCUMENTED in a doc comment does not
 *  trip its own tripwire — the same idiom connector-card-face.test.ts uses. */
function codeOnly(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

const PROVIDERS: CloudProviderInput[] = [
  { id: "digitalocean", label: "DigitalOcean", detail: "Simplest setup" },
  { id: "google", label: "Google Cloud", detail: "Your own GCP project" },
  { id: "aws", label: "AWS", detail: "No API keys" },
];

// ── SURFACE 1 — the ways in ───────────────────────────────────────────────

{
  const all = planHardwareAddOptions({ cloudProviders: PROVIDERS, providerAvailability: null });
  assert(all.length === 5, "three cloud providers, SSH, and this computer — every way in, on one row");
  assert(all.every((o) => o.state === "available"), "unknown availability fails OPEN — a failed check never breaks a working provider");
  assert(all.filter((o) => o.kind === "ssh").length === 1 && all.filter((o) => o.kind === "device").length === 1,
    "SSH and this-computer are each offered exactly once");
}

{
  // The live production shape: AWS deliberately unwired (CLAUDE.md's own
  // standing decision), Google unconfigured, DigitalOcean working.
  const options = planHardwareAddOptions({
    cloudProviders: PROVIDERS,
    providerAvailability: { digitalocean: true, google: false, aws: false },
  });
  const byKey = new Map(options.map((o) => [o.key, o]));
  assert(byKey.get("digitalocean")?.state === "available", "DigitalOcean has no operator dependency and stays available");
  assert(byKey.get("aws")?.state === "unavailable", "AWS is honestly unavailable rather than a button that 500s");
  assert(byKey.get("google")?.state === "unavailable", "…and so is Google Cloud when its own credentials are unset");
  assert(byKey.get("ssh")?.state === "available" && byKey.get("device")?.state === "available",
    "…while the two options with no operator dependency at all are untouched");

  const awsReason = byKey.get("aws")?.unavailableReason || "";
  assert(awsReason.length > 0, "an unavailable option always states a real reason");
  // A professional tool labels; it does not hand a customer the operator's
  // own env-var problem.
  assert(!/env|credential|token|secret|EMPYRALIS_|configure|API/i.test(awsReason),
    `the reason names the fact, not the mechanism — got "${awsReason}"`);
  assert(!/error|failed|broken/i.test(awsReason), "…and it is never phrased as an error");
  assert(byKey.get("digitalocean")?.unavailableReason === null, "an available option carries no reason at all");
}

{
  // An existing connection proves the operator side worked at least once —
  // never blocked by an availability miss. Same carve-out the setup modal
  // already makes, stated once here instead of twice.
  const options = planHardwareAddOptions({
    cloudProviders: PROVIDERS,
    providerAvailability: { google: false },
    connectedProviderIds: ["google"],
  });
  assert(options.find((o) => o.key === "google")?.state === "available",
    "a provider already connected is never taken away by an availability miss");
}

{
  const closed = planHardwareAddOptions({
    cloudProviders: PROVIDERS,
    providerAvailability: { digitalocean: false, google: false, aws: false },
  });
  assert(hasUsableAddOption(closed), "even with every cloud provider closed there is still a real way in");
}

assert(
  cloudProviderUnavailableReason("AWS").includes("AWS"),
  "the reason names the provider it is about",
);

// ── SURFACE 2 — question 1: is this computer working? ─────────────────────

const HEALTHY = {
  connectionTone: "online" as const,
  connectionLabel: "Online",
  dockerStatus: "ready" as ProbeStatus,
  fullAccessAuthorizedButOff: null,
  cannotReceiveUpdates: false,
  executionBlocked: false,
};

{
  const health = planBoxHealth(HEALTHY);
  assert(health.caveat === null, "a healthy box collapses to ONE line — no caveat, nothing else to render");
  assert(health.headline === "Online", "…and the headline is the word the pill already uses, not a second vocabulary");
}

{
  // Every unhealthy shape produces exactly ONE caveat, never a list glued
  // together — a person acts on the first thing that is wrong.
  const shapes = [
    { ...HEALTHY, dockerStatus: "missing" as ProbeStatus },
    { ...HEALTHY, dockerStatus: "offline" as ProbeStatus },
    { ...HEALTHY, cannotReceiveUpdates: true },
    { ...HEALTHY, fullAccessAuthorizedButOff: true },
    { ...HEALTHY, connectionTone: "offline" as const, connectionLabel: "Offline" },
    { ...HEALTHY, connectionTone: "unknown" as const, connectionLabel: "Unknown" },
  ];
  for (const shape of shapes) {
    const caveat = planBoxHealth(shape).caveat;
    assert(typeof caveat === "string" && caveat.length > 0, `${JSON.stringify(shape.dockerStatus)} produces a caveat`);
    assert(!/\band\b.*\band\b/i.test(caveat || ""), `the caveat is one fact, not a list — got "${caveat}"`);
  }
}

{
  // A box nobody can reach: the state IS the story. Naming Docker as well
  // would send someone to fix the wrong thing.
  const offline = planBoxHealth({ ...HEALTHY, connectionTone: "offline", connectionLabel: "Offline", dockerStatus: "missing" });
  assert(!/docker/i.test(offline.caveat || ""), "an unreachable box is not also reported as a Docker problem");
}

{
  // "could not tell" and "offline" are different facts and must not share
  // one sentence — they send a person to do two different things.
  const unknown = planBoxHealth({ ...HEALTHY, connectionTone: "unknown", connectionLabel: "Unknown" });
  const offline = planBoxHealth({ ...HEALTHY, connectionTone: "offline", connectionLabel: "Offline" });
  assert(unknown.caveat !== offline.caveat, "'hasn't reported in' never reads as 'is offline'");
}

// ── SURFACE 2 — question 2: what can it do? ──────────────────────────────

{
  const ready = planCliCapabilityRow("codex_cli", "Codex", "ready");
  assert(ready.healthy && ready.action === null && ready.note === null,
    "a signed-in CLI is one line with nothing to press");
  assert(planCliCapabilityRow("codex_cli", "Codex", "unauthenticated").action === "sign_in",
    "installed but not signed in offers exactly Sign in");
  assert(planCliCapabilityRow("codex_cli", "Codex", "missing").action === "install",
    "not installed offers exactly Install");
  assert(!planCliCapabilityRow("codex_cli", "Codex", "unauthenticated").healthy,
    "installed-but-not-signed-in is NOT healthy — signing in is what lets an agent use it");
}

{
  // EVERY probe status, `undefined` included — a status this module does not
  // branch on must still land somewhere honest rather than falling through.
  for (const status of ALL_PROBE_STATUSES) {
    const docker = planSandboxCapabilityRow(status);
    const ollama = planLocalModelCapabilityRow(status, 32_000_000_000);
    const channel = planChannelCapabilityRow(planChannelTransportState({ transportStatus: status, pluginsStatus: status }), true);
    for (const row of [docker, ollama, channel]) {
      assert(row.stateLabel.length > 0, `${row.key} always has a state word (status=${String(status)})`);
      assert(row.healthy || row.action !== null || row.note !== null,
        `${row.key} that is not healthy always says something (status=${String(status)})`);
    }
    // Docker and Ollama have NO install capability on the gateway
    // (desktop-permissions.ts exposes cli.install / cli.login.* /
    // openclaw.provision and nothing else), so a button here could only
    // ever fail.
    assert(docker.action === null, `Docker never offers a control that cannot work (status=${String(status)})`);
    assert(ollama.action === null, `…and neither does the local-model row (status=${String(status)})`);
  }
}

{
  // "Not installed" and "could not check" are different facts.
  assert(planSandboxCapabilityRow("missing").stateLabel !== planSandboxCapabilityRow(undefined).stateLabel,
    "Docker absent never reads the same as Docker unconfirmed");
  assert(planSandboxCapabilityRow("missing").note !== planSandboxCapabilityRow("offline").note,
    "…and 'not installed' never reads the same as 'installed but not running'");
}

{
  // The memory gate is about physics, not installation — a 1 GB box reading
  // "Not set up" beside Docker's own "Not set up" invites someone to try.
  const tiny = planLocalModelCapabilityRow("missing", 1_000_000_000);
  assert(tiny.stateLabel === "Too small", "a box that physically cannot run a model says so");
  assert(tiny.note?.includes(String(OLLAMA_MIN_MEMORY_GB)) === true, "…and names the bar it misses");
  const big = planLocalModelCapabilityRow("missing", 64_000_000_000);
  assert(big.stateLabel !== "Too small", "a big box that simply has no model is a different fact");
  const unknownMemory = planLocalModelCapabilityRow("missing", null);
  assert(unknownMemory.stateLabel !== "Too small", "unknown memory never becomes a claim about capacity");
}

{
  // The channel "Set up" button provisions against a specific agent's
  // policy. With no agent pinned there is nothing it could act on.
  const withTarget = planChannelCapabilityRow("set_up", true);
  const without = planChannelCapabilityRow("set_up", false);
  assert(withTarget.action === "set_up", "Set up is offered when there is an agent to set up for");
  assert(without.action === null && without.note !== null,
    "…and is not rendered at all when there is not, with the reason said instead");
  assert(planChannelCapabilityRow("unknown", true).action === null,
    "an unconfirmed transport offers no Set up — re-running setup against an unknown is not the honest next step");
}

// ── SURFACE 2 — question 3: what's wrong? ────────────────────────────────

const HEALTHY_CAPS: CapabilityRow[] = [
  planCliCapabilityRow("codex_cli", "Codex", "ready"),
  planSandboxCapabilityRow("ready"),
  planLocalModelCapabilityRow("ready", 32_000_000_000),
  planChannelCapabilityRow("ready", true),
];

{
  const problems = planBoxProblems({ ...HEALTHY, updateRefusalReason: null });
  assert(problems.length === 0, "a healthy box has NO problems section at all — it is absent, not empty-stated");
  assert(!boxHasProblem(problems, HEALTHY_CAPS), "…and nothing raises the section either");
}

{
  // MEASURED LIVE, and the reason this rule changed: the version that
  // enumerated capability problems here printed the identical three
  // sentences twice on one screen — once beside the capability, once below.
  const problems = planBoxProblems({ ...HEALTHY, dockerStatus: "offline", updateRefusalReason: null });
  const dockerRow = planSandboxCapabilityRow("offline");
  assert(problems.length === 0, "a capability's own problem is stated in its row, never repeated below it");
  assert(boxHasProblem(problems, [dockerRow]), "…but it still raises the section, so diagnostics and repair are reachable");
  assert(!problems.some((p) => p.title === dockerRow.note), "…and its exact sentence never appears twice");
}

{
  // An UNFINISHED capability is not a fault. Letting "not signed in" or
  // "no local model" raise this section would put "What's wrong" on nearly
  // every box in the product and teach people to ignore it.
  const unfinished: CapabilityRow[] = [
    planCliCapabilityRow("codex_cli", "Codex", "missing"),
    planCliCapabilityRow("cursor_cli", "Cursor CLI", "unauthenticated"),
    planLocalModelCapabilityRow("missing", 32_000_000_000),
    planChannelCapabilityRow("set_up", true),
  ];
  assert(unfinished.every((c) => !c.problem), "an unfinished capability is never a fault");
  assert(!boxHasProblem([], unfinished), "…and a box that is merely unfinished has nothing wrong with it");
  assert(planSandboxCapabilityRow("offline").problem, "Docker being down IS a fault — agents can run nothing");
  assert(planSandboxCapabilityRow("missing").problem, "…and so is Docker being absent");
}

{
  const problems = planBoxProblems({
    ...HEALTHY,
    connectionTone: "offline",
    connectionLabel: "Offline",
    cannotReceiveUpdates: true,
    updateRefusalReason: "Its launcher points at a fixed path.",
  });
  assert(problems.length >= 2, "facts with no capability row of their own are enumerated here");
  assert(new Set(problems.map((p) => p.key)).size === problems.length, "…each with its own key, never merged");
  assert(problems.some((p) => p.detail === "Its launcher points at a fixed path."),
    "the backend's own refusal reason is passed through rather than replaced with a guess");
}

{
  // FOUND LIVE: the pill read "Degraded" while the line under it read
  // "Nothing needs your attention on this computer" — two facts
  // contradicting each other on one screen.
  const degraded = planBoxHealth({ ...HEALTHY, connectionTone: "degraded", connectionLabel: "Degraded" });
  assert(degraded.caveat !== null, "a degraded connection never renders as 'nothing needs your attention'");
  const blocked = planBoxHealth({
    ...HEALTHY,
    connectionTone: "degraded",
    connectionLabel: "Online — tools unavailable",
    executionBlocked: true,
  });
  assert(blocked.caveat !== degraded.caveat, "…and 'reachable but tools blocked' is its own fact");
  // Keyed on a stable status CODE, never the pill's prose — matching a
  // reworded sentence is a failure this codebase has already paid for.
  const real = planBoxHealth({ ...HEALTHY, connectionTone: "degraded", connectionLabel: "Degraded", dockerStatus: "offline" });
  assert(/docker/i.test(real.caveat || ""), "a real cause still outranks the generic degraded line");
}

// ── SOURCE SCANS — the half a behavioural test cannot do ─────────────────

const listSource = readFileSync(new URL("./HardwareSection.tsx", import.meta.url), "utf8");
const listCode = codeOnly(listSource);
const detailSource = readFileSync(
  new URL("../../../app/(account)/w/[workspaceId]/hardware/[gatewayId]/page.tsx", import.meta.url),
  "utf8",
);
const detailCode = codeOnly(detailSource);
const pageSource = readFileSync(
  new URL("../../../app/(account)/w/[workspaceId]/hardware/page.tsx", import.meta.url),
  "utf8",
);

assert(listCode.length > 2000 && detailCode.length > 2000, "CANARY: both real sources were read, comments stripped");
assert(/fleet-list-row/.test(listCode), "CANARY: the list page still renders rows");
assert(/fleet-hw-dashboard/.test(detailCode), "CANARY: the detail page still renders its dashboard");

// The rules are actually WIRED — this repo's most common defect is complete,
// correct, tested code with zero callers.
assert(/planHardwareAddOptions\(/.test(listCode), "the list page calls the real add-options rule");
assert(/planBoxHealth\(/.test(detailCode), "the detail page calls the real health rule");
assert(/planBoxProblems\(/.test(detailCode) && /boxHasProblem\(/.test(detailCode), "…and the real problems rule");
assert(/planCliCapabilityRow\(/.test(detailCode) && /planSandboxCapabilityRow\(/.test(detailCode),
  "…and builds its capability rows from the module rather than inline branches");

// NO COMMAND IS EVER SHOWN TO A CUSTOMER on the detail page. `plutil`,
// `launchctl kickstart`, `sudo tee` and a systemd unit path were all
// printed here. The ONE exception is the launch-repair block, whose
// commands come from the BACKEND and which exists precisely because the
// gateway runs unprivileged in a read-only mount namespace and no button
// could ever work — that one is `launchRepair.commands`, a value, not a
// literal typed into this file.
for (const banned of ["plutil", "launchctl", "sudo tee", "systemctl restart", "LaunchAgents", "setup-token", "agent_computer.sh"]) {
  assert(!detailCode.includes(banned), `no "${banned}" command line is printed at a customer`);
}
assert(/GatewayLaunchRepairRow/.test(detailCode) && /commands\.join/.test(detailCode),
  "CANARY: the backend-supplied repair is still reachable — through the clipboard, not a wall of shell");
// The repair is the one thing on this page Empyralis cannot do for the
// customer. It is handed over as a sentence plus a copy button; a <pre>
// block of four launchctl lines is the wall this pass removed.
assert(!/<pre/.test(detailCode), "no block of shell is printed on the page at all");

// A RAW PROBE SUMMARY IS NEVER RENDERED. This is what leaked
// "[redacted-secret]" into an ordinary file path — the backend redacts every
// visible string, and its entropy sweep eats a long path. Fixed by not
// choosing to display the dump, never by weakening the redactor.
assert(!/item\?\.summary|item\.summary|\.summary \|\|/.test(detailCode),
  "no service_inventory summary is rendered as customer copy");
assert(!/dockerNotReadyReason|serviceItemPresentation/.test(detailCode),
  "…and the two helpers that used to thread one through are gone");

// The header the founder rejected: a paragraph, and three stat tiles two of
// which are not properties of hardware at all.
assert(!/fleet-status-strip/.test(listCode), "the channel/connector stat tiles are gone from Hardware");
assert(!/useWorkspaceStatusStrip/.test(listCode), "…and so is the hook that fed them");
assert(!/connected over SSH\. Set up once/.test(pageSource + listSource),
  "the paragraph above the page is gone — a professional tool labels, it does not lecture");

// Availability reaches the page BEFORE the click, not only inside the modal.
assert(/provider-availability/.test(listCode), "the list page reads real provider availability");
assert(!/fleet-provider-card"[\s\S]{0,400}disabled/.test(listCode),
  "an unavailable provider is an inert card, never a disabled button that reads as broken");

// One accent-filled button per view. Cursor CLI's Sign in was accent-filled
// in a list of four identical rows.
const accentFills = (detailCode.match(/fleet-btn--accent-fill/g) || []).length;
assert(accentFills === 0, `the detail page spends no accent fill on a row action (found ${accentFills})`);

assert(codeOnly("// plutil x\nconst a = 1;").includes("plutil") === false, "CANARY: the scan ignores a banned token in a comment");
assert(codeOnly('const a = "plutil";').includes("plutil"), "CANARY: …and still sees one in real code");

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
