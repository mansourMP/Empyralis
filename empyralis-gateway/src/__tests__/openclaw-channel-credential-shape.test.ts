/**
 * The credential-shape derivation is a CROSS-LANGUAGE DUPLICATE of
 * `scripts/generate_openclaw_channel_manifest.py`, and this file is what keeps
 * the two answering the same question the same way.
 *
 * The expected set and the actual set come from different sources, which is
 * the only shape of conformance check worth having (CLAUDE.md: "a check that
 * derives its own expectations from the thing it checks is blind, and reports
 * 'passed'"):
 *
 *     expected   server_modules/openclaw_channel_manifest.json — what the
 *                PYTHON generator derived, checked in
 *     actual     this TypeScript derivation, run over the same
 *                `openclaw config schema` document the generator read
 *
 * They agree on the FIELD SET for every channel the pinned build declares a
 * node for. They differ on the primary/advanced split for exactly three, and
 * that difference is bounded, named and proven unable to reach the live path
 * — see MODE_GATED_SPLIT_CHANNELS below.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { GENERATED_OPENCLAW_MANIFEST } from "../openclaw/generated-openclaw-channels";
import {
  deriveOpenClawCredentialShapes,
  isSecretRefUnion,
} from "../openclaw/provisioning/openclaw-channel-credential-shape";

/**
 * The channels whose PYTHON `advanced` split depends on an axis this port
 * deliberately does not carry: `_mode_gated_secrets`, which reads every
 * `dist/*.js` in the OpenClaw package (98MB, 4566 files on the pinned build)
 * to learn that a secret is only live under some OTHER setting — Feishu's
 * `encryptKey` under `connectionMode: "webhook"`, Telegram's and Zalo's
 * `webhookSecret` under a configured webhook URL. Scanning that on every panel
 * poll is not a trade worth making inside a gateway.
 *
 * THE GAP CANNOT REACH THE LIVE PATH, and that is asserted below rather than
 * asserted here in prose: this derivation runs ONLY for channels the manifest
 * calls `plugin_absent`, OpenClaw ships bundled secret contracts for nine
 * channels and every one of them is a channel the pinned build already
 * declares a node for. So for every channel this code is ever asked about,
 * the Python generator's gated-secret set is empty too, and the two
 * implementations produce the identical split.
 *
 * Field NAMES, `secret`, `type` and `file_alternative` match everywhere,
 * including here — this is a difference in which half of the form a field is
 * rendered in, never in which fields exist or which are credentials.
 */
const MODE_GATED_SPLIT_CHANNELS: ReadonlySet<string> = new Set(["feishu", "telegram", "zalo"]);

/** dist/ does not carry fixtures, so resolve back to src/ when running from
 *  the build output — the same two-candidate idiom openclaw-provisioning.test.ts
 *  already uses. */
function unprunedFixturePath(): string {
  const candidates = [
    path.join(__dirname, "fixtures", "openclaw-config-schema.channels.unpruned.json"),
    path.join(__dirname, "..", "..", "src", "__tests__", "fixtures", "openclaw-config-schema.channels.unpruned.json"),
  ];
  for (const candidate of candidates) if (fs.existsSync(candidate)) return candidate;
  throw new Error(`unpruned openclaw schema fixture not found (looked in ${candidates.join(", ")})`);
}

function unprunedSchema(): unknown {
  return JSON.parse(fs.readFileSync(unprunedFixturePath(), "utf8"));
}

/** The channels the manifest admits it cannot describe — read from the
 *  manifest, never typed here. Four today. */
function pluginAbsentChannelIds(): string[] {
  return GENERATED_OPENCLAW_MANIFEST.channels
    .filter((channel) => channel.credential_shape.connect_method === "plugin_absent")
    .map((channel) => channel.id);
}

test("the fixture describes the same OpenClaw the manifest was generated from", () => {
  // Two documents claiming to describe one build is a drift source of its own.
  const fixture = unprunedSchema() as { openclawVersion?: string };
  assert.equal(fixture.openclawVersion, GENERATED_OPENCLAW_MANIFEST.openclaw_version);
});

