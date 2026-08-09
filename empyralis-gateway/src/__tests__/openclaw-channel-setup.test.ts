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
