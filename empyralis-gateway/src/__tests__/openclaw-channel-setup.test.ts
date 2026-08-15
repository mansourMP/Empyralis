/**
 * The device half of channel setup.
 *
 * This is the side where a mistake matters most: it takes a config path
 * fragment from the cloud and hands it to `openclaw config patch`. Without a
 * closed allowlist, a confused or compromised caller could patch
 * `gateway.auth.token`, `tools.profile` or `plugins.allow` and walk straight
 * through the lockdown the provisioner exists to enforce. So the write path is
 * tested for what it REFUSES, not just what it accepts.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { GENERATED_OPENCLAW_MANIFEST } from "../openclaw/generated-openclaw-channels";
import { OPENCLAW_TRANSPORT_CHANNEL_IDS } from "../openclaw/capabilities";
import {
  OPENCLAW_CHANNEL_SETUP_CAPABILITY,
  OpenClawChannelSetupRuntime,
  credentialFieldsForChannel,
  parseCredentialWrite,
} from "../openclaw/provisioning/openclaw-channel-setup";

/** A live transport channel that takes a pasted credential, chosen from the
 *  manifest rather than named — a hard-coded id here would be the very thing
 *  the whole derivation exists to remove, and it would rot the day that
 *  channel is cut over to a first-party runtime. */
function someCredentialChannel(): { id: string; fields: readonly { name: string; secret: boolean }[] } {
  const channel = GENERATED_OPENCLAW_MANIFEST.channels.find(
    (entry) =>
      OPENCLAW_TRANSPORT_CHANNEL_IDS.includes(entry.id) &&
      entry.credential_shape.connect_method === "credential",
  );
  assert.ok(channel, "the manifest carries no live channel that takes a credential");
  return { id: channel.id, fields: channel.credential_shape.fields };
}

function somePairingChannel(): string | undefined {
  return GENERATED_OPENCLAW_MANIFEST.channels.find(
    (entry) =>
      OPENCLAW_TRANSPORT_CHANNEL_IDS.includes(entry.id) &&
      entry.credential_shape.connect_method === "pairing",
  )?.id;
}

test("a declared field is accepted and trimmed", () => {
  const channel = someCredentialChannel();
  const field = channel.fields[0].name;
  const parsed = parseCredentialWrite({ channel_id: channel.id, values: { [field]: "  value  " } });
  assert.deepEqual(parsed.errors, []);
  assert.deepEqual(parsed.values, { [field]: "value" });
});

test("a field OpenClaw does not declare is refused BY NAME", () => {
  // A dropped field would be a save that reports success and changes nothing.
  const channel = someCredentialChannel();
  const parsed = parseCredentialWrite({
    channel_id: channel.id,
    values: { notAFieldOpenClawHas: "x" },
  });
  assert.equal(parsed.values.notAFieldOpenClawHas, undefined);
  assert.ok(parsed.errors.some((error) => error.includes("notAFieldOpenClawHas")));
});

test("a lockdown path cannot be reached through the credential write", () => {
  // The reason the allowlist exists. None of these are credential fields on
  // any channel, so all of them must be refused whatever channel is named.
  const channel = someCredentialChannel();
  for (const forbidden of ["enabled", "dmPolicy", "groupPolicy", "tools", "configWrites"]) {
    const parsed = parseCredentialWrite({ channel_id: channel.id, values: { [forbidden]: "x" } });
    assert.equal(Object.keys(parsed.values).length, 0, `${forbidden} was accepted`);
    assert.ok(parsed.errors.length > 0, `${forbidden} produced no error`);
  }
});

test("a channel this gateway does not transport is refused", () => {
  const parsed = parseCredentialWrite({ channel_id: "notarealchannel", values: { token: "x" } });
  assert.deepEqual(parsed.values, {});
  assert.ok(parsed.errors[0].includes("notarealchannel"));
});