test("this derivation reproduces the Python generator's field set, channel for channel", () => {
  const schema = unprunedSchema();
  const ids = GENERATED_OPENCLAW_MANIFEST.channels.map((channel) => channel.id);
  const derived = deriveOpenClawCredentialShapes(schema, ids);

  let compared = 0;
  for (const channel of GENERATED_OPENCLAW_MANIFEST.channels) {
    const expected = channel.credential_shape;
    if (expected.connect_method === "plugin_absent") {
      // The manifest has nothing to compare against, by definition. What IS
      // assertable is that the fixture agrees the node is absent — if a node
      // appeared here the manifest would simply be stale.
      assert.equal(
        derived[channel.id],
        undefined,
        `${channel.id}: the manifest says its plugin contributed no config node, but the schema has one`,
      );
      continue;
    }
    const got = derived[channel.id];
    assert.ok(got, `${channel.id}: the derivation produced no shape at all`);
    assert.equal(got.connect_method, expected.connect_method, `${channel.id}: connect_method`);
    assert.deepEqual(
      [...got.fields]
        .map((field) => ({
          name: field.name,
          secret: field.secret,
          type: field.type,
          file_alternative: field.file_alternative,
        }))
        .sort((a, b) => (a.name < b.name ? -1 : 1)),
      // Sorted on both sides: the ORDER of the two halves is a property of the
      // advanced split, which the next test owns. This one is about which
      // fields exist and what they are.
      [...expected.fields]
        .map((field) => ({
          name: field.name,
          secret: field.secret,
          type: field.type,
          file_alternative: field.file_alternative,
        }))
        .sort((a, b) => (a.name < b.name ? -1 : 1)),
      `${channel.id}: credential fields`,
    );
    compared += 1;
  }
  assert.ok(compared >= 15, `only ${compared} channels were actually compared`);
});

test("the primary/advanced split matches too, except where a bundled secret contract decides it", () => {
  const schema = unprunedSchema();
  const ids = GENERATED_OPENCLAW_MANIFEST.channels.map((channel) => channel.id);
  const derived = deriveOpenClawCredentialShapes(schema, ids);

  const disagreed: string[] = [];
  for (const channel of GENERATED_OPENCLAW_MANIFEST.channels) {
    const got = derived[channel.id];
    if (!got || channel.credential_shape.connect_method === "plugin_absent") continue;
    const expected = new Map(
      channel.credential_shape.fields.map((field) => [field.name, field.advanced]),
    );
    if (got.fields.some((field) => expected.get(field.name) !== field.advanced)) {
      disagreed.push(channel.id);
    }
  }

  assert.deepEqual(
    disagreed.sort(),
    [...MODE_GATED_SPLIT_CHANNELS].sort(),
    "the set of channels whose advanced split differs from the generator's has changed",
  );
});

test("the unported axis cannot reach a channel this derivation is ever asked about", () => {
  // The load-bearing claim behind MODE_GATED_SPLIT_CHANNELS. This code runs
  // only for `plugin_absent` channels; if one of them ever needed the
  // gated-secret axis, the exemption above would be quietly covering a real
  // defect on the live path instead of an inert difference off it.
  for (const channelId of pluginAbsentChannelIds()) {
    assert.equal(
      MODE_GATED_SPLIT_CHANNELS.has(channelId),
      false,
      `${channelId} is both derived live AND exempt from the split assertion — the exemption is no longer inert`,
    );
  }
});

test("a secret is OpenClaw's own SecretRef union, never a name", () => {
  // The structural signal, taken from a REAL node in the fixture rather than
  // built here: a fixture that invents its own input cannot notice the real
  // input is shaped differently.
  const channels = (unprunedSchema() as { properties: { channels: { properties: Record<string, { properties?: Record<string, unknown> }> } } })
    .properties.channels.properties;
  const secretNodes: unknown[] = [];
  for (const node of Object.values(channels)) {
    for (const fieldNode of Object.values(node.properties ?? {})) {
      if (isSecretRefUnion(fieldNode)) secretNodes.push(fieldNode);
    }
  }
  assert.ok(secretNodes.length > 0, "the fixture carries no SecretRef union at all");
  // A plain string named like a credential is NOT one. LINE's
  // `channelAccessToken` is exactly this shape and is caught by its `tokenFile`
  // sibling instead, never by its name.
  assert.equal(isSecretRefUnion({ type: "string" }), false);
  assert.equal(isSecretRefUnion({ anyOf: [{ type: "string" }] }), false);
});

test("a channel whose node is absent gets no shape, rather than an empty one", () => {
  // "The plugin is installed and still contributes nothing" must keep the
  // manifest's honest unknown. An empty shape would render as `pairing` — a
  // guess that this channel links by QR — which is the failure this whole
  // derivation exists to avoid making in the other direction.
  const derived = deriveOpenClawCredentialShapes(unprunedSchema(), ["a-channel-openclaw-does-not-have"]);
  assert.deepEqual(derived, {});
});
