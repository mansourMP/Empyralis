#!/usr/bin/env node
/**
 * Regenerates src/__tests__/fixtures/openclaw-config-schema.channels.json from
 * the OpenClaw actually installed on this machine.
 *
 * Run this ONLY when moving the version pin
 * (src/openclaw/provisioning/openclaw-version.ts), and read the resulting diff:
 * a change in this fixture is a change in what an owner's policy MEANS. The
 * test that consumes it (openclaw-provisioning.test.ts) is what turns an
 * upstream schema change from a silent behaviour change into a failing build.
 *
 * The full `openclaw config schema` document is ~2.5MB; only the
 * policy-bearing properties are kept.
 *
 *   node scripts/refresh-openclaw-schema-fixture.mjs [--profile <name>]
 *
 * The profile is only used to scope the CLI call; nothing is written to it.
 */
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, "..", "src", "__tests__", "fixtures", "openclaw-config-schema.channels.json");
const MANIFEST = path.join(HERE, "..", "..", "server_modules", "openclaw_channel_manifest.json");

/**
 * Every channel the pinned OpenClaw declares a config node for — read out of
 * the generated channel manifest, never typed here.
 *
 * This used to be `["feishu","line","qqbot","zalo","msteams","whatsapp"]` with
 * a comment saying it must match OPENCLAW_TRANSPORT_CHANNEL_IDS. That was a
 * fourth hand-maintained copy of the channel set, and the one furthest from
 * anybody's attention: a fixture that silently stops covering a channel does
 * not fail, it just stops checking it — which is worse than failing.
 *
 * WhatsApp is now included by construction rather than as a named exception:
 * it is in the manifest like everything else, and it remains the one channel
 * that suppresses the inbound plugin hook, so the hook audit's regression test
 * still has a real example of that shape.
 */
const manifest = JSON.parse(readFileSync(MANIFEST, "utf8"));
const CHANNELS = manifest.channels
  .filter((channel) => channel.config_schema_present)
  .map((channel) => channel.id);
if (CHANNELS.length === 0) {
  throw new Error(
    "The generated channel manifest declares no channel with a config schema node. Run " +
      "`python3 scripts/generate_openclaw_channel_manifest.py` first — an empty fixture " +
      "asserts nothing and passes.",
  );
}
const POLICY_PROPERTIES = [
  "dmPolicy",
  // The NESTED direct-message surface. `channels.matrix` and
  // `channels.googlechat` keep `policy` and `allowFrom` inside a `dm` object
  // instead of at the top level, and a fixture that drops it cannot tell
  // "this channel has no direct-message policy" (which is what the generator
  // used to conclude about both of them) from "it has one, one level down".
  "dm",
  "groupPolicy",
  "requireMention",
  "groups",
  "teams",
  "configWrites",
  "pluginHooks",
  "allowFrom",
  "groupAllowFrom",
  // `enabled` is policy-bearing in the only sense that matters here: it is a
  // key the generator WRITES, and `openclaw config patch` refuses a key the
  // node does not declare. A fixture that drops it cannot express the
  // difference between "this channel can be switched on" and "writing to this
  // channel takes the whole box's provisioning down".
  "enabled",
];

// A throwaway HOME rather than `--profile`: a profile still lands under the
// operator's own home (`~/.openclaw-<profile>`), and reading a schema is no
// reason to write anything into a real OpenClaw tree. Same isolation the
// Python manifest generator uses.
const scratchHome = mkdtempSync(path.join(tmpdir(), "empyralis-openclaw-fixture-"));
const env = { ...process.env, HOME: scratchHome };
delete env.OPENCLAW_CONFIG_DIR;

// Their CLI prints either a bare semver-ish line or the "OpenClaw 2026.6.10
// (aa69b12)" banner depending on how it is invoked; the fixture records the
// version only, same shape parseOpenClawVersion() accepts.
const versionOutput = execFileSync("openclaw", ["--version"], { encoding: "utf8", env }).trim();
const versionMatch = versionOutput.match(/\b(\d{4}\.\d{1,2}\.\d{1,3})\b/);
if (!versionMatch) throw new Error(`Could not parse an OpenClaw version out of: ${versionOutput}`);
const version = versionMatch[1];
const schema = JSON.parse(
  execFileSync("openclaw", ["config", "schema"], {
    encoding: "utf8",
    env,
    maxBuffer: 64 * 1024 * 1024,
  }),
);
rmSync(scratchHome, { recursive: true, force: true });

/**
 * Prunes one node to the policy-bearing properties, keeping the two structural
 * facts that decide whether a write is ACCEPTED at all:
 *
 *   additionalProperties  `false` on almost every channel node, so an
 *                         undeclared key refuses the patch — and because the
 *                         push is all-or-nothing, refuses every channel on the
 *                         box. `synology-chat` is the one that is permissive.
 *   anyOf / oneOf         `twitch` declares a credential branch and an
 *                         `accounts` branch. Dropping them left the fixture
 *                         claiming that channel declares nothing at all.
 */
function prune(node) {
  const kept = {};
  // `required` names the third structural fact, and it needs its properties
  // kept alongside it or the fixture cannot express the difference that
  // matters: thirteen nodes declare a `required` property and validate anyway
  // because each one carries a `default`, while `twitch` demands credentials
  // that have none and is refused. Dropping the property would make every
  // `required` look unsatisfiable and every channel look unconfigurable.
  const required = Array.isArray(node.required) ? node.required : [];
  for (const key of [...POLICY_PROPERTIES, ...required]) {
    if (Object.hasOwn(node.properties ?? {}, key)) kept[key] = node.properties[key];
  }
  const pruned = { type: node.type ?? "object", properties: kept };
  if (required.length > 0) pruned.required = [...required];
  if (Object.hasOwn(node, "additionalProperties")) pruned.additionalProperties = node.additionalProperties;
  for (const branchKey of ["anyOf", "oneOf"]) {
    if (Array.isArray(node[branchKey])) pruned[branchKey] = node[branchKey].map(prune);
  }
  return pruned;
}

const source = schema.properties.channels.properties;
const properties = {};
for (const channel of CHANNELS) {
  const node = source[channel];
  if (!node) throw new Error(`channels.${channel} is missing from the installed schema.`);
  properties[channel] = prune(node);
}

mkdirSync(path.dirname(OUT), { recursive: true });
writeFileSync(
  OUT,
  `${JSON.stringify(
    {
      $comment:
        "Pruned fixture of `openclaw config schema` from openclaw@" +
        version +
        " (the pinned build). Only the policy-bearing properties are kept — the full document is ~2.5MB. " +
        "Regenerate with scripts/refresh-openclaw-schema-fixture.mjs when the pin moves.",
      openclawVersion: version,
      type: "object",
      properties: { channels: { type: "object", properties } },
    },
    Object.keys({}).length === 0 ? sortKeys : undefined,
    2,
  )}\n`,
);

function sortKeys(_key, value) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, value[key]]));
  }
  return value;
}

console.log(`wrote ${OUT} from openclaw ${version}`);