test("a superseded channel is not writable even though the manifest declares it", () => {
  // Declared and visible, never live: a first-party runtime owns these, and a
  // credential written here would put two runtimes on one account.
  const superseded = GENERATED_OPENCLAW_MANIFEST.channels.find(
    (entry) => !OPENCLAW_TRANSPORT_CHANNEL_IDS.includes(entry.id),
  );
  assert.ok(superseded, "the manifest has no superseded channel to test with");
  assert.equal(credentialFieldsForChannel(superseded.id), undefined);
});

test("a pairing channel refuses a credential rather than silently accepting one", () => {
  const pairing = somePairingChannel();
  if (!pairing) return; // no live pairing channel in this build; nothing to assert
  const parsed = parseCredentialWrite({ channel_id: pairing, values: { token: "x" } });
  assert.deepEqual(parsed.values, {});
  assert.ok(parsed.errors[0].includes("does not take a pasted credential"));
});

test("OpenClaw's own redaction placeholder is never written back as a value", () => {
  // The browser reads `set: true` for a secret and shows a masked field. If a
  // form ever echoed the masked read back, this is what would arrive — and
  // writing the literal string would silently destroy a working credential.
  const channel = someCredentialChannel();
  const secret = channel.fields.find((field) => field.secret);
  if (!secret) return;
  const parsed = parseCredentialWrite({
    channel_id: channel.id,
    values: { [secret.name]: "__OPENCLAW_REDACTED__" },
  });
  assert.deepEqual(parsed.values, {});
  assert.ok(parsed.errors[0].includes("redaction placeholder"));
});

test("an empty value is refused rather than read as a clear", () => {
  const channel = someCredentialChannel();
  const parsed = parseCredentialWrite({
    channel_id: channel.id,
    values: { [channel.fields[0].name]: "   " },
  });
  assert.deepEqual(parsed.values, {});
  assert.ok(parsed.errors[0].includes("Omit the field"));
});

test("a read reports the three states separately and no value", async () => {
  const channel = someCredentialChannel();
  const secretField = channel.fields.find((field) => field.secret)?.name ?? channel.fields[0].name;
  const runtime = new OpenClawChannelSetupRuntime({
    profile: "test-profile",
    cli: {
      // Stands in for the CLI only. The shapes are OpenClaw's real ones,
      // including the redaction placeholder it substitutes for a secret.
      run: async (args: string[]) => {
        if (args[0] === "channels") {
          return {
            code: 0,
            stdout: JSON.stringify({
              chat: { [channel.id]: { installed: true, accounts: ["default"], origin: "configured" } },
            }),
            stderr: "",
          };
        }
        return {
          code: 0,
          stdout: JSON.stringify({
            [channel.id]: { enabled: true, [secretField]: "__OPENCLAW_REDACTED__" },
          }),
          stderr: "",
        };
      },
    } as never,
  });

  const result = await runtime.handleCapabilityInvoke({
    payload: { capability_id: OPENCLAW_CHANNEL_SETUP_CAPABILITY, arguments: { action: "read" } },
  });

  const states = (result.channels ?? []) as Array<Record<string, unknown>>;
  const state = states.find((entry) => entry.channel_id === channel.id);
  assert.ok(state, "the read did not project the channel");
  // Three independent facts, none collapsed into the others.
  assert.equal(state.installed, true);
  assert.equal(state.enabled, true);
  assert.equal(typeof state.configured, "boolean");
  const fields = state.fields as Array<Record<string, unknown>>;
  const projected = fields.find((entry) => entry.name === secretField);
  assert.ok(projected);
  // A redacted read means "a value is present", and the placeholder itself is
  // never re-emitted.
  assert.equal(projected.set, true);
  assert.equal(JSON.stringify(result).includes("__OPENCLAW_REDACTED__"), false);
});

test("an unknown action is refused rather than falling through to a read", () => {
  // Silent misrouting beats loud failure, and that is a bug: a typo'd action
  // that quietly performed a read would report success for a write.
  const runtime = new OpenClawChannelSetupRuntime({ profile: "test-profile", cli: {} as never });
  return runtime
    .handleCapabilityInvoke({
      payload: { capability_id: OPENCLAW_CHANNEL_SETUP_CAPABILITY, arguments: { action: "wipe" } },
    })
    .then((result) => {
      assert.equal(result.status, "refused");
    });
});

