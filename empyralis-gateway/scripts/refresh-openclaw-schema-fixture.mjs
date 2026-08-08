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
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, "..", "src", "__tests__", "fixtures", "openclaw-config-schema.channels.json");

/** Must match OPENCLAW_TRANSPORT_CHANNEL_IDS, plus whatsapp — kept because it
 *  is the one channel that suppresses the inbound plugin hook, and the hook
 *  audit's regression test needs a real example of that shape. */
const CHANNELS = ["feishu", "line", "qqbot", "zalo", "msteams", "whatsapp"];
const POLICY_PROPERTIES = [
  "dmPolicy",
  "groupPolicy",
  "requireMention",
  "groups",
  "teams",
  "configWrites",
  "pluginHooks",
  "allowFrom",
  "groupAllowFrom",
];

const profileIndex = process.argv.indexOf("--profile");
const profile = profileIndex >= 0 ? process.argv[profileIndex + 1] : "empyralis-schema-fixture";

const version = execFileSync("openclaw", ["--profile", profile, "--version"], { encoding: "utf8" }).trim();
const schema = JSON.parse(
  execFileSync("openclaw", ["--profile", profile, "config", "schema"], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  }),
);

const source = schema.properties.channels.properties;
const properties = {};
for (const channel of CHANNELS) {
  const node = source[channel];
  if (!node) throw new Error(`channels.${channel} is missing from the installed schema.`);
  const kept = {};
  for (const key of POLICY_PROPERTIES) {
    if (Object.hasOwn(node.properties ?? {}, key)) kept[key] = node.properties[key];
  }
  properties[channel] = { type: "object", properties: kept };
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
