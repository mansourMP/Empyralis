#!/usr/bin/env node
/**
 * CHANNEL-ADOPTION-PLAN.md step 4's live proof: runs the REAL
 * OpenClawProvisioner (compiled from src/openclaw/provisioning/) against a
 * REAL `openclaw` install, under an ISOLATED throwaway profile, and prints
 * exactly what it did.
 *
 * WHAT THIS PROVES
 *   - the version pin passes only against openclaw@2026.6.10;
 *   - the transcribed channel-policy shapes still match the INSTALLED schema;
 *   - the generated config is accepted by `openclaw config patch`;
 *   - the mandatory lockdown holds in the EFFECTIVE config read back out of
 *     OpenClaw (loopback, token auth, mDNS off, control UI off, no model
 *     provider credential, no audit suppressions);
 *   - the owner's policy is actually in force, path by path;
 *   - `openclaw security audit` is clean;
 *   - drift (an out-of-band edit to the generated config) is DETECTED, named,
 *     and corrected on the next run.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 *   - it never registers a launchd/systemd job on the machine running it. The
 *     unit CONTENT is rendered and written under a scratch home so it can be
 *     inspected, with a no-op registrar. Bootstrapping a real background job
 *     onto a developer's Mac as a side effect of a verification script is not
 *     something a verification script gets to decide.
 *
 * SAFETY: `--profile` is mandatory and defaults to a throwaway name. It must
 * NEVER be pointed at `~/.openclaw` or `~/.openclaw-dev` — those are real
 * profiles. Tear down with `rm -rf ~/.openclaw-<profile>`.
 *
 *   node scripts/verify-openclaw-provisioning.mjs [--profile <name>] [--keep]
 */