// ── A channel whose plugin the MANIFEST never saw ─────────────────────────
//
// Four carried channels contribute their `channels.<id>` config node only once
// their plugin is installed, and none of them was installed on the machine the
// manifest was generated from — so the manifest carries `plugin_absent` and no
// fields for them. Correct before installation. WRONG AFTER IT: from the
// moment provisioning installs the plugin, this box's own `config schema`
// carries the node and can say exactly what the fields are.
//
// Nothing asked, so all four dead-ended after a successful install — the panel
// said "Setup fields aren't known for this one yet" forever, on a computer that
// knew. These tests are the seam that reads it.

/** The manifest's own list, never a typed id: the day a plugin ships bundled
 *  upstream this set shrinks, and a hard-coded id here would then be testing a
 *  channel that no longer takes this path. */
function somePluginAbsentChannel(): string {
  const channel = GENERATED_OPENCLAW_MANIFEST.channels.find(
    (entry) =>
      OPENCLAW_TRANSPORT_CHANNEL_IDS.includes(entry.id) &&
      entry.credential_shape.connect_method === "plugin_absent",
  );
  assert.ok(channel, "the manifest carries no plugin-absent transport channel");
  return channel.id;
}

/** A REAL channel node out of the checked-in schema fixture, re-keyed under the
 *  absent channel's id — which is exactly what installing that plugin does to
 *  the live schema. Borrowed rather than invented: a fixture that makes up its
 *  own input cannot notice the real input is shaped differently. */
function liveSchemaWithNodeFor(channelId: string): { schema: unknown; fieldNames: string[] } {
  const candidates = [
    path.join(__dirname, "fixtures", "openclaw-config-schema.channels.unpruned.json"),
    path.join(__dirname, "..", "..", "src", "__tests__", "fixtures", "openclaw-config-schema.channels.unpruned.json"),
  ];
  const fixturePath = candidates.find((candidate) => fs.existsSync(candidate));
  assert.ok(fixturePath, `unpruned openclaw schema fixture not found (looked in ${candidates.join(", ")})`);
  const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8")) as { properties: { channels: { properties: Record<string, unknown> } } };
  const donor = GENERATED_OPENCLAW_MANIFEST.channels.find(
    (entry) => entry.credential_shape.connect_method === "credential",
  );
  assert.ok(donor, "the manifest carries no credential channel to borrow a node from");
  const channels = fixture.properties.channels.properties;
  channels[channelId] = channels[donor.id];
  return {
    schema: fixture,
    fieldNames: donor.credential_shape.fields.map((field) => field.name).sort(),
  };
}

interface SchemaCountingCli {
  cli: { run: (args: string[]) => Promise<{ code: number; stdout: string; stderr: string }>; configSchema: () => Promise<unknown> };
  schemaReads: () => number;
}

function countingCli(options: { installed: string[]; schema: unknown }): SchemaCountingCli {
  let schemaReads = 0;
  const chat: Record<string, unknown> = {};
  for (const id of options.installed) chat[id] = { installed: true, accounts: [] };
  return {
    schemaReads: () => schemaReads,
    cli: {
      run: async (args: string[]) => {
        if (args[0] === "channels") return { code: 0, stdout: JSON.stringify({ chat }), stderr: "" };
        return { code: 0, stdout: JSON.stringify({}), stderr: "" };
      },
      configSchema: async () => {
        schemaReads += 1;
        return options.schema;
      },
    },
  };
}

