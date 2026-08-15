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
 *   - a channel the owner has NOT set up renders OFF, carries no policy, and
 *     therefore produces none of the audit findings that used to refuse the
 *     whole box;
 *   - the DM policy/allowlist keys this build would write match the ones each
 *     channel PLUGIN states for itself (`channels capabilities --json`), which
 *     is a different source from the config schema they are derived from;
 *   - drift (an out-of-band edit to the generated config) is DETECTED, named,
 *     and corrected on the next run.
 *
 * COST: the channels below are marked as set up by their owner, which is what
 * production sends for a channel somebody actually connected — so this script
 * now also exercises the CHANNEL PLUGIN INSTALL path, and a first run against
 * a brand-new profile fetches those packages (minutes, and it needs the npm
 * registry). Re-runs against the same profile install nothing.
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
    installPlugin: true,
    dmPolicy: { mode: "allowlist", allowlist: ["ou_owner", "ou_teammate"] },
    // require_mention OFF with an explicit chat allowlist: the case that needs
    // per-chat `groups` entries and must never use a `"*"` wildcard.
    groupPolicy: { mode: "allowlist", allowlist: ["oc_group_a"], requireMention: false },
  },
  {
    channelId: "line",
    enabled: true,
    installPlugin: true,
    dmPolicy: { mode: "pairing", allowlist: [] },
    groupPolicy: { mode: "open", allowlist: [], requireMention: false },
  },
  {
    channelId: "qqbot",
    enabled: true,
    installPlugin: true,
    dmPolicy: { mode: "owner_only", allowlist: [] },
    groupPolicy: { mode: "allowlist", allowlist: [], requireMention: true },
  },
  {
    // THE UNMAPPABLE CASE. OpenClaw's zalo channel has neither a channel-level
    // requireMention nor a per-group map, and their default is TRUE, so
    // "reply without being mentioned" cannot be carried. Must fail CLOSED.
    channelId: "zalo",
    enabled: true,
    installPlugin: true,
    dmPolicy: { mode: "open", allowlist: [] },
    groupPolicy: { mode: "open", allowlist: [], requireMention: false },
  },
  {
    channelId: "msteams",
    enabled: true,
    installPlugin: true,
    dmPolicy: { mode: "allowlist", allowlist: ["a@example.com"] },
    groupPolicy: { mode: "open", allowlist: [], requireMention: true },
  },
  // ── The channels this script used to skip, and the cost of skipping them ──
  //
  // The five above all declare `dmPolicy` AND `groupPolicy`, so every run of
  // this script was green while `openclaw config patch` refused the config the
  // product actually generated:
  //
  //   channels.clickclack: invalid config:
  //   must not have additional properties: "groupPolicy", "dmPolicy"
  //
  // and because the push is all-or-nothing, that refused EVERY channel on
  // EVERY box — Telegram included. A live proof that exercises only the
  // uniform channels proves the uniform channels.
  {
    // NEITHER policy field. `allowFrom` is its only lever.
    channelId: "clickclack",
    enabled: true,
    installPlugin: true,
    dmPolicy: { mode: "allowlist", allowlist: ["u_owner"] },
    groupPolicy: { mode: "open", allowlist: [], requireMention: true },
  },
  {
    // groupPolicy but NO dmPolicy and NO top-level allowFrom — it moved both
    // under a nested `dm` object this generator does not write.
    channelId: "matrix",
    enabled: true,
    installPlugin: true,
    dmPolicy: { mode: "open", allowlist: [] },
    groupPolicy: { mode: "allowlist", allowlist: ["!room:example.org"], requireMention: true },
  },
  {
    // An `anyOf` whose every branch requires a credential with no default, so
    // there is no document Empyralis can write for it — not even
    // `{enabled: false}`, and not even `{}`, which is a present object and is
    // validated like any other.
    channelId: "twitch",
    enabled: true,
    installPlugin: true,
    dmPolicy: { mode: "open", allowlist: [] },
    groupPolicy: { mode: "open", allowlist: [], requireMention: true },
  },
  {
    // NO `channels.<id>` NAMESPACE AT ALL on a box without its plugin, so the
    // ID is refused rather than its contents — `unknown channel id`, a
    // different refusal from every case above and equally fatal to the whole
    // push. This is the one the previous pass shipped: the generator wrote
    // `{enabled: false}` for it, believing that was the safe minimum.
    channelId: "openclaw-weixin",
    enabled: true,
    installPlugin: true,
    dmPolicy: { mode: "open", allowlist: [] },
    groupPolicy: { mode: "open", allowlist: [], requireMention: true },
  },
  {
    // THE CHANNEL NOBODY ASKED FOR — and the reason this entry is permanent.
    //
    // Its node is the one PERMISSIVE one in the pinned build, so it swallowed
    // `allowFrom: ["*"]` without complaint while its real sender list
    // (`allowedUserIds`) stayed empty; its own plugin then reported the
    // resulting admit-nobody state to `openclaw security audit` as CRITICAL,
    // and the all-or-nothing push turned that into a total refusal — measured
    // live, on the founder's Mac, with a real Telegram bot token sitting in
    // the config the refusal threw away:
    //
    //   openclaw_security_audit_not_clean
    //   channels.synology-chat.warning.3 [critical]
    //
    // `installPlugin: false` is the whole point: nobody has set this channel
    // up, so it must render OFF, and an OFF channel is one their audit skips
    // entirely (`if (!enabled) continue`, audit-channel.collect.runtime).
    // Both halves are asserted after RUN 1.
    channelId: "synology-chat",
    enabled: true,
    installPlugin: false,
    dmPolicy: { mode: "open", allowlist: [] },
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

// ── A channel nobody set up is OFF, and its audit findings are gone ────────
{
  const cli = new OpenClawCli({ profile, env: process.env });
  const read = await cli.run(["config", "get", "channels", "--json"], { timeoutMs: 30_000 });
  const effective = read.code === 0 ? JSON.parse(read.stdout) : {};
  const synology = effective["synology-chat"] ?? {};
  console.log(`\n── the untouched channel ──\n  channels.synology-chat  ${JSON.stringify(synology)}`);
  if (synology.enabled !== false) {
    console.error("\nFAIL: a channel the owner never set up was left switched on.");
    process.exit(1);
  }
  // THE WRITE THAT PRODUCED THE CRITICAL, checked where it can actually be
  // observed. `openclaw config patch` MERGES and never deletes, so a value a
  // PREVIOUS build wrote stays in the effective config forever — asserting its
  // absence here would fail on residue rather than on behaviour. What this
  // build would write is decided by the DM surface it derives from the live
  // schema, so that is what gets asserted; the residue is reported instead,
  // because a reader of this output should know it is still on the box.
  if (Object.hasOwn(synology, "allowFrom")) {
    console.log(
      `  NOTE: channels.synology-chat.allowFrom is still ${JSON.stringify(synology.allowFrom)} — left by an ` +
        "earlier build. `config patch` merges and never deletes, and the channel is off, so it authorizes nothing.",
    );
  }
  const synologyFindings = first.auditFindings.filter((f) => f.checkId.startsWith("channels.synology-chat."));
  if (synologyFindings.length > 0) {
    console.error(`\nFAIL: synology-chat still produces audit findings: ${JSON.stringify(synologyFindings)}`);
    process.exit(1);
  }
  if (!first.disabledChannels.some((f) => f.channelId === "synology-chat" && f.code === "channel_not_set_up_by_owner")) {
    console.error("\nFAIL: the channel was switched off without saying so.");
    process.exit(1);
  }
}

// ── Our DM key derivation vs OPENCLAW'S OWN answer ────────────────────────
//
// The derivation reads the installed CONFIG SCHEMA. This reads the same
// question off PLUGIN METADATA — `channels capabilities --json` makes each
// plugin state its own `setupWizard.dmPolicy.policyKey` / `.allowFromKey` as
// fully-qualified config paths. Two independent sources, which is the only
// thing that makes this more than the generator agreeing with itself: a
// derivation checked against the document it was derived from can only ever
// confirm itself (CLAUDE.md's own standing rule).
//
// Silent on a channel whose plugin does not state the paths — that is a gap in
// their metadata, not a disagreement, and a check that treated "they didn't
// say" as "we're wrong" would fail on nine channels forever.
{
  const cli = new OpenClawCli({ profile, env: process.env });
  const { resolveOpenClawChannelKeySupport } = require("../dist/openclaw/provisioning/openclaw-channel-shapes.js");
  const schema = await cli.configSchema();
  const ids = channels.map((channel) => channel.channelId);
  const derived = new Map(resolveOpenClawChannelKeySupport(schema, ids).map((entry) => [entry.channelId, entry.dm]));

  // The permissive node, against the LIVE schema rather than the fixture: it
  // declares no sender list, so this build has no key to write there. This is
  // the fix-1 half, and it is checked here because a residual value from an
  // earlier build makes the effective config unable to answer it.
  const synologyDm = derived.get("synology-chat");
  if (synologyDm && synologyDm.allowlistPath !== null) {
    console.error(
      `\nFAIL: this build would write channels.synology-chat.${synologyDm.allowlistPath.join(".")} — a key that ` +
        "node does not declare. That write is accepted and never read, which is what refused the whole box.",
    );
    process.exit(1);
  }

  const listed = await cli.run(["channels", "capabilities", "--json"], { timeoutMs: 60_000 });
  const reported = listed.code === 0 ? JSON.parse(listed.stdout).channels ?? [] : [];
  let compared = 0;
  for (const entry of reported) {
    const channelId = entry?.plugin?.id;
    const stated = entry?.plugin?.setupWizard?.dmPolicy;
    if (!channelId || !stated || !derived.has(channelId)) continue;
    const ours = derived.get(channelId);
    const oursPolicy = ours.policyPath ? `channels.${channelId}.${ours.policyPath.join(".")}` : null;
    const oursAllow = ours.allowlistPath ? `channels.${channelId}.${ours.allowlistPath.join(".")}` : null;
    console.log(`  dm keys  ${channelId.padEnd(16)} openclaw=${stated.policyKey} / ${stated.allowFromKey}`);
    if (oursPolicy !== stated.policyKey || oursAllow !== stated.allowFromKey) {
      console.error(
        `\nFAIL: ${channelId} keeps its DM policy at ${stated.policyKey} / ${stated.allowFromKey}, ` +
          `and this build would write ${oursPolicy} / ${oursAllow}.`,
      );
      process.exit(1);
    }
    compared += 1;
  }
  console.log(`  dm keys  cross-checked ${compared} channel(s) against OpenClaw's own plugin metadata`);
  if (compared === 0) {
    console.error("\nFAIL: no channel stated its own DM key paths, so this check asserted nothing.");
    process.exit(1);
  }
}

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