import { mkdtempSync, readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";

const require = createRequire(import.meta.url);
const { OpenClawCli } = require("../dist/openclaw/provisioning/openclaw-cli.js");
const { OpenClawProvisioner } = require("../dist/openclaw/provisioning/openclaw-provisioner.js");
const { OPENCLAW_PINNED_VERSION } = require("../dist/openclaw/provisioning/openclaw-version.js");

const argv = process.argv.slice(2);
const profileIndex = argv.indexOf("--profile");
const profile = profileIndex >= 0 ? argv[profileIndex + 1] : "empyralis-prov-live";
if (["", "dev", "default"].includes(profile)) {
  console.error(`Refusing to verify against profile "${profile}".`);
  process.exit(1);
}

const gatewayPort = Number.parseInt(process.env.VERIFY_OPENCLAW_PORT || "19499", 10);
const gatewayToken = process.env.VERIFY_OPENCLAW_TOKEN || "verify-openclaw-gateway-token-0123456789abcdef";
const bridgeToken = process.env.VERIFY_BRIDGE_TOKEN || "verify-bridge-secret";
const bridgePort = Number.parseInt(process.env.VERIFY_BRIDGE_PORT || "8794", 10);
const repoRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const bridgePluginPath = path.join(repoRoot, "openclaw-bridge-plugin");

// Scratch home: the launchd plist lands here instead of ~/Library/LaunchAgents.
const scratchHome = mkdtempSync(path.join(os.tmpdir(), "empyralis-openclaw-verify-home-"));
const stateDir = mkdtempSync(path.join(os.tmpdir(), "empyralis-openclaw-verify-state-"));

/** The policy set the cloud would send. Chosen to exercise every branch of the
 *  authority table in ONE run, including the unmappable one. */
const channels = [
  {
    channelId: "feishu",
    enabled: true,
    dmPolicy: { mode: "allowlist", allowlist: ["ou_owner", "ou_teammate"] },
    // require_mention OFF with an explicit chat allowlist: the case that needs
    // per-chat `groups` entries and must never use a `"*"` wildcard.
    groupPolicy: { mode: "allowlist", allowlist: ["oc_group_a"], requireMention: false },
  },
  {
    channelId: "line",
    enabled: true,
    dmPolicy: { mode: "pairing", allowlist: [] },
    groupPolicy: { mode: "open", allowlist: [], requireMention: false },
  },
  {
    channelId: "qqbot",
    enabled: true,
    dmPolicy: { mode: "owner_only", allowlist: [] },
    groupPolicy: { mode: "allowlist", allowlist: [], requireMention: true },
  },
  {
    // THE UNMAPPABLE CASE. OpenClaw's zalo channel has neither a channel-level
    // requireMention nor a per-group map, and their default is TRUE, so
    // "reply without being mentioned" cannot be carried. Must fail CLOSED.
    channelId: "zalo",
    enabled: true,
    dmPolicy: { mode: "open", allowlist: [] },
    groupPolicy: { mode: "open", allowlist: [], requireMention: false },
  },
  {
    channelId: "msteams",
    enabled: true,
    dmPolicy: { mode: "allowlist", allowlist: ["a@example.com"] },
    groupPolicy: { mode: "open", allowlist: [], requireMention: true },
  },
];

function buildProvisioner() {
  const cli = new OpenClawCli({ profile, env: process.env });
  return new OpenClawProvisioner({
    plan: {
      profile,
      gatewayPort,
      profileStateDir: path.join(scratchHome, `.openclaw-${profile}`),
      bridgePluginPath,
      channels,
      // --with-synthetic-inbound loads step 1-3's synthetic driver alongside
      // the bridge plugin, so the merged inbound path can be exercised
      // against THIS provisioned instance without real channel credentials
      // (which are step 5). Never set in a production plan.
      extraPluginPaths: argv.includes("--with-synthetic-inbound")
        ? [path.join(bridgePluginPath, "verify", "synthetic-inbound-driver")]
        : undefined,
    },
    secrets: { gatewayToken },
    bridgeToken,
    bridgeEndpointUrl: `http://127.0.0.1:${bridgePort}/openclaw/inbound`,
    cli,
    env: process.env,
    platform: process.platform,
    // Scratch home so the supervisor unit is rendered somewhere inspectable
    // rather than installed into the real ~/Library/LaunchAgents.
    homeDir: scratchHome,
    stateDir,
    binaryPath: process.env.VERIFY_OPENCLAW_BINARY || "openclaw",
    registerJob: async () => undefined,
    record: async (type, payload) => {
      console.log(`  journal  ${type} ${JSON.stringify(payload)}`);
    },
  });
}

function summarize(label, result) {
  console.log(`\n── ${label} ──`);
  console.log(`  status                ${result.status}`);
  if (result.refusal) console.log(`  refusal               ${result.refusal.code}: ${result.refusal.detail}`);
  console.log(`  openclaw version      ${result.version.observed} (pinned ${OPENCLAW_PINNED_VERSION})`);
  console.log(`  fingerprint           ${result.configFingerprint}`);
  console.log(`  previous fingerprint  ${result.previousConfigFingerprint ?? "(none)"}`);
  console.log(`  config changed        ${result.configChanged}`);
  console.log(`  drifted paths         ${result.driftedPaths.length ? result.driftedPaths.join(", ") : "(none)"}`);
  console.log(`  lockdown violations   ${result.lockdownViolations.length ? JSON.stringify(result.lockdownViolations) : "(none)"}`);
  console.log(`  audit findings        ${result.auditFindings.length ? result.auditFindings.map((f) => `${f.checkId}[${f.severity}]`).join(", ") : "(none)"}`);
  console.log(`  stripped cred env     ${result.strippedCredentialEnvNames.join(", ") || "(none present)"}`);
  for (const finding of result.disabledChannels) {
    console.log(`  DISABLED ${finding.channelId}: ${finding.code}`);
    console.log(`           ${finding.detail}`);
  }
  for (const finding of result.widenings) {
    console.log(`  WIDENED  ${finding.channelId}: ${finding.code}`);
  }
  if (result.supervisor) {
    console.log(`  supervisor            ${result.supervisor.fileState} -> ${result.supervisor.repair?.action}`);
    console.log(`  unit path             ${result.supervisor.definition?.unitPath}`);
  }
}

const first = await buildProvisioner().provision();
summarize("RUN 1 — first provisioning", first);
if (first.status !== "provisioned") process.exit(1);

// Show the unit that WOULD be installed, verbatim.
const unitPath = first.supervisor?.definition?.unitPath;
if (unitPath && existsSync(unitPath)) {
  console.log(`\n── supervisor unit (${unitPath}) ──`);
  console.log(readFileSync(unitPath, "utf8"));
}

// ── Drift: change the config out from under us, exactly as OpenClaw's own
// in-chat `/activation always` command would, then re-provision. ───────────
console.log("\n── introducing drift out-of-band (as OpenClaw's own /activation would) ──");
const cli = new OpenClawCli({ profile, env: process.env });
const driftPatch = path.join(stateDir, "drift.json");
const { writeFileSync } = await import("node:fs");
writeFileSync(
  driftPatch,
  JSON.stringify({ channels: { feishu: { requireMention: true } }, discovery: { mdns: { mode: "minimal" } } }),
);
const patched = await cli.configPatch(driftPatch);
console.log(`  config patch exit=${patched.code} ${patched.stdout.trim()}`);

const second = await buildProvisioner().provision();
summarize("RUN 2 — after out-of-band drift", second);
if (second.status !== "provisioned") process.exit(1);
if (second.driftedPaths.length === 0) {
  console.error("\nFAIL: drift was introduced but not detected.");
  process.exit(1);
}

const third = await buildProvisioner().provision();
summarize("RUN 3 — idempotent re-run", third);
if (third.driftedPaths.length !== 0) {
  console.error("\nFAIL: a clean re-run reported drift; the generator is not deterministic.");
  process.exit(1);
}
if (third.configFingerprint !== first.configFingerprint) {
  console.error("\nFAIL: the same policy produced a different fingerprint.");
  process.exit(1);
}

console.log("\nOK — provisioned, drift detected and corrected, re-run is a no-op.");
console.log(`Profile state dir: ${path.join(os.homedir(), `.openclaw-${profile}`)}`);
console.log(`Scratch home (unit): ${scratchHome}`);
console.log(`Scratch gateway state: ${stateDir}`);
if (!argv.includes("--keep")) {
  console.log(`\nTear down with:\n  rm -rf ~/.openclaw-${profile} ${scratchHome} ${stateDir}`);
}