test("an installed plugin-absent channel reports the fields its own box declares", async () => {
  const channelId = somePluginAbsentChannel();
  const { schema, fieldNames } = liveSchemaWithNodeFor(channelId);
  const counting = countingCli({ installed: [channelId], schema });
  const runtime = new OpenClawChannelSetupRuntime({
    profile: "test-profile",
    cli: counting.cli as never,
  });

  const result = await runtime.read();
  const state = result.channels.find((entry) => entry.channel_id === channelId);
  assert.ok(state, "the read did not project the channel");
  // The manifest's pre-install belief is `plugin_absent` with no fields; the
  // box has been asked and the observation wins.
  assert.equal(state.connect_method, "credential");
  assert.deepEqual(
    state.fields.map((field) => field.name).sort(),
    fieldNames,
    "the live schema's fields did not reach the state",
  );
  assert.ok(state.fields.some((field) => field.secret), "no field was classified as a secret");
  // Read AT MOST ONCE: `config schema` is ~2.5MB, and one shell-out per
  // channel is exactly the shape this had to avoid.
  assert.equal(counting.schemaReads(), 1);
});

test("the 2.5MB schema is not read at all when no channel needs it", async () => {
  // Every box today is this box: none of the four plugin-absent channels is
  // installed, so a read must cost exactly what it always did.
  const { schema } = liveSchemaWithNodeFor(somePluginAbsentChannel());
  const counting = countingCli({ installed: [someCredentialChannel().id], schema });
  const runtime = new OpenClawChannelSetupRuntime({
    profile: "test-profile",
    cli: counting.cli as never,
  });

  const result = await runtime.read();
  assert.equal(counting.schemaReads(), 0);
  const absent = result.channels.find((entry) => entry.channel_id === somePluginAbsentChannel());
  // …and the honest unknown is untouched.
  assert.ok(absent);
  assert.equal(absent.connect_method, "plugin_absent");
  assert.deepEqual(absent.fields, []);
});

test("an installed plugin-absent channel that still declares no node keeps the honest unknown", async () => {
  // "The plugin is there and contributes nothing yet" is a real state, and the
  // answer to it is still "we do not know" — never a guessed form, never a
  // guessed QR flow.
  const channelId = somePluginAbsentChannel();
  const counting = countingCli({
    installed: [channelId],
    schema: { properties: { channels: { properties: { telegram: { properties: {} } } } } },
  });
  const runtime = new OpenClawChannelSetupRuntime({
    profile: "test-profile",
    cli: counting.cli as never,
  });

  const state = (await runtime.read()).channels.find((entry) => entry.channel_id === channelId);
  assert.ok(state);
  assert.equal(state.connect_method, "plugin_absent");
  assert.deepEqual(state.fields, []);
});

test("the write allowlist for a live-derived channel is the live schema, and is still closed", () => {
  const channelId = somePluginAbsentChannel();
  const { fieldNames } = liveSchemaWithNodeFor(channelId);
  const live = fieldNames.map((name) => ({
    name,
    secret: false,
    type: "string" as const,
    file_alternative: null,
    advanced: false,
  }));

  const accepted = parseCredentialWrite(
    { channel_id: channelId, values: { [fieldNames[0]]: " v " } },
    live,
  );
  assert.deepEqual(accepted.errors, []);
  assert.deepEqual(accepted.values, { [fieldNames[0]]: "v" });

  // Closed, not opened: a live read decides WHICH fields, never THAT anything
  // goes. A lockdown path is still unreachable through this door.
  const refused = parseCredentialWrite(
    { channel_id: channelId, values: { "gateway.auth.token": "x" } },
    live,
  );
  assert.deepEqual(refused.values, {});
  assert.equal(refused.errors.length, 1);
});

test("a live read can never widen a channel the pinned build already answers for", () => {
  // A pairing channel's empty field list is the manifest's POSITIVE answer that
  // there is nothing to paste. Only `plugin_absent` — the manifest admitting it
  // cannot know — may be answered by the box.
  const pairing = somePairingChannel();
  if (!pairing) return;
  const parsed = parseCredentialWrite(
    { channel_id: pairing, values: { anything: "x" } },
    [{ name: "anything", secret: true, type: "string", file_alternative: null, advanced: false }],
  );
  assert.deepEqual(parsed.values, {});
  assert.ok(parsed.errors[0].includes("does not take a pasted credential"));
});
