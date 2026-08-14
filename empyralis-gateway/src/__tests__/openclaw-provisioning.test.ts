import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import {
  OPENCLAW_TRANSPORT_CHANNEL_IDS,
  openClawTransportChannelKeys,
} from "../openclaw/capabilities";
import {
  GENERATED_OPENCLAW_MANIFEST,
  GENERATED_OPENCLAW_SUPERSEDED_CHANNEL_IDS,
} from "../openclaw/generated-openclaw-channels";
import { openClawChannelIdFromChannelKey } from "../openclaw/outbound-payload";
import {
  OPENCLAW_CHANNEL_POLICY_SHAPES,
  auditOpenClawChannelShapes,
  resolveOpenClawChannelKeySupport,
  resolveOpenClawChannelToolFlags,
  resolveOpenClawPluginHookFlags,
} from "../openclaw/provisioning/openclaw-channel-shapes";
import {
  canonicalJson,
  detectForbiddenCredentialEnvNames,
  findOpenClawLockdownViolations,
  renderOpenClawConfig,
  sanitizeOpenClawChildEnv,
  type EmpyralisChannelPolicy,
  type OpenClawProvisioningPlan,
} from "../openclaw/provisioning/openclaw-config-plan";
import {
  OPENCLAW_PINNED_VERSION,
  checkOpenClawVersion,
  parseOpenClawVersion,
} from "../openclaw/provisioning/openclaw-version";
import {
  OpenClawProvisioner,
  blockingAuditFindings,
  findConfigDrift,
  flattenConfigPaths,
} from "../openclaw/provisioning/openclaw-provisioner";
import { OpenClawCli, assertValidOpenClawProfile } from "../openclaw/provisioning/openclaw-cli";
import {
  channelPluginInstallDescriptor,
  checkPluginHostCompatibility,
} from "../openclaw/provisioning/openclaw-plugin-install";
import {
  buildOpenClawSupervisedEnv,
  openClawSupervisorLabel,
  resolveExpectedOpenClawSupervisorUnit,
} from "../openclaw/provisioning/openclaw-supervisor-unit";
import { parseChannelPolicies } from "../openclaw/provisioning/openclaw-provisioning-runtime";

// ── helpers ───────────────────────────────────────────────────────────────

function policy(overrides: Partial<EmpyralisChannelPolicy> & { channelId: string }): EmpyralisChannelPolicy {
  return {
    enabled: true,
    dmPolicy: { mode: "open", allowlist: [] },
    groupPolicy: { mode: "open", allowlist: [], requireMention: true },
    ...overrides,
  };
}

function plan(channels: EmpyralisChannelPolicy[]): OpenClawProvisioningPlan {
  return {
    profile: "acme",
    gatewayPort: 18789,
    profileStateDir: "/tmp/fake-home/.openclaw-acme",
    bridgePluginPath: "/opt/empyralis/openclaw-bridge-plugin",
    channels,
  };
}

/** Tests run from `dist/`, and tsc does not copy JSON fixtures, so the
 *  fixture is read from the source tree. Both layouts are tried so this works
 *  whether the file is executed from dist or directly from src. */
function fixturePath(): string {
  const candidates = [
    path.join(__dirname, "fixtures", "openclaw-config-schema.channels.json"),
    path.join(__dirname, "..", "..", "src", "__tests__", "fixtures", "openclaw-config-schema.channels.json"),
  ];
  for (const candidate of candidates) if (fs.existsSync(candidate)) return candidate;
  throw new Error(`openclaw schema fixture not found (looked in ${candidates.join(", ")})`);
}

function channelBlock(rendered: ReturnType<typeof renderOpenClawConfig>, channelId: string): Record<string, unknown> {
  const channels = rendered.config.channels as Record<string, Record<string, unknown>>;
  return channels[channelId];
}

// ── version pin ───────────────────────────────────────────────────────────

test("version pin: accepts only the exact pinned build", () => {
  assert.equal(parseOpenClawVersion("OpenClaw 2026.6.10 (aa69b12) — All your chats"), "2026.6.10");
  assert.equal(parseOpenClawVersion("2026.6.10\n"), "2026.6.10");
  assert.equal(checkOpenClawVersion(`${OPENCLAW_PINNED_VERSION}\n`).ok, true);

  const mismatch = checkOpenClawVersion("2026.7.0");
  assert.equal(mismatch.ok, false);
  assert.equal(mismatch.code, "openclaw_version_mismatch");
  // The refusal must name what silently breaks, not just "wrong version" —
  // that naming is the whole reason the pin exists (MAN-306's shape).
  assert.match(mismatch.detail ?? "", /cancel-predicate/);
  assert.match(mismatch.detail ?? "", /openclaw-plugin-sdk/);
  assert.match(mismatch.detail ?? "", /outbound-payload/);

  assert.equal(checkOpenClawVersion(undefined).code, "openclaw_not_installed");
  assert.equal(checkOpenClawVersion("no version here").code, "openclaw_version_unreadable");
});

// ── the authority table ───────────────────────────────────────────────────

test("require_mention=false is EXACT where expressible", () => {
  // feishu has a channel-level requireMention AND a groups map.
  const rendered = renderOpenClawConfig(
    plan([
      policy({
        channelId: "feishu",
        groupPolicy: { mode: "open", allowlist: [], requireMention: false },
      }),
    ]),
    { gatewayToken: "t" },
  );
  assert.equal(rendered.disabledChannels.length, 0);
  const block = channelBlock(rendered, "feishu");
  assert.equal(block.requireMention, false);
  // OpenClaw's own default is TRUE and falls through per-group, so the
  // wildcard entry is required, not decorative.
  assert.deepEqual(block.groups, { "*": { requireMention: false } });
});

test("require_mention=false with a chat allowlist writes per-chat entries, never a wildcard", () => {
  const rendered = renderOpenClawConfig(
    plan([
      policy({
        channelId: "line",
        groupPolicy: { mode: "allowlist", allowlist: ["Cb", "Ca"], requireMention: false },
      }),
    ]),
    { gatewayToken: "t" },
  );
  const block = channelBlock(rendered, "line");
  assert.equal(block.groupPolicy, "allowlist");
  // A `"*"` key would set allowAll in their resolveChannelGroupPolicy and
  // silently turn an allowlist into "every group".
  assert.deepEqual(block.groups, {
    Ca: { requireMention: false },
    Cb: { requireMention: false },
  });
});

test("UNMAPPABLE: require_mention=false on zalo fails the channel CLOSED", () => {
  const rendered = renderOpenClawConfig(
    plan([
      policy({
        channelId: "zalo",
        groupPolicy: { mode: "open", allowlist: [], requireMention: false },
      }),
    ]),
    { gatewayToken: "t" },
  );
  assert.equal(channelBlock(rendered, "zalo").enabled, false);
  assert.equal(rendered.disabledChannels.length, 1);
  assert.equal(rendered.disabledChannels[0].code, "require_mention_off_not_expressible");
  assert.equal(rendered.disabledChannels[0].channelId, "zalo");
  // The owner-facing sentence must explain the consequence, not the schema.
  assert.match(rendered.disabledChannels[0].detail, /never reaching Empyralis/);
});

test("zalo is fine when require_mention is ON, or when no group traffic is admitted", () => {
  const on = renderOpenClawConfig(
    plan([policy({ channelId: "zalo", groupPolicy: { mode: "open", allowlist: [], requireMention: true } })]),
    { gatewayToken: "t" },
  );
  assert.equal(on.disabledChannels.length, 0);
  assert.equal(channelBlock(on, "zalo").enabled, true);

  // require_mention=false but groups disabled: the mention axis is
  // unreachable, so there is nothing to fail closed about.
  const groupsOff = renderOpenClawConfig(
    plan([policy({ channelId: "zalo", groupPolicy: { mode: "disabled", allowlist: [], requireMention: false } })]),
    { gatewayToken: "t" },
  );
  assert.equal(groupsOff.disabledChannels.length, 0);
  assert.equal(channelBlock(groupsOff, "zalo").groupPolicy, "disabled");

  // Same for an allowlist with nothing in it — the default state of a fresh
  // binding (DEFAULT_GROUP_POLICY_MODE=allowlist, allowlist=[]).
  const emptyAllowlist = renderOpenClawConfig(
    plan([policy({ channelId: "zalo", groupPolicy: { mode: "allowlist", allowlist: [], requireMention: false } })]),
    { gatewayToken: "t" },
  );
  assert.equal(emptyAllowlist.disabledChannels.length, 0);
  assert.equal(channelBlock(emptyAllowlist, "zalo").groupPolicy, "disabled");
});

test("dm pairing/owner_only widen to open, and the widening is RECORDED", () => {
  const rendered = renderOpenClawConfig(
    plan([
      policy({ channelId: "feishu", dmPolicy: { mode: "pairing", allowlist: [] } }),
      policy({ channelId: "line", dmPolicy: { mode: "owner_only", allowlist: [] } }),
    ]),
    { gatewayToken: "t" },
  );
  assert.equal(channelBlock(rendered, "feishu").dmPolicy, "open");
  assert.equal(channelBlock(rendered, "line").dmPolicy, "open");
  // "open" without the wildcard means DROP EVERY DM — their own validator
  // says so. A widening that silences the channel is not a widening.
  assert.deepEqual(channelBlock(rendered, "feishu").allowFrom, ["*"]);
  assert.deepEqual(channelBlock(rendered, "line").allowFrom, ["*"]);
  const codes = rendered.widenings.map((finding) => finding.code).sort();
  assert.deepEqual(codes, ["dm_owner_only_widened_to_open", "dm_pairing_widened_to_open"]);
  // Nothing is silently loosened: every widening carries a reason.
  for (const finding of rendered.widenings) assert.ok(finding.detail.length > 20);
});

test("dm allowlist is expressed EXACTLY where the channel has the mode", () => {
  const rendered = renderOpenClawConfig(
    plan([policy({ channelId: "msteams", dmPolicy: { mode: "allowlist", allowlist: ["b@x", "a@x"] } })]),
    { gatewayToken: "t" },
  );
  const block = channelBlock(rendered, "msteams");
  assert.equal(block.dmPolicy, "allowlist");
  assert.deepEqual(block.allowFrom, ["a@x", "b@x"]);
  assert.equal(rendered.widenings.length, 0);
});

test("a group chat allowlist is never written into groupAllowFrom (a SENDER list)", () => {
  const rendered = renderOpenClawConfig(
    plan([
      policy({
        channelId: "zalo",
        groupPolicy: { mode: "allowlist", allowlist: ["group-1"], requireMention: true },
      }),
    ]),
    { gatewayToken: "t" },
  );
  const block = channelBlock(rendered, "zalo");
  assert.equal(block.groupAllowFrom, undefined);
  assert.equal(block.groupPolicy, "open");
  assert.deepEqual(
    rendered.widenings.map((finding) => finding.code),
    ["group_allowlist_not_keyed_by_chat_widened_to_open"],
  );
});

// ── a key the channel does not have is never written ──────────────────────
//
// THE REGRESSION THIS EXISTS FOR, captured from a real provisioning run
// against a real openclaw@2026.6.10 through the product's own route:
//
//   {"status":"refused",
//    "refusal":{"code":"openclaw_config_patch_failed",
//      "detail":"OpenClaw rejected the generated config: Error: Config
//                validation failed: channels.clickclack: invalid config:
//                must not have additional properties: \"groupPolicy\",
//                \"dmPolicy\""}}
//
// Almost every `channels.<id>` node is `additionalProperties: false`, and the
// whole document goes over as ONE patch by design — so one unwritable key on
// one channel refused every channel on every box, Telegram included. The
// answer had been sitting in the shape data the whole time: ClickClack's
// dm/group policy enums are BOTH empty, which is the manifest's way of saying
// the fields do not exist.

test("a channel whose policy modes are empty gets a block with NEITHER key", () => {
  // clickclack: dm_policy_modes [] and group_policy_modes [] — it hardcodes
  // both in its own ingress call and exposes `allowFrom` as the only lever.
  const shape = OPENCLAW_CHANNEL_POLICY_SHAPES.clickclack;
  assert.deepEqual(shape.dmPolicyModes, [], "fixture drifted: clickclack is the empty-enum case");
  assert.deepEqual(shape.groupPolicyModes, []);

  const rendered = renderOpenClawConfig(plan([policy({ channelId: "clickclack" })]), { gatewayToken: "t" });
  const block = channelBlock(rendered, "clickclack");

  assert.equal(Object.hasOwn(block, "dmPolicy"), false, "dmPolicy would refuse the entire push");
  assert.equal(Object.hasOwn(block, "groupPolicy"), false, "groupPolicy would refuse the entire push");
  // Still ON, and still carrying the one lever it does have — silently
  // dropping the channel would be curation, and curation is what this whole
  // subsystem exists not to do.
  assert.equal(block.enabled, true);
  assert.deepEqual(block.allowFrom, ["*"]);

  // Not silently: three facts, three codes.
  assert.deepEqual(
    rendered.widenings.map((finding) => finding.code).sort(),
    ["dm_policy_mode_not_expressible_sender_list_only", "group_policy_not_expressible"],
  );
  for (const finding of rendered.widenings) assert.ok(finding.detail.length > 20);
  assert.equal(rendered.disabledChannels.length, 0);
});

test("no channel is ever handed a policy MODE its own enum does not carry", () => {
  // One key over from the bug above, and the same class: `groupPolicy` exists
  // but not the word we want. Their validator refuses an out-of-enum value
  // exactly as loudly as an undeclared key.
  for (const [channelId, shape] of Object.entries(OPENCLAW_CHANNEL_POLICY_SHAPES)) {
    for (const groupMode of ["open", "allowlist", "disabled"] as const) {
      for (const dmMode of ["open", "allowlist", "pairing", "owner_only"] as const) {
        const rendered = renderOpenClawConfig(
          plan([
            policy({
              channelId,
              dmPolicy: { mode: dmMode, allowlist: ["someone"] },
              groupPolicy: { mode: groupMode, allowlist: ["a-chat"], requireMention: true },
            }),
          ]),
          { gatewayToken: "t" },
        );
        const block = channelBlock(rendered, channelId);
        if (Object.hasOwn(block, "groupPolicy")) {
          assert.ok(
            shape.groupPolicyModes.includes(block.groupPolicy as never),
            `${channelId}: groupPolicy "${String(block.groupPolicy)}" is not in [${shape.groupPolicyModes.join(", ")}]`,
          );
        }
        if (Object.hasOwn(block, "dmPolicy")) {
          assert.ok(
            shape.dmPolicyModes.includes(block.dmPolicy as never),
            `${channelId}: dmPolicy "${String(block.dmPolicy)}" is not in [${shape.dmPolicyModes.join(", ")}]`,
          );
        }
      }
    }
  }
});

test("the rendered config only ever writes keys the installed schema declares", () => {
  // The whole-document version of the two tests above, driven off the pinned
  // schema fixture rather than off the shape table — a SECOND source, which is
  // the only thing that makes this more than the generator agreeing with
  // itself. It covers `allowFrom` and `enabled` too, which the shape table has
  // no field for: matrix/googlechat/tlon genuinely reject `allowFrom` (they
  // moved it under a nested `dm` object), and writing it would refuse the box.
  const schema = schemaFixture() as any;
  const nodes = schema.properties.channels.properties as Record<string, any>;
  const channelIds = Object.keys(OPENCLAW_CHANNEL_POLICY_SHAPES).filter((id) => nodes[id]);
  assert.ok(channelIds.length > 15, "expected the fixture to cover the real channel set");

  const rendered = renderOpenClawConfig(
    {
      ...plan(channelIds.map((channelId) => policy({ channelId }))),
      channelKeySupport: resolveOpenClawChannelKeySupport(schema, channelIds),
    },
    { gatewayToken: "t" },
  );

  const configured = rendered.config.channels as Record<string, Record<string, unknown>>;
  for (const channelId of channelIds) {
    // A channel with no writable branch is absent, not empty — asserted on its
    // own below; there is nothing to check against a branch here.
    if (!Object.hasOwn(configured, channelId)) continue;
    const node = nodes[channelId];
    const branches: any[] = Array.isArray(node.anyOf) ? node.anyOf : Array.isArray(node.oneOf) ? node.oneOf : [node];
    const written = Object.keys(channelBlock(rendered, channelId));
    // A document that mixes keys from two branches matches NEITHER, so the
    // written set has to sit inside ONE branch — not inside their union.
    const fits = branches.some(
      (branch) =>
        branch.additionalProperties !== false ||
        written.every((key) => Object.hasOwn(branch.properties ?? {}, key)),
    );
    assert.ok(fits, `${channelId}: wrote [${written.join(", ")}] — no single schema branch accepts all of them`);
  }
});

test("key support honours additionalProperties, and a required property with no default", () => {
  const schema = schemaFixture() as any;
  const support = resolveOpenClawChannelKeySupport(schema, ["synology-chat", "telegram", "tlon", "feishu"]);
  const byId = new Map(support.map((entry) => [entry.channelId, entry]));

  // `additionalProperties: {}` — nothing to withhold.
  assert.equal(byId.get("synology-chat")?.acceptsUndeclaredKeys, true);

  assert.equal(byId.get("telegram")?.acceptsUndeclaredKeys, false);
  assert.equal(byId.get("telegram")?.keys.includes("allowFrom"), true);
  // tlon moved its sender list elsewhere entirely.
  assert.equal(byId.get("tlon")?.keys.includes("allowFrom"), false);
  assert.equal(byId.get("tlon")?.keys.includes("enabled"), true);

  // feishu names EIGHT required properties and is still fully writable,
  // because every one of them carries a `default` the merged document picks
  // up. A rule that treated `required` alone as disqualifying would take the
  // most-used channel in the product offline.
  assert.ok((schema.properties.channels.properties.feishu.required as string[]).length >= 8);
  assert.equal(byId.get("feishu")?.keys.includes("dmPolicy"), true);
  assert.equal(byId.get("feishu")?.keys.includes("enabled"), true);

  // Take the default away and the same node stops being writable — the twitch
  // shape, reproduced on a channel whose real schema does not have it.
  const noDefault = JSON.parse(JSON.stringify(schema));
  delete noDefault.properties.channels.properties.feishu.properties.domain.default;
  assert.deepEqual(resolveOpenClawChannelKeySupport(noDefault, ["feishu"])[0].keys, []);
});

test("an anyOf node is resolved to ONE branch, never the union of two", () => {
  // A document mixing keys from two branches matches NEITHER, so the widest
  // usable branch is picked rather than everything on offer. Synthetic,
  // because twitch — the only real anyOf in the pinned build — has no usable
  // branch at all (see the credentials test below).
  const schema = {
    properties: {
      channels: {
        properties: {
          split: {
            anyOf: [
              {
                type: "object",
                additionalProperties: false,
                properties: { enabled: {}, allowFrom: {}, dmPolicy: {} },
              },
              { type: "object", additionalProperties: false, properties: { enabled: {}, groupPolicy: {} } },
            ],
          },
        },
      },
    },
  };
  const [support] = resolveOpenClawChannelKeySupport(schema, ["split"]);
  assert.deepEqual([...support.keys].sort(), ["allowFrom", "dmPolicy", "enabled"]);
  assert.equal(support.keys.includes("groupPolicy"), false, "groupPolicy lives on the OTHER branch");
});

test("a channel with no sender list is left on its own default, never on a mode that drops everything", () => {
  // `dmPolicy: "open"` WITHOUT the wildcard means DROP EVERY DM (their own
  // validator says so), so a channel that will not take `allowFrom` must not
  // be handed the mode either — writing it would be strictly worse than
  // writing nothing, and in the unrecoverable direction.
  const rendered = renderOpenClawConfig(
    {
      ...plan([policy({ channelId: "telegram" })]),
      channelKeySupport: [{ channelId: "telegram", hasConfigNode: true, keys: ["enabled", "groupPolicy"], acceptsUndeclaredKeys: false }],
    },
    { gatewayToken: "t" },
  );
  const block = channelBlock(rendered, "telegram");
  assert.equal(Object.hasOwn(block, "dmPolicy"), false);
  assert.equal(Object.hasOwn(block, "allowFrom"), false);
  assert.equal(
    rendered.widenings.some((finding) => finding.code === "dm_policy_open_requires_wildcard_allowlist"),
    true,
  );
});

test("a channel that will not even take `enabled` is left out entirely, not pushed as {}", () => {
  const rendered = renderOpenClawConfig(
    {
      ...plan([policy({ channelId: "telegram" })]),
      channelKeySupport: [{ channelId: "telegram", hasConfigNode: true, keys: [], acceptsUndeclaredKeys: false }],
    },
    { gatewayToken: "t" },
  );
  // Not `{}`. An empty object is a PRESENT object and gets validated like any
  // other — measured against the real CLI: `channels.twitch: {}` is refused
  // with "must have required property 'username'", which would take the whole
  // push down for a channel we had just decided not to touch.
  assert.equal(Object.hasOwn(rendered.config.channels as object, "telegram"), false);
  assert.deepEqual(
    rendered.disabledChannels.map((finding) => finding.code),
    ["channel_not_configurable"],
  );
});

test("a channel whose every schema branch demands credentials is left out, and said so", () => {
  // twitch, live: an `anyOf` of a credential branch (`username`/`accessToken`/
  // `channel`, no defaults) and an `accounts` branch. There is no document
  // Empyralis can write for it — not even `{enabled: false}` — so it is
  // reported rather than attempted.
  const schema = schemaFixture();
  const support = resolveOpenClawChannelKeySupport(schema, ["twitch"]);
  assert.deepEqual(support[0].keys, []);
  assert.equal(support[0].acceptsUndeclaredKeys, false);

  const rendered = renderOpenClawConfig(
    { ...plan([policy({ channelId: "twitch" })]), channelKeySupport: support },
    { gatewayToken: "t" },
  );
  assert.equal(Object.hasOwn(rendered.config.channels as object, "twitch"), false);
  assert.deepEqual(
    rendered.disabledChannels.map((finding) => finding.code),
    ["channel_not_configurable"],
  );
});

// ── a channel id this box does not have is never written ──────────────────
//
// THE SECOND REGRESSION, from the run right after the first fix landed —
// same blast radius, completely different cause:
//
//   openclaw_config_patch_failed — "Config validation failed:
//   channels.openclaw-weixin: unknown channel id: openclaw-weixin"
//
// `openclaw-weixin` is a real channel in OpenClaw's own registry, but its
// config namespace only exists once its PLUGIN is installed. The generator
// wrote `{enabled: false}` for it — which does not help, because the id is
// refused before its contents are looked at — and the all-or-nothing push
// took every other channel down with it.

test("a channel with no config namespace on this box is OMITTED, not written as {enabled:false}", () => {
  const support = [
    { channelId: "openclaw-weixin", hasConfigNode: false, keys: [], acceptsUndeclaredKeys: false },
    { channelId: "telegram", hasConfigNode: true, keys: ["enabled", "dmPolicy", "allowFrom", "groupPolicy"], acceptsUndeclaredKeys: false },
  ];
  const rendered = renderOpenClawConfig(
    {
      ...plan([policy({ channelId: "openclaw-weixin" }), policy({ channelId: "telegram" })]),
      channelKeySupport: support,
    },
    { gatewayToken: "t" },
  );

  const channels = rendered.config.channels as Record<string, unknown>;
  assert.equal(Object.hasOwn(channels, "openclaw-weixin"), false, "the ID ITSELF is what OpenClaw refuses");
  // The whole point: the channel that IS present still gets configured. Before
  // the fix this assertion was unreachable in production — the push was
  // refused wholesale.
  assert.equal((channels.telegram as Record<string, unknown>).enabled, true);

  assert.deepEqual(
    rendered.disabledChannels.map((finding) => [finding.channelId, finding.code]),
    [["openclaw-weixin", "channel_absent_from_this_box"]],
  );
  // Its own code, not `channel_not_configurable`: that one means "the code is
  // here and none of it is writable" and asks the owner to change something.
  // This one clears by itself when the plugin lands.
  const detail = rendered.disabledChannels[0].detail;
  assert.match(detail, /not set up on this computer yet/);
  assert.match(detail, /nothing you have configured is lost/);
});

test("every channel id the config names is one the installed schema declares", () => {
  // The whole-document form, over the FULL Empyralis channel set — including
  // the four whose plugin contributes their config node (`openclaw-weixin`,
  // `openclaw-zaloclawbot`, `wecom`, `yuanbao`), which every earlier test in
  // this file filtered out and which is exactly why this shipped.
  const schema = schemaFixture() as any;
  const declared = new Set(Object.keys(schema.properties.channels.properties as Record<string, unknown>));
  const everyChannelId = GENERATED_OPENCLAW_MANIFEST.channels.map((channel) => channel.id);
  const pluginOnly = everyChannelId.filter((id) => !declared.has(id));
  assert.ok(pluginOnly.length > 0, "expected the fixture to include plugin-contributed channels");

  const rendered = renderOpenClawConfig(
    {
      ...plan(everyChannelId.map((channelId) => policy({ channelId }))),
      channelKeySupport: resolveOpenClawChannelKeySupport(schema, everyChannelId),
    },
    { gatewayToken: "t" },
  );

  for (const channelId of Object.keys(rendered.config.channels as object)) {
    assert.ok(declared.has(channelId), `channels.${channelId} is not a channel id this box has`);
  }
  // Omitted, but never silently: every one of them is reported.
  const reported = new Set(rendered.disabledChannels.map((finding) => finding.channelId));
  for (const channelId of pluginOnly) {
    assert.ok(reported.has(channelId), `${channelId} was dropped from the config without saying so`);
  }
});

test("no Empyralis channel can ever be one of OpenClaw's `configurable: false` ids", () => {
  // The one gap between "the installed schema declares a node" (what this
  // module reads) and "the validator accepts the id" (what actually decides).
  // Their rule is `bundled metadata WHERE configurable !== false`, and
  // `qa-channel` is the only entry in the pinned build that fails it: it has a
  // full schema node and is still refused with `unknown channel id`, measured.
  //
  // It is unreachable because it is not in their channel REGISTRY, so it is
  // not in the generated manifest either — a second, independent source from
  // the schema. Asserted rather than assumed, because the day a real channel
  // is marked non-configurable this file should fail rather than the box.
  const manifestIds = GENERATED_OPENCLAW_MANIFEST.channels.map((channel) => channel.id);
  assert.equal(manifestIds.includes("qa-channel"), false);

  // And if one ever were, it would arrive through the manifest — which is
  // generated from `openclaw channels list`, the registry that excludes it. So
  // the guard that matters is that every id Empyralis renders came from there.
  assert.ok(manifestIds.length > 20);
  for (const channelId of Object.keys(OPENCLAW_CHANNEL_POLICY_SHAPES)) {
    assert.ok(manifestIds.includes(channelId), `${channelId} has a shape but is not a registry channel`);
  }
});

test("an unknown channel id is never configured from a guess", () => {
  // `matrix` used to stand in for "unknown" because the shape table covered
  // only five channels by hand. It is a real, known channel now that the table
  // is derived from OpenClaw's own schema, so this needs an id that genuinely
  // has no policy semantics anywhere — the case where guessing an
  // authorization mapping would be a silently broken gate.
  const unknown = "not-a-real-openclaw-channel";
  assert.equal(
    GENERATED_OPENCLAW_MANIFEST.channels.some((channel) => channel.id === unknown),
    false,
  );
  const rendered = renderOpenClawConfig(plan([policy({ channelId: unknown })]), { gatewayToken: "t" });
  assert.equal(channelBlock(rendered, unknown).enabled, false);
  assert.equal(rendered.disabledChannels.length, 1);
});

// ── determinism / fingerprint ─────────────────────────────────────────────

test("rendering is deterministic and the fingerprint ignores secrets only", () => {
  const channels = [policy({ channelId: "feishu", dmPolicy: { mode: "allowlist", allowlist: ["z", "a"] } })];
  const a = renderOpenClawConfig(plan(channels), { gatewayToken: "token-one" });
  const b = renderOpenClawConfig(plan(channels), { gatewayToken: "token-two" });
  assert.equal(a.fingerprint, b.fingerprint, "rotating the gateway token must not read as a policy change");
  assert.equal(canonicalJson(a.redactedConfig), canonicalJson(b.redactedConfig));
  assert.notEqual((a.config.gateway as Record<string, unknown>).auth, undefined);

  const changed = renderOpenClawConfig(
    plan([policy({ channelId: "feishu", dmPolicy: { mode: "open", allowlist: [] } })]),
    { gatewayToken: "token-one" },
  );
  assert.notEqual(a.fingerprint, changed.fingerprint, "a policy change must always change the fingerprint");
  // And the secret must never be in the hashed document.
  assert.equal(JSON.stringify(a.redactedConfig).includes("token-one"), false);
});

// ── lockdown ──────────────────────────────────────────────────────────────

test("the generated config satisfies its own lockdown", () => {
  const rendered = renderOpenClawConfig(plan([policy({ channelId: "feishu" })]), { gatewayToken: "long-token" });
  assert.deepEqual(findOpenClawLockdownViolations(rendered.config), []);
});

test("every lockdown item is actually checked", () => {
  const rendered = renderOpenClawConfig(plan([]), { gatewayToken: "long-token" });
  const mutate = (mutator: (config: Record<string, any>) => void): string[] => {
    const copy = JSON.parse(JSON.stringify(rendered.config));
    mutator(copy);
    return findOpenClawLockdownViolations(copy).map((violation) => violation.code);
  };
  assert.deepEqual(mutate((c) => (c.gateway.bind = "lan")), ["gateway_bind_not_loopback"]);
  assert.deepEqual(mutate((c) => (c.gateway.auth.mode = "none")), ["gateway_auth_mode_not_token"]);
  assert.deepEqual(mutate((c) => (c.gateway.mode = "remote")), ["gateway_mode_not_local"]);
  assert.deepEqual(mutate((c) => (c.discovery.mdns.mode = "minimal")), ["mdns_not_off"]);
  assert.deepEqual(mutate((c) => (c.gateway.controlUi.enabled = true)), ["control_ui_enabled"]);
  assert.deepEqual(mutate((c) => (c.gateway.tailscale.mode = "funnel")), ["tailscale_publish_enabled"]);
  assert.deepEqual(mutate((c) => (c.gateway.remote.enabled = true)), ["remote_gateway_enabled"]);
  assert.deepEqual(mutate((c) => (c.discovery.wideArea.enabled = true)), ["wide_area_discovery_enabled"]);
  assert.deepEqual(mutate((c) => (c.gateway.auth.token = "")), ["gateway_auth_token_missing"]);
  assert.deepEqual(
    mutate((c) => (c.models.providers = { anthropic: { apiKey: "sk-live" } })),
    ["model_provider_credential_configured"],
  );
  assert.deepEqual(
    mutate((c) => (c.security.audit.suppressions = [{ checkId: "gateway.bind_no_auth" }])),
    ["security_audit_suppressions_present"],
  );
});

test("brain off: model provider credentials never reach the child environment", () => {
  const env = {
    PATH: "/usr/bin",
    ANTHROPIC_API_KEY: "sk-ant",
    OPENAI_API_KEY: "sk-oai",
    AWS_BEARER_TOKEN_BEDROCK: "x",
    SOME_OTHER_API_KEY: "y",
    OLLAMA_HOST: "http://127.0.0.1:11434",
    EMPYRALIS_BRIDGE_TOKEN: "keep-me",
  } as NodeJS.ProcessEnv;
  const clean = sanitizeOpenClawChildEnv(env);
  assert.deepEqual(Object.keys(clean).sort(), ["EMPYRALIS_BRIDGE_TOKEN", "PATH"]);
  assert.deepEqual(detectForbiddenCredentialEnvNames(env), [
    "ANTHROPIC_API_KEY",
    "AWS_BEARER_TOKEN_BEDROCK",
    "OLLAMA_HOST",
    "OPENAI_API_KEY",
    "SOME_OTHER_API_KEY",
  ]);
});

test("the supervised environment is an allowlist, so nothing can be inherited into it", () => {
  const env = buildOpenClawSupervisedEnv({
    profile: "acme",
    homeDir: "/Users/x",
    pathEnv: "/usr/bin",
    bridgeToken: "bridge",
    bridgeEndpointUrl: "http://127.0.0.1:8790/openclaw/inbound",
  });
  assert.deepEqual(Object.keys(env).sort(), [
    "EMPYRALIS_BRIDGE_ENDPOINT_URL",
    "EMPYRALIS_BRIDGE_QUEUE_FILE",
    "EMPYRALIS_BRIDGE_TOKEN",
    "HOME",
    "OPENCLAW_PROFILE",
    "PATH",
  ]);
});

// ── isolation ─────────────────────────────────────────────────────────────

test("profiles are validated, never sanitized, and can never be the operator's own", () => {
  assert.equal(assertValidOpenClawProfile("acme-1"), "acme-1");
  assert.throws(() => assertValidOpenClawProfile("../../etc"), /Invalid OpenClaw profile/);
  assert.throws(() => assertValidOpenClawProfile(""), /Invalid OpenClaw profile/);
  assert.throws(() => assertValidOpenClawProfile("Acme"), /Invalid OpenClaw profile/);
  assert.throws(() => assertValidOpenClawProfile("dev"), /reserved/);
});

test("every CLI invocation carries --profile", async () => {
  const calls: string[][] = [];
  const cli = new OpenClawCli({
    profile: "acme",
    env: { PATH: "/usr/bin" },
    exec: async (args) => {
      calls.push(args);
      return { code: 0, stdout: "{}", stderr: "" };
    },
  });
  await cli.version();
  await cli.configSchema();
  await cli.configPatch("/tmp/x.json");
  await cli.securityAuditJson();
  assert.equal(calls.length, 4);
  for (const call of calls) {
    assert.equal(call[0], "--profile");
    assert.equal(call[1], "acme");
  }
  // A secret must never appear in argv (`ps` is world-readable).
  assert.equal(calls.flat().some((arg) => arg.includes("token")), false);
});

test("the supervisor unit is per-profile, loopback-bound, and passes no token in argv", () => {
  const unit = resolveExpectedOpenClawSupervisorUnit({
    profile: "acme",
    binaryPath: "/opt/homebrew/bin/openclaw",
    gatewayPort: 18789,
    environment: { HOME: "/Users/x", PATH: "/usr/bin" },
    platform: "darwin",
    homeDir: "/Users/x",
    logDir: "/Users/x/.empyralis/gateway/logs",
  });
  assert.ok(unit);
  assert.equal(unit.name, openClawSupervisorLabel("acme"));
  assert.match(unit.contents, /<string>--profile<\/string>/);
  assert.match(unit.contents, /<string>loopback<\/string>/);
  assert.match(unit.contents, /EnvironmentVariables/);
  assert.equal(/--token/.test(unit.contents), false);
  // Two customers never share a job.
  assert.notEqual(openClawSupervisorLabel("acme"), openClawSupervisorLabel("globex"));
});

// ── drift ─────────────────────────────────────────────────────────────────

test("drift is one-directional: generated paths must match, extra paths are not drift", () => {
  const generated = { gateway: { bind: "loopback", port: 1 }, channels: { feishu: { enabled: true } } };
  assert.deepEqual(findConfigDrift(generated, JSON.parse(JSON.stringify(generated))), []);

  // OpenClaw's own defaults and its meta.lastTouchedAt must not read as drift.
  const withExtras = {
    ...JSON.parse(JSON.stringify(generated)),
    meta: { lastTouchedAt: "2026-08-08" },
    gateway: { bind: "loopback", port: 1, handshakeTimeoutMs: 5000 },
  };
  assert.deepEqual(findConfigDrift(generated, withExtras), []);

  // But a changed generated path is drift, named by path.
  const changed = JSON.parse(JSON.stringify(generated));
  changed.channels.feishu.enabled = false;
  assert.deepEqual(findConfigDrift(generated, changed), ["channels.feishu.enabled"]);

  // An absent subtree is drift on every path under it.
  assert.deepEqual(findConfigDrift(generated, { gateway: { bind: "loopback", port: 1 } }), [
    "channels.feishu.enabled",
  ]);

  // The redacted token can never be compared, so it is never drift.
  assert.deepEqual(
    findConfigDrift({ gateway: { auth: { token: "real" } } }, { gateway: { auth: { token: "__OPENCLAW_REDACTED__" } } }),
    [],
  );
});

test("flattenConfigPaths keeps empty objects and arrays as leaves", () => {
  const flat = flattenConfigPaths({ a: { b: {} }, c: [1, 2] });
  assert.deepEqual([...flat.keys()].sort(), ["a.b", "c"]);
});

// ── audit gate ────────────────────────────────────────────────────────────

test("audit: critical and warn block; info does not; inconclusive probes do not", () => {
  const findings = [
    { checkId: "summary.attack_surface", severity: "info", title: "" },
    { checkId: "sandbox.browser_container.docker_probe_timeout", severity: "warn", title: "" },
    { checkId: "gateway.token_too_short", severity: "warn", title: "short token" },
    { checkId: "gateway.bind_no_auth", severity: "critical", title: "no auth" },
  ];
  assert.deepEqual(
    blockingAuditFindings(findings).map((finding) => finding.checkId),
    ["gateway.token_too_short", "gateway.bind_no_auth"],
  );
  assert.deepEqual(blockingAuditFindings([findings[0], findings[1]]), []);
});

// ── the installed schema still matches the transcription ──────────────────

test("channel shape table matches the pinned schema fixture, and finds drift when it does not", () => {
  const schema = JSON.parse(fs.readFileSync(fixturePath(), "utf8"));
  assert.deepEqual(auditOpenClawChannelShapes(schema, [...OPENCLAW_TRANSPORT_CHANNEL_IDS]), []);

  const mutated = JSON.parse(JSON.stringify(schema));
  delete mutated.properties.channels.properties.feishu.properties.requireMention;
  const findings = auditOpenClawChannelShapes(mutated, ["feishu"]);
  assert.equal(findings.length, 1);
  assert.equal(findings[0].code, "require_mention_support_changed");

  const missing = JSON.parse(JSON.stringify(schema));
  delete missing.properties.channels.properties.zalo;
  assert.equal(auditOpenClawChannelShapes(missing, ["zalo"])[0].code, "channel_missing_from_schema");
});

test("plugin hook suppression is discovered from the schema, not assumed to be WhatsApp-only", () => {
  const schema = JSON.parse(fs.readFileSync(fixturePath(), "utf8"));

  // 2026-08-14 full OpenClaw channel cutover: WhatsApp is now IN
  // OPENCLAW_TRANSPORT_CHANNEL_IDS (OPENCLAW_CUT_OVER_CHANNEL_IDS covers it),
  // so the real transported set now surfaces exactly the one plugin hook
  // WhatsApp's schema declares — this is the discovery mechanism actually
  // doing its job on the real active set, not a regression.
  assert.deepEqual(resolveOpenClawPluginHookFlags(schema, [...OPENCLAW_TRANSPORT_CHANNEL_IDS]).enable, [
    { channelId: "whatsapp", flag: "messageReceived" },
  ]);
  // WhatsApp does, and is handled now that it is transported.
  assert.deepEqual(resolveOpenClawPluginHookFlags(schema, ["whatsapp"]).enable, [
    { channelId: "whatsapp", flag: "messageReceived" },
  ]);

  // A channel that GROWS the same switch is covered with no code change.
  const future = JSON.parse(JSON.stringify(schema));
  future.properties.channels.properties.line.properties.pluginHooks = {
    type: "object",
    properties: { messageReceived: { type: "boolean" } },
  };
  assert.deepEqual(resolveOpenClawPluginHookFlags(future, ["line"]).enable, [
    { channelId: "line", flag: "messageReceived" },
  ]);

  // A shape we do not know how to satisfy is reported, never guessed at.
  const odd = JSON.parse(JSON.stringify(schema));
  odd.properties.channels.properties.line.properties.pluginHooks = {
    type: "object",
    properties: { messageReceived: { type: "string" } },
  };
  assert.equal(resolveOpenClawPluginHookFlags(odd, ["line"]).findings[0].code, "unhandled_plugin_hook");
});

test("discovered plugin hooks are switched on in the rendered config", () => {
  const rendered = renderOpenClawConfig(
    { ...plan([policy({ channelId: "line" })]), pluginHookFlags: [{ channelId: "line", flag: "messageReceived" }] },
    { gatewayToken: "t" },
  );
  assert.deepEqual(channelBlock(rendered, "line").pluginHooks, { messageReceived: true });
});

test("every transported channel with a config node has a shape, and one without degrades honestly", () => {
  // Both sides are now derived from the same generated manifest, so this is
  // no longer "did two hand-written lists stay in step" but "does every
  // channel we transport have the policy semantics provisioning needs".
  assert.ok(OPENCLAW_TRANSPORT_CHANNEL_IDS.length > 0);
  assert.ok(Object.keys(OPENCLAW_CHANNEL_POLICY_SHAPES).length > 0);

  const byId = new Map(GENERATED_OPENCLAW_MANIFEST.channels.map((c) => [c.id, c]));
  for (const channelId of OPENCLAW_TRANSPORT_CHANNEL_IDS) {
    const channel = byId.get(channelId);
    assert.ok(channel, `${channelId} is transported but absent from the manifest`);
    assert.equal(
      Object.hasOwn(OPENCLAW_CHANNEL_POLICY_SHAPES, channelId),
      channel.config_schema_present,
      `${channelId}: a shape must exist exactly when OpenClaw declares channels.${channelId}`,
    );
  }

  // The channels with no config node are NOT an oversight: their plugin
  // contributes it only once installed. renderOpenClawConfig must leave such
  // a channel OFF with a stated reason rather than guess at an authorization
  // mapping — a guessed policy is how a gate silently stops being a gate.
  const withoutSchema = OPENCLAW_TRANSPORT_CHANNEL_IDS.filter(
    (id) => !byId.get(id)?.config_schema_present,
  );
  assert.ok(withoutSchema.length > 0, "expected at least one install-gated channel to cover");
  for (const channelId of withoutSchema) {
    const rendered = renderOpenClawConfig(plan([policy({ channelId })]), { gatewayToken: "t" });
    assert.deepEqual(channelBlock(rendered, channelId), { enabled: false });
    assert.equal(rendered.disabledChannels.some((f) => f.channelId === channelId), true);
  }
});

test("a channel a first-party Empyralis runtime owns is never transported", () => {
  // Declaring all of OpenClaw's channels must not produce two runtimes on one
  // account. The split is computed on the Python side and generated into this
  // bundle, so the gateway cannot hold a second opinion about it.
  assert.ok(GENERATED_OPENCLAW_SUPERSEDED_CHANNEL_IDS.length > 0);
  for (const channelId of GENERATED_OPENCLAW_SUPERSEDED_CHANNEL_IDS) {
    assert.equal(
      OPENCLAW_TRANSPORT_CHANNEL_IDS.includes(channelId),
      false,
      `${channelId} is advertised by the gateway while an Empyralis runtime still owns it`,
    );
  }
  const advertised = new Set(openClawTransportChannelKeys());
  for (const channelId of GENERATED_OPENCLAW_SUPERSEDED_CHANNEL_IDS) {
    assert.equal(advertised.has(`openclaw_${channelId}`), false);
  }
  // Active + superseded must partition the manifest — a channel in neither
  // would be silently unreachable, and one in both is the bug above.
  assert.deepEqual(
    [...OPENCLAW_TRANSPORT_CHANNEL_IDS, ...GENERATED_OPENCLAW_SUPERSEDED_CHANNEL_IDS].sort(),
    GENERATED_OPENCLAW_MANIFEST.channels.map((c) => c.id).sort(),
  );
});

test("every transported channel_key is `openclaw_` plus OpenClaw's own id, verbatim", () => {
  // The `openclaw_qq` bug (their id is `qqbot`), made structurally
  // impossible: nothing here types a suffix any more.
  const ids = new Set(GENERATED_OPENCLAW_MANIFEST.channels.map((c) => c.id));
  assert.ok(ids.has("qqbot"));
  assert.equal(ids.has("qq"), false);
  for (const channelKey of openClawTransportChannelKeys()) {
    const channelId = openClawChannelIdFromChannelKey(channelKey);
    assert.ok(channelId, `${channelKey} does not round-trip to an OpenClaw channel id`);
    assert.equal(channelKey, `openclaw_${channelId}`);
    assert.equal(ids.has(channelId), true);
  }
});

// ── the cloud's payload ───────────────────────────────────────────────────

test("policy payload parsing is fail-closed on every axis", () => {
  const ok = parseChannelPolicies([
    {
      channel_id: "feishu",
      dm_policy: { mode: "allowlist", allowlist: ["a", "a", " b "] },
      group_policy: { mode: "allowlist", allowlist: ["g1"] },
    },
  ]);
  assert.deepEqual(ok.errors, []);
  assert.deepEqual(ok.channels[0].dmPolicy.allowlist, ["a", "b"]);
  // Absent require_mention defaults TRUE — the safe side of the one axis
  // Empyralis cannot re-enforce itself.
  assert.equal(ok.channels[0].groupPolicy.requireMention, true);

  // An unknown mode is NEVER coerced to a default.
  assert.equal(
    parseChannelPolicies([{ channel_id: "feishu", dm_policy: { mode: "nope" }, group_policy: { mode: "open" } }])
      .errors.length,
    1,
  );
  // "discord" is a real OpenClaw channel id but NOT a transported one — it
  // stays first-party (see OPENCLAW_CUT_OVER_CHANNEL_IDS's own comment on
  // why Studio business-connector channels were excluded from the 2026-08-14
  // cutover) — so this is still the right example of "channel not in
  // OPENCLAW_TRANSPORT_CHANNEL_IDS", now that telegram itself IS transported.
  assert.equal(
    parseChannelPolicies([{ channel_id: "discord", dm_policy: { mode: "open" }, group_policy: { mode: "open" } }])
      .errors.length,
    1,
  );
  assert.equal(parseChannelPolicies("not an array").errors.length, 1);
});

// ── the orchestration, with a fake CLI ────────────────────────────────────

interface FakeCliState {
  version: string | undefined;
  schema: unknown;
  effective: Record<string, unknown>;
  /** What the instance reports AFTER a successful `config patch`. Defaults to
   *  `effective` (i.e. the write changed nothing), which is how the
   *  "did not take effect" scenarios are expressed. */
  effectiveAfterPatch?: Record<string, unknown>;
  audit: unknown;
  patchCode: number;
  /** channelId -> OpenClaw's own `installed` flag from `channels list`.
   *  Modelled because `installed` is the ONE structural fact that separates
   *  "no plugin" from "no credential"; their send-error prose is not. */
  channelsInstalled?: Record<string, boolean>;
  /** pluginId -> resolved `<name>@<version>` in their install registry. */
  installRecords?: Record<string, string>;
  /**
   * Every `npm` argv this run attempted, in order — the acquisition of the
   * OpenClaw CLI itself.
   *
   * `npmInstall` is deliberately OPT-IN and its absence THROWS rather than
   * defaulting to a stub that succeeds. A provisioning run that reaches for a
   * real `npm install --global` from a unit test is the same class of hazard
   * as a test reaching a live LLM provider (CLAUDE.md): slow, networked,
   * machine-mutating, and green either way. Only a test that says it is
   * testing acquisition gets an npm at all.
   */
  npmCalls?: string[][];
  npmInstall?: "succeeds" | "fails";
  /** Every `plugins install` argv this run attempted, in order. A test that
   *  asserts an install did NOT happen is the entire content of the
   *  idempotency and "only what is needed" claims — an outcome assertion
   *  alone cannot tell a skipped install from a repeated one. */
  installCalls?: string[][];
  /** Makes `plugins install` fail — a 404, or an unreachable registry. */
  installFailure?: { code: number; stderr: string };
  /** `plugins install` exits 0 and prints "Installed plugin: …" while nothing
   *  actually lands. Their CLI's success is a claim; this is what makes the
   *  read-back the only thing entitled to promote a channel to installed. */
  installSilentlyDoesNothing?: boolean;
}

function fakeProvisioner(state: FakeCliState, overrides: Record<string, unknown> = {}) {
  const files = new Map<string, string>();
  state.installCalls = state.installCalls ?? [];
  state.npmCalls = state.npmCalls ?? [];
  const cli = new OpenClawCli({
    profile: "acme",
    env: { PATH: "/usr/bin" },
    exec: async (args) => {
      const rest = args.slice(2);
      if (rest[0] === "channels" && rest[1] === "list") {
        const chat: Record<string, unknown> = {};
        for (const channel of GENERATED_OPENCLAW_MANIFEST.channels) {
          chat[channel.id] = { installed: state.channelsInstalled?.[channel.id] === true };
        }
        return { code: 0, stdout: JSON.stringify({ chat }), stderr: "" };
      }
      if (rest[0] === "plugins" && rest[1] === "registry") {
        const installRecords: Record<string, unknown> = {};
        for (const [pluginId, spec] of Object.entries(state.installRecords ?? {})) {
          installRecords[pluginId] = { resolvedSpec: spec };
        }
        return { code: 0, stdout: JSON.stringify({ persisted: { installRecords } }), stderr: "" };
      }
      if (rest[0] === "plugins" && rest[1] === "install") {
        state.installCalls!.push(rest);
        if (state.installFailure) {
          return { code: state.installFailure.code, stdout: "", stderr: state.installFailure.stderr };
        }
        // A real install makes both read-back sources agree; the spec is the
        // one their resolver settled on, which is NOT always the one asked
        // for (`@openclaw/feishu` -> `@openclaw/feishu@2026.6.10`).
        const spec = String(rest[2]);
        const pkg = spec.includes("@", 1) ? spec.slice(0, spec.lastIndexOf("@")) : spec;
        const entry = GENERATED_OPENCLAW_MANIFEST.channels.find(
          (channel) => channel.plugin_install?.npm_package === pkg,
        );
        if (entry?.plugin_install && !state.installSilentlyDoesNothing) {
          state.installRecords = {
            ...(state.installRecords ?? {}),
            [entry.plugin_install.plugin_id]: `${pkg}@${OPENCLAW_PINNED_VERSION}`,
          };
          state.channelsInstalled = { ...(state.channelsInstalled ?? {}), [entry.id]: true };
        }
        return { code: 0, stdout: `Installed plugin: ${entry?.id ?? spec}`, stderr: "" };
      }
      if (rest[0] === "--version") {
        return state.version === undefined
          ? { code: 127, stdout: "", stderr: "not found" }
          : { code: 0, stdout: state.version, stderr: "" };
      }
      if (rest[0] === "config" && rest[1] === "schema") {
        return { code: 0, stdout: JSON.stringify(state.schema), stderr: "" };
      }
      if (rest[0] === "config" && rest[1] === "patch") {
        // A successful patch makes the instance agree with what was written,
        // unless the scenario deliberately says otherwise.
        if (state.patchCode === 0 && state.effectiveAfterPatch) state.effective = state.effectiveAfterPatch;
        return { code: state.patchCode, stdout: "", stderr: state.patchCode === 0 ? "" : "rejected" };
      }
      if (rest[0] === "config" && rest[1] === "get") {
        const subtree = rest[2];
        if (!(subtree in state.effective)) return { code: 1, stdout: "", stderr: "not found" };
        return { code: 0, stdout: JSON.stringify(state.effective[subtree]), stderr: "" };
      }
      if (rest[0] === "security" && rest[1] === "audit") {
        return { code: 0, stdout: JSON.stringify(state.audit), stderr: "" };
      }
      return { code: 1, stdout: "", stderr: `unexpected ${rest.join(" ")}` };
    },
  });
  return new OpenClawProvisioner({
    plan: plan([policy({ channelId: "feishu" })]),
    secrets: { gatewayToken: "a-suitably-long-gateway-token" },
    bridgeToken: "bridge",
    bridgeEndpointUrl: "http://127.0.0.1:8790/openclaw/inbound",
    cli,
    env: { PATH: "/usr/bin" },
    platform: "darwin",
    homeDir: "/tmp/fake-home",
    stateDir: "/tmp/fake-state",
    binaryPath: "/usr/bin/openclaw",
    fs: {
      readFile: async (filePath) => {
        const found = files.get(filePath);
        if (found === undefined) throw new Error("ENOENT");
        return found;
      },
      writeFile: async (filePath, contents) => {
        files.set(filePath, contents);
      },
      mkdir: async () => undefined,
      rm: async (filePath) => {
        files.delete(filePath);
      },
    },
    registerJob: async () => undefined,
    probeHealth: async () => true,
    runNpm: async (args: string[]) => {
      state.npmCalls!.push(args);
      if (state.npmInstall === undefined) {
        throw new Error(
          "this test shelled out to npm without opting in; set npmInstall to declare that it is testing acquisition",
        );
      }
      if (state.npmInstall === "fails") {
        return { code: 1, stdout: "", stderr: "npm ERR! network ETIMEDOUT" };
      }
      // A real `npm i -g openclaw@<pin>` makes the CLI answer as the pin.
      state.version = OPENCLAW_PINNED_VERSION;
      return { code: 0, stdout: "added 1 package", stderr: "" };
    },
    ...overrides,
  });
}

function schemaFixture(): unknown {
  return JSON.parse(fs.readFileSync(fixturePath(), "utf8"));
}

/** What the instance reads back as once the generated config is in force.
 *
 * `installedPluginIds` must mirror what the run's install pass will produce,
 * because `plugins.allow` is generated FROM that — a helper that ignored it
 * would make every install scenario report residual drift. */
function effectiveFor(
  channels: EmpyralisChannelPolicy[],
  installedPluginIds: readonly string[] = [],
): Record<string, unknown> {
  const rendered = renderOpenClawConfig(
    {
      ...plan(channels),
      installedChannelPluginIds: installedPluginIds,
      // The same discovery the provisioner does, from the same fixture it is
      // handed — otherwise a channel that cannot take a key renders one way
      // here and another way in the run, and every such test reports drift
      // that does not exist.
      channelKeySupport: resolveOpenClawChannelKeySupport(
        schemaFixture(),
        channels.map((channel) => channel.channelId),
      ),
    },
    { gatewayToken: "a-suitably-long-gateway-token" },
  );
  const effective = JSON.parse(JSON.stringify(rendered.config)) as Record<string, any>;
  effective.gateway.auth.token = "__OPENCLAW_REDACTED__";
  return effective;
}

test("a version mismatch refuses BEFORE anything is written, when installing is off", async () => {
  const provisioner = fakeProvisioner(
    {
      version: "2026.7.0",
      schema: schemaFixture(),
      effective: {},
      audit: { findings: [] },
      patchCode: 0,
    },
    { installRuntime: false },
  );
  const result = await provisioner.provision();
  assert.equal(result.status, "refused");
  assert.equal(result.refusal?.code, "openclaw_version_mismatch");
  assert.equal(result.configFingerprint, undefined, "nothing may be generated before the pin is satisfied");
});

test("a box that already has the pinned CLI executes no npm at all", async () => {
  // The idempotency claim, and the only assertion that can carry it: an
  // outcome of "already_installed" is produced just as happily by a run that
  // reinstalled. On a boot-time reconcile with no network, a single
  // `npm install --global` is a multi-minute stall on a box that is correct.
  const state: FakeCliState = {
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: effectiveFor([policy({ channelId: "feishu" })]),
    audit: { findings: [] },
    patchCode: 0,
  };
  const result = await fakeProvisioner(state).provision();
  assert.equal(result.status, "provisioned");
  assert.equal(result.runtimeInstall?.action, "already_installed");
  assert.equal(state.npmCalls!.length, 0, "a correct box must shell out to nothing");
});

test("a box with no OpenClaw installs the pin, once, and then provisions", async () => {
  const state: FakeCliState = {
    version: undefined,
    npmInstall: "succeeds",
    schema: schemaFixture(),
    effective: effectiveFor([policy({ channelId: "feishu" })]),
    audit: { findings: [] },
    patchCode: 0,
  };
  const result = await fakeProvisioner(state).provision();
  assert.equal(result.status, "provisioned", result.refusal?.detail);
  assert.equal(result.runtimeInstall?.action, "installed");
  // Call COUNT, not merely "an install happened" — a double install is
  // satisfied by an existence assertion just as happily as a correct one.
  assert.equal(state.npmCalls!.length, 1);
  const args = state.npmCalls![0];
  assert.ok(args.includes("--global"), args.join(" "));
  assert.ok(
    args.includes(`openclaw@${OPENCLAW_PINNED_VERSION}`),
    "the spec must be the exact pin, never a range: " + args.join(" "),
  );
});

test("an off-pin CLI is replaced with the pin rather than refused", async () => {
  // `npm i -g <name>@<exact>` downgrades as willingly as it upgrades, which is
  // the entire point of pinning: a box that drifted forward is brought back,
  // not left running three transcribed contracts against a build nobody
  // verified.
  const state: FakeCliState = {
    version: "2026.7.0",
    npmInstall: "succeeds",
    schema: schemaFixture(),
    effective: effectiveFor([policy({ channelId: "feishu" })]),
    audit: { findings: [] },
    patchCode: 0,
  };
  const result = await fakeProvisioner(state).provision();
  assert.equal(result.status, "provisioned", result.refusal?.detail);
  assert.equal(result.runtimeInstall?.action, "installed");
  assert.equal(result.version.observed, OPENCLAW_PINNED_VERSION);
});

test("an install that cannot reach npm refuses as ITSELF, and writes nothing", async () => {
  // Never flattened into `openclaw_not_installed`. The two need different
  // answers from whoever reads the result, and the second one reads as a box
  // nobody bothered to set up.
  const state: FakeCliState = {
    version: undefined,
    npmInstall: "fails",
    schema: schemaFixture(),
    effective: {},
    audit: { findings: [] },
    patchCode: 0,
  };
  const result = await fakeProvisioner(state).provision();
  assert.equal(result.status, "refused");
  assert.equal(result.refusal?.code, "openclaw_runtime_install_failed");
  assert.equal(result.configFingerprint, undefined, "nothing may be generated before the pin is satisfied");
  assert.equal(result.runtimeInstall?.action, "failed");
});

test("the baseline (no channels at all) provisions and satisfies the lockdown", async () => {
  // What a freshly provisioned box does on its own, with no cloud round trip
  // and nobody pressing anything: install, lock down, audit, supervise. Zero
  // channels is zero inbound policy, not a weaker instance — every lockdown
  // expectation is channel-independent, so this is the state a box should sit
  // in until its owner enables something.
  const state: FakeCliState = {
    version: undefined,
    npmInstall: "succeeds",
    schema: schemaFixture(),
    effective: effectiveFor([]),
    audit: { findings: [] },
    patchCode: 0,
  };
  const result = await fakeProvisioner(state, { plan: plan([]) }).provision();
  assert.equal(result.status, "provisioned", result.refusal?.detail);
  assert.deepEqual(result.lockdownViolations, []);
  assert.deepEqual(result.driftedPaths, []);
  assert.equal(result.channelPlugins.length, 0, "an empty policy fetches no third-party plugin");
  assert.equal(state.installCalls!.length, 0);
  assert.ok(result.supervisor?.supported, "the baseline still installs supervision");
});

test("a happy path provisions, records a fingerprint, and reports no drift", async () => {
  const provisioner = fakeProvisioner({
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: effectiveFor([policy({ channelId: "feishu" })]),
    audit: { findings: [{ checkId: "summary.attack_surface", severity: "info", title: "" }] },
    patchCode: 0,
  });
  const result = await provisioner.provision();
  assert.equal(result.status, "provisioned", result.refusal?.detail);
  assert.deepEqual(result.driftedPaths, []);
  assert.ok(result.configFingerprint);
  assert.equal(result.healthy, true);
  assert.equal(result.supervisor?.supported, true);
});

test("the provisioner hands the renderer the installed schema's key support", async () => {
  // The WIRING, not the renderer. renderOpenClawConfig defaults key support to
  // "assume acceptable" so that pure callers keep working, which means a
  // provisioner that forgot to pass it would look perfect in every unit test
  // of the renderer and still write `allowFrom` to a channel that refuses it —
  // taking the whole box's provisioning down with it. Asserted against the
  // bytes actually handed to `openclaw config patch`.
  let patched: Record<string, any> | undefined;
  const files = new Map<string, string>();
  const provisioner = fakeProvisioner(
    {
      version: OPENCLAW_PINNED_VERSION,
      schema: schemaFixture(),
      effective: effectiveFor([policy({ channelId: "matrix" }), policy({ channelId: "clickclack" })]),
      audit: { findings: [] },
      patchCode: 0,
    },
    {
      // matrix rejects `allowFrom` (it moved under a nested `dm` object) and
      // has no `dmPolicy`; clickclack has neither policy key. Both are
      // ordinary, enabled channels — not edge cases anyone opted into.
      plan: plan([policy({ channelId: "matrix" }), policy({ channelId: "clickclack" })]),
      fs: {
        readFile: async (filePath: string) => {
          const found = files.get(filePath);
          if (found === undefined) throw new Error("ENOENT");
          return found;
        },
        writeFile: async (filePath: string, contents: string) => {
          files.set(filePath, contents);
          if (filePath.endsWith("empyralis-generated.config.json")) patched = JSON.parse(contents);
        },
        mkdir: async () => undefined,
        rm: async (filePath: string) => {
          files.delete(filePath);
        },
      },
    },
  );
  const result = await provisioner.provision();
  assert.equal(result.status, "provisioned", result.refusal?.detail);

  const matrix = patched?.channels.matrix as Record<string, unknown>;
  assert.equal(Object.hasOwn(matrix, "allowFrom"), false, "matrix has no top-level allowFrom");
  assert.equal(Object.hasOwn(matrix, "dmPolicy"), false);
  assert.equal(matrix.groupPolicy, "open", "the keys it DOES have are still written");

  const clickclack = patched?.channels.clickclack as Record<string, unknown>;
  assert.equal(Object.hasOwn(clickclack, "dmPolicy"), false);
  assert.equal(Object.hasOwn(clickclack, "groupPolicy"), false);
  assert.deepEqual(clickclack.allowFrom, ["*"], "its one real lever is still pulled");

  // And the owner is told, per channel, rather than left to infer it.
  assert.deepEqual(
    [...new Set(result.widenings.map((finding) => finding.channelId))].sort(),
    ["clickclack", "matrix"],
  );
});

test("the supervised unit points at WHERE openclaw is, not at what it is called", async () => {
  // The wiring, not the resolver. Every gateway reaches provisioning through
  // OpenClawProvisioningRuntime, which had no configured binary path on
  // essentially every box and defaulted to the bare string "openclaw" —
  // correct for OpenClawCli's execFile (it does a PATH lookup) and fatal for
  // a supervisor unit (launchd resolves ProgramArguments[0] against its OWN
  // minimal PATH, systemd rejects a non-absolute ExecStart at load). A test
  // of the resolver alone stays green while the provisioner passes the raw
  // configured value straight through, which is exactly what it did.
  const binDir = fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-openclaw-bin-"));
  const binary = path.join(binDir, "openclaw");
  fs.writeFileSync(binary, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  try {
    const state: FakeCliState = {
      version: OPENCLAW_PINNED_VERSION,
      schema: schemaFixture(),
      effective: effectiveFor([policy({ channelId: "feishu" })]),
      audit: { findings: [] },
      patchCode: 0,
    };
    const result = await fakeProvisioner(state, {
      // The poison value itself, plus a PATH on which it genuinely resolves.
      // binDir is FIRST, so this resolves to the file written above even on a
      // machine that has a real openclaw installed.
      binaryPath: "openclaw",
      env: { PATH: `${binDir}:/usr/bin:/bin` },
    }).provision();

    assert.equal(result.status, "provisioned", result.refusal?.detail);
    assert.equal(result.supervisor?.supported, true);
    assert.equal(result.supervisor?.unsupportedReason, undefined);
    const contents = result.supervisor?.definition?.contents ?? "";
    assert.match(contents, new RegExp(`<string>${binary.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}</string>`));
    assert.equal(
      /<string>openclaw<\/string>/.test(contents),
      false,
      "a bare program name is a job that dies with status 78 and logs nothing",
    );
  } finally {
    fs.rmSync(binDir, { recursive: true, force: true });
  }
});

test("nothing to point at means NO unit, with a reason — never a unit that cannot start", async () => {
  const written: string[] = [];
  {
    const state: FakeCliState = {
      version: OPENCLAW_PINNED_VERSION,
      schema: schemaFixture(),
      effective: effectiveFor([policy({ channelId: "feishu" })]),
      audit: { findings: [] },
      patchCode: 0,
    };
    const files = new Map<string, string>();
    const result = await fakeProvisioner(state, {
      // A non-absolute name that cannot resolve on ANY box, so this test does
      // not depend on whether the machine running it happens to have a real
      // openclaw installed. The PATH is a real one so `sh` itself resolves.
      binaryPath: "empyralis-openclaw-that-is-not-installed",
      env: { PATH: "/usr/bin:/bin" },
      fs: {
        readFile: async (filePath: string) => {
          const found = files.get(filePath);
          if (found === undefined) throw new Error("ENOENT");
          return found;
        },
        writeFile: async (filePath: string, contents: string) => {
          written.push(filePath);
          files.set(filePath, contents);
        },
        mkdir: async () => undefined,
        rm: async (filePath: string) => {
          files.delete(filePath);
        },
      },
    }).provision();

    // The RUN still succeeds — a box with no transport binary is still locked
    // down, audited and recorded; only supervision is honestly absent.
    assert.equal(result.status, "provisioned", result.refusal?.detail);
    assert.equal(result.supervisor?.supported, false);
    assert.equal(result.supervisor?.unsupportedReason, "binary_path_not_absolute");
    assert.equal(result.supervisor?.definition, null);
    assert.equal(
      written.some((filePath) => filePath.includes("LaunchAgents")),
      false,
      "nothing may write a plist whose program does not exist",
    );
  }
});

test("drift is DETECTED, named, and corrected — never silently tolerated", async () => {
  const clean = effectiveFor([policy({ channelId: "feishu" })]);
  const drifted = JSON.parse(JSON.stringify(clean)) as Record<string, any>;
  // Exactly what OpenClaw's own in-chat `/activation always` command does to
  // a config Empyralis is the sole author of.
  drifted.channels.feishu.requireMention = false;
  drifted.discovery.mdns.mode = "minimal";

  const journalled: Array<{ type: string; payload: Record<string, unknown> }> = [];
  const provisioner = fakeProvisioner(
    {
      version: OPENCLAW_PINNED_VERSION,
      schema: schemaFixture(),
      effective: drifted,
      effectiveAfterPatch: clean,
      audit: { findings: [] },
      patchCode: 0,
    },
    {
      record: async (type: string, payload: Record<string, unknown>) => {
        journalled.push({ type, payload });
      },
    },
  );

  const result = await provisioner.provision();
  assert.equal(result.status, "provisioned", result.refusal?.detail);
  assert.deepEqual(result.driftedPaths, ["channels.feishu.requireMention", "discovery.mdns.mode"]);
  assert.equal(result.configChanged, true);
  const driftEvent = journalled.find((entry) => entry.type === "openclaw.provision.drift_corrected");
  assert.ok(driftEvent, "drift must always be journaled, never quietly fixed");
  assert.equal(driftEvent?.payload.resolution, "regenerated_from_empyralis_policy");
  assert.deepEqual(driftEvent?.payload.drifted_paths, [
    "channels.feishu.requireMention",
    "discovery.mdns.mode",
  ]);
});

test("a lockdown violation surviving the write REFUSES the instance", async () => {
  const bad = effectiveFor([policy({ channelId: "feishu" })]) as Record<string, any>;
  bad.gateway.bind = "lan";
  const provisioner = fakeProvisioner({
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: bad,
    audit: { findings: [] },
    patchCode: 0,
  });
  const result = await provisioner.provision();
  assert.equal(result.status, "refused");
  assert.equal(result.refusal?.code, "openclaw_lockdown_violated");
  assert.equal(result.lockdownViolations[0].code, "gateway_bind_not_loopback");
});

test("a policy that did not take effect REFUSES rather than reporting success", async () => {
  const notApplied = effectiveFor([policy({ channelId: "feishu" })]) as Record<string, any>;
  notApplied.channels.feishu.requireMention = false;
  const provisioner = fakeProvisioner({
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: notApplied,
    audit: { findings: [] },
    patchCode: 0,
  });
  const result = await provisioner.provision();
  assert.equal(result.status, "refused");
  assert.equal(result.refusal?.code, "openclaw_policy_not_applied");
  assert.match(result.refusal?.detail ?? "", /channels\.feishu\.requireMention/);
});

test("a non-clean security audit REFUSES", async () => {
  const provisioner = fakeProvisioner({
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: effectiveFor([policy({ channelId: "feishu" })]),
    audit: { findings: [{ checkId: "gateway.bind_no_auth", severity: "critical", title: "no auth" }] },
    patchCode: 0,
  });
  const result = await provisioner.provision();
  assert.equal(result.status, "refused");
  assert.equal(result.refusal?.code, "openclaw_security_audit_not_clean");
});

test("a schema whose shape moved REFUSES before generating anything", async () => {
  const mutated = JSON.parse(JSON.stringify(schemaFixture()));
  mutated.properties.channels.properties.feishu.properties.dmPolicy.enum = ["open"];
  const provisioner = fakeProvisioner({
    version: OPENCLAW_PINNED_VERSION,
    schema: mutated,
    effective: {},
    audit: { findings: [] },
    patchCode: 0,
  });
  const result = await provisioner.provision();
  assert.equal(result.status, "refused");
  assert.equal(result.refusal?.code, "openclaw_channel_shape_drift");
});

test("read-back covers every top-level key the generator writes", async () => {
  // A generated subtree missing from VERIFIED_CONFIG_SUBTREES reads back as
  // absent and reports permanent, uncorrectable drift on every run — a
  // failure that looks exactly like success until someone reads the payload.
  const rendered = renderOpenClawConfig(plan([policy({ channelId: "feishu" })]), { gatewayToken: "t" });
  const effective = effectiveFor([policy({ channelId: "feishu" })]);
  const readSubtrees = new Set<string>();
  const provisioner = fakeProvisioner({
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective,
    audit: { findings: [] },
    patchCode: 0,
  });
  const cli = (provisioner as unknown as { options: { cli: OpenClawCli } }).options.cli;
  const originalRun = cli.run.bind(cli);
  cli.run = async (args: string[], opts?: { timeoutMs?: number }) => {
    if (args[0] === "config" && args[1] === "get") readSubtrees.add(args[2]);
    return originalRun(args, opts);
  };
  await provisioner.provision();
  for (const key of Object.keys(rendered.config)) {
    assert.ok(readSubtrees.has(key), `generated top-level key "${key}" is never read back for verification`);
  }
});

test("`dmPolicy: \"open\"` is NEVER written without the wildcard that makes it open", () => {
  // OpenClaw's `open` is the policy MODE; an empty `allowFrom` still drops
  // every DM ("dmPolicy=open with empty allowedUserIds blocks all senders").
  // Getting this wrong silences a channel before Empyralis can see anything,
  // which is the one divergence direction that cannot be reported.
  const modes: Array<EmpyralisChannelPolicy["dmPolicy"]["mode"]> = [
    "open",
    "pairing",
    "owner_only",
    "allowlist",
  ];
  for (const mode of modes) {
    for (const channelId of [...OPENCLAW_TRANSPORT_CHANNEL_IDS]) {
      const rendered = renderOpenClawConfig(
        plan([policy({ channelId, dmPolicy: { mode, allowlist: mode === "allowlist" ? ["someone"] : [] } })]),
        { gatewayToken: "t" },
      );
      const block = channelBlock(rendered, channelId);
      if (block.dmPolicy !== "open") continue;
      assert.deepEqual(
        block.allowFrom,
        ["*"],
        `${channelId} with dm mode "${mode}" wrote dmPolicy:"open" without allowFrom:["*"] — every DM would be dropped`,
      );
    }
  }
});

test("the agent workspace is pinned inside the instance's own profile, not the shared ~/.openclaw", () => {
  // `--profile` isolates state + config but NOT the workspace: verified live,
  // an unpinned instance writes `~/.openclaw/workspace-<profile>`, inside the
  // operator's own tree and beside every other profile on that machine. That
  // would hollow out the one claim the whole adoption rests on.
  const rendered = renderOpenClawConfig(plan([policy({ channelId: "feishu" })]), { gatewayToken: "t" });
  const agents = rendered.config.agents as { defaults: Record<string, unknown> };
  assert.equal(agents.defaults.workspace, "/tmp/fake-home/.openclaw-acme/workspace");
});

test("a run that changed the config reports restartRequired, and a no-op run does not", async () => {
  // Overclaiming "the policy is live" when it has only been WRITTEN is the
  // same lie this module exists to prevent. OpenClaw's own restart RPC is
  // scoped operator.admin — the scope this codebase has a standing rule never
  // to request (it is the only one under which a client-asserted
  // senderIsOwner is honoured) — so the restart is reported, never taken.
  const clean = effectiveFor([policy({ channelId: "feishu" })]);
  const drifted = JSON.parse(JSON.stringify(clean)) as Record<string, any>;
  drifted.channels.feishu.requireMention = false;

  const changed = await fakeProvisioner({
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: drifted,
    effectiveAfterPatch: clean,
    audit: { findings: [] },
    patchCode: 0,
  }).provision();
  assert.equal(changed.status, "provisioned", changed.refusal?.detail);
  assert.equal(changed.configChanged, true);
  assert.equal(changed.restartRequired, true);

  // A refusal never claims a restart is pending — nothing was applied.
  // `installRuntime: false` so the off-pin version stays a refusal instead of
  // being corrected by an install: what is under test here is what a refusal
  // reports, not which refusal it is.
  const refused = await fakeProvisioner(
    {
      version: "2026.7.0",
      schema: schemaFixture(),
      effective: clean,
      audit: { findings: [] },
      patchCode: 0,
    },
    { installRuntime: false },
  ).provision();
  assert.equal(refused.status, "refused");
  assert.equal(refused.restartRequired, false);
});

// ── channel plugin installation (step 5) ──────────────────────────────────
//
// The gap these cover: twenty of OpenClaw's twenty-seven channels are separate
// npm packages, and until provisioning installed them a `channels.<id>` policy
// was written for code that was not there. The resulting outbound rejection
// meant "no plugin AND no credential" at once — indistinguishable, from
// outside, from the ordinary "not connected yet" state.

test("plugin install: the descriptor is READ from OpenClaw's catalog, never guessed from the channel id", () => {
  // The `@openclaw/<id>` convention holds for the official plugins and is
  // wrong for every external one. A guess would produce four broken installs.
  assert.equal(channelPluginInstallDescriptor("feishu")?.npm_package, "@openclaw/feishu");
  assert.equal(channelPluginInstallDescriptor("wecom")?.npm_package, "@wecom/wecom-openclaw-plugin");
  // …and the PLUGIN id is not the channel id there either, which is what the
  // install registry keys on.
  assert.equal(channelPluginInstallDescriptor("wecom")?.plugin_id, "wecom-openclaw-plugin");
  assert.equal(channelPluginInstallDescriptor("feishu")?.plugin_id, "feishu");
  // Bundled channels have no descriptor at all — `null` in the manifest is a
  // positive statement ("ships in the pinned build"), not "unknown".
  assert.equal(channelPluginInstallDescriptor("telegram"), undefined);
  assert.equal(channelPluginInstallDescriptor("irc"), undefined);
});

test("plugin install: every transported channel is either bundled or installable, never neither", () => {
  // A channel in neither bucket would be advertised by Empyralis and
  // impossible to bring up — the shape that made this whole gap invisible.
  const bundled = new Set(["clickclack", "imessage", "irc", "mattermost", "signal", "sms", "telegram"]);
  for (const channelId of OPENCLAW_TRANSPORT_CHANNEL_IDS) {
    const descriptor = channelPluginInstallDescriptor(channelId);
    assert.equal(
      Boolean(descriptor) !== bundled.has(channelId),
      true,
      `${channelId} is in neither the installable catalog nor the bundled set, or in both`,
    );
    if (descriptor) {
      assert.ok(descriptor.npm_spec.length > 0, `${channelId} has an empty npm spec`);
      assert.ok(descriptor.plugin_id.length > 0, `${channelId} has an empty plugin id`);
    }
  }
});

test("plugin install: a plugin needing a newer host than the pin REFUSES, and an unreadable range does too", () => {
  assert.equal(checkPluginHostCompatibility(">=2026.5.29", "2026.6.10").ok, true);
  assert.equal(checkPluginHostCompatibility(">=2026.6.10", "2026.6.10").ok, true);
  assert.equal(checkPluginHostCompatibility(null, "2026.6.10").ok, true);
  // Needs a host we are not pinned to. Installing anyway silently gets an
  // older plugin build than the catalog describes.
  assert.equal(checkPluginHostCompatibility(">=2026.7.1", "2026.6.10").ok, false);
  // A prerelease sorts below its release, so a 2026.5.12 host does NOT
  // satisfy `>=2026.5.12-beta.1`… it exceeds it.
  assert.equal(checkPluginHostCompatibility(">=2026.5.12-beta.1", "2026.5.12").ok, true);
  assert.equal(checkPluginHostCompatibility(">=2026.5.12-beta.1", "2026.5.11").ok, false);
  // A range shape we cannot evaluate is a refusal, never an assumed pass:
  // an unevaluated compatibility claim is one we cannot vouch for.
  assert.equal(checkPluginHostCompatibility("^2026.6.0", "2026.6.10").ok, false);
  assert.equal(checkPluginHostCompatibility("<2026.9.0", "2026.6.10").ok, false);
});

test("plugin install: a requested channel with no plugin is INSTALLED, then verified by read-back", async () => {
  const state: FakeCliState = {
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: effectiveFor([policy({ channelId: "feishu" })], ["feishu"]),
    audit: { findings: [] },
    patchCode: 0,
  };
  const result = await fakeProvisioner(state, {
    plan: plan([policy({ channelId: "feishu", installPlugin: true })]),
  }).provision();

  assert.equal(result.status, "provisioned", result.refusal?.detail);
  assert.deepEqual(state.installCalls, [["plugins", "install", "@openclaw/feishu", "--pin"]]);
  const feishu = result.channelPlugins.find((entry) => entry.channelId === "feishu");
  assert.equal(feishu?.installed, true);
  assert.equal(feishu?.action, "installed");
  assert.equal(feishu?.resolvedSpec, `@openclaw/feishu@${OPENCLAW_PINNED_VERSION}`);
});

test("plugin install: re-provisioning an installed box shells out to NO install at all", async () => {
  // Not a cosmetic optimisation. Their `plugins install` re-downloads the
  // package and then exits 1 with "plugin already exists", so a naive re-run
  // would make every reprovision fail — and fail only when the network is up,
  // which is the worst possible shape for a boot-time reconcile.
  const state: FakeCliState = {
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: effectiveFor([policy({ channelId: "feishu" })], ["feishu"]),
    audit: { findings: [] },
    patchCode: 0,
    channelsInstalled: { feishu: true },
    installRecords: { feishu: `@openclaw/feishu@${OPENCLAW_PINNED_VERSION}` },
  };
  const result = await fakeProvisioner(state, {
    plan: plan([policy({ channelId: "feishu", installPlugin: true })]),
  }).provision();

  assert.equal(result.status, "provisioned", result.refusal?.detail);
  assert.deepEqual(state.installCalls, [], "an already-installed plugin must never be re-fetched");
  const feishu = result.channelPlugins.find((entry) => entry.channelId === "feishu");
  assert.equal(feishu?.installed, true);
  // "adopted" on the first run that meets a pre-existing install: the pin is
  // captured from what is really there rather than asserted against nothing.
  assert.equal(feishu?.action, "adopted");
});

test("plugin install: a plugin that changed under a pinned box REFUSES rather than carrying traffic", async () => {
  const files = new Map<string, string>();
  const stateDir = "/tmp/fake-state";
  const recordPath = `${stateDir}/openclaw/acme.provisioning.json`;
  files.set(
    recordPath,
    JSON.stringify({
      fingerprint: "whatever",
      appliedAt: new Date().toISOString(),
      pinnedVersion: OPENCLAW_PINNED_VERSION,
      channels: [],
      pluginPins: { feishu: "@openclaw/feishu@2026.6.10" },
    }),
  );
  const state: FakeCliState = {
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: effectiveFor([policy({ channelId: "feishu" })]),
    audit: { findings: [] },
    patchCode: 0,
    channelsInstalled: { feishu: true },
    // Something ran `openclaw plugins update`, or a human installed by hand.
    installRecords: { feishu: "@openclaw/feishu@2026.7.1" },
  };
  const result = await fakeProvisioner(state, {
    plan: plan([policy({ channelId: "feishu", installPlugin: true })]),
    stateDir,
    fs: {
      readFile: async (filePath: string) => {
        const found = files.get(filePath);
        if (found === undefined) throw new Error("ENOENT");
        return found;
      },
      writeFile: async (filePath: string, contents: string) => {
        files.set(filePath, contents);
      },
      mkdir: async () => undefined,
      rm: async (filePath: string) => {
        files.delete(filePath);
      },
    },
  }).provision();

  assert.equal(result.status, "refused");
  assert.equal(result.refusal?.code, "openclaw_plugin_version_drift");
  assert.match(result.refusal?.detail ?? "", /2026\.6\.10/);
  assert.match(result.refusal?.detail ?? "", /2026\.7\.1/);
  assert.deepEqual(state.installCalls, []);
});

test("plugin install: a 404 or an unreachable registry REFUSES, carrying OpenClaw's own words", async () => {
  for (const failure of [
    { code: 1, stderr: "Package not found on npm: @openclaw/feishu." },
    { code: 1, stderr: "npm view failed: npm error code ECONNREFUSED" },
  ]) {
    const state: FakeCliState = {
      version: OPENCLAW_PINNED_VERSION,
      schema: schemaFixture(),
      effective: effectiveFor([policy({ channelId: "feishu" })]),
      audit: { findings: [] },
      patchCode: 0,
      installFailure: failure,
    };
    const result = await fakeProvisioner(state, {
      plan: plan([policy({ channelId: "feishu", installPlugin: true })]),
    }).provision();

    assert.equal(result.status, "refused");
    assert.equal(result.refusal?.code, "openclaw_plugin_install_failed");
    // Verbatim, so a 404 and a dead registry stay distinguishable without
    // this codebase pattern-matching either sentence.
    assert.ok(result.refusal?.detail.includes(failure.stderr), result.refusal?.detail);
    // Nothing was written: a failed install must not leave a half-provisioned
    // instance that reads as configured.
    assert.equal(result.configChanged, false);
    assert.equal(result.supervisor, undefined);
  }
});

test("plugin install: an install that claims success but produces nothing loadable REFUSES", async () => {
  const state: FakeCliState = {
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: effectiveFor([policy({ channelId: "feishu" })]),
    audit: { findings: [] },
    patchCode: 0,
  };
  // Their CLI prints "Installed plugin: feishu" and exits 0; the read-back is
  // the only thing that can contradict it.
  state.installSilentlyDoesNothing = true;
  const result = await fakeProvisioner(state, {
    plan: plan([policy({ channelId: "feishu", installPlugin: true })]),
  }).provision();
  assert.equal(result.status, "refused");
  assert.equal(result.refusal?.code, "openclaw_plugin_not_loaded");
  // The instance is not left half-provisioned by the optimistic claim.
  assert.equal(result.configChanged, false);
  assert.equal(result.supervisor, undefined);
});

test("plugin install: only what was asked for is fetched; everything else is REPORTED, not installed", async () => {
  // "Install only what is needed" and "no plugin is separately reportable"
  // are the same assertion from two sides: line is observed as absent, feishu
  // is brought up, and neither fact is inferred from a failed send.
  const state: FakeCliState = {
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: effectiveFor(
      [policy({ channelId: "feishu" }), policy({ channelId: "line" }), policy({ channelId: "irc" })],
      ["feishu"],
    ),
    audit: { findings: [] },
    patchCode: 0,
  };
  const result = await fakeProvisioner(state, {
    plan: plan([
      policy({ channelId: "feishu", installPlugin: true }),
      policy({ channelId: "line" }),
      policy({ channelId: "irc" }),
    ]),
  }).provision();

  assert.equal(result.status, "provisioned", result.refusal?.detail);
  assert.deepEqual(state.installCalls, [["plugins", "install", "@openclaw/feishu", "--pin"]]);

  const byChannel = new Map(result.channelPlugins.map((entry) => [entry.channelId, entry]));
  assert.equal(byChannel.get("feishu")?.installed, true);
  // NO PLUGIN — reported as a fact, not as a refusal and not as a send error.
  assert.equal(byChannel.get("line")?.installed, false);
  assert.equal(byChannel.get("line")?.requiresPlugin, true);
  assert.equal(byChannel.get("line")?.action, "not-installed");
  // Bundled: needs nothing fetched, and we do not claim it is installed
  // unless OpenClaw said so.
  assert.equal(byChannel.get("irc")?.requiresPlugin, false);
  assert.equal(byChannel.get("irc")?.action, "bundled");
});

test("plugin install: the security audit runs AFTER the plugin is loaded, and still gates", async () => {
  // A third-party plugin is new code inside the instance. "The audit was
  // clean before we added it" is not a claim worth making, so the audit must
  // run downstream of the install — and must still refuse when it is dirty.
  const state: FakeCliState = {
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: effectiveFor([policy({ channelId: "feishu" })], ["feishu"]),
    audit: {
      findings: [
        { checkId: "security.exposure.open_groups_with_elevated", severity: "critical", title: "elevated tools" },
      ],
    },
    patchCode: 0,
  };
  const result = await fakeProvisioner(state, {
    plan: plan([policy({ channelId: "feishu", installPlugin: true })]),
  }).provision();

  assert.equal(result.status, "refused");
  assert.equal(result.refusal?.code, "openclaw_security_audit_not_clean");
  // The install DID happen first — that is the ordering being asserted.
  assert.deepEqual(state.installCalls, [["plugins", "install", "@openclaw/feishu", "--pin"]]);
});

test("plugin install: install_plugin is opt-IN across the cloud boundary", async () => {
  // Fetching third-party code onto a customer's machine is the expensive,
  // irreversible direction; not fetching it is a reported state. So an absent
  // or non-boolean flag must mean "do not install", never "install".
  const parsed = parseChannelPolicies([
    { channel_id: "feishu", dm_policy: { mode: "open" }, group_policy: { mode: "open" }, install_plugin: true },
    { channel_id: "line", dm_policy: { mode: "open" }, group_policy: { mode: "open" } },
    { channel_id: "zalo", dm_policy: { mode: "open" }, group_policy: { mode: "open" }, install_plugin: "yes" },
  ]);
  assert.deepEqual(parsed.errors, []);
  const byChannel = new Map(parsed.channels.map((channel) => [channel.channelId, channel]));
  assert.equal(byChannel.get("feishu")?.installPlugin, true);
  assert.equal(byChannel.get("line")?.installPlugin, false);
  assert.equal(byChannel.get("zalo")?.installPlugin, false, "a truthy non-boolean must not install");
});

test("plugin install: the instance's plugin inventory is an explicit allowlist", async () => {
  // OpenClaw said this itself on the first live run that installed a plugin:
  //   "plugins.allow is empty; discovered non-bundled plugins may auto-load:
  //    feishu (…). Set plugins.allow to explicit trusted ids."
  // Provisioning now writes into that plugin directory, so "whatever is on
  // disk" is no longer a safe inventory.
  const state: FakeCliState = {
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: effectiveFor([policy({ channelId: "feishu" }), policy({ channelId: "irc" })], ["feishu"]),
    audit: { findings: [] },
    patchCode: 0,
  };
  const result = await fakeProvisioner(state, {
    plan: plan([policy({ channelId: "feishu", installPlugin: true }), policy({ channelId: "irc" })]),
  }).provision();
  assert.equal(result.status, "provisioned", result.refusal?.detail);

  const rendered = renderOpenClawConfig(
    {
      ...plan([policy({ channelId: "feishu" }), policy({ channelId: "irc" })]),
      installedChannelPluginIds: ["feishu"],
    },
    { gatewayToken: "t" },
  );
  const plugins = rendered.config.plugins as Record<string, unknown>;
  // Exactly the bridge plus what this box installed. `irc` is bundled and
  // needs no entry — their allowlist still lets an explicitly-enabled bundled
  // chat channel activate, which is why this is a safe control and not a
  // blunt one.
  assert.deepEqual(plugins.allow, ["empyralis-bridge", "feishu"]);

  // An empty allowlist is a lockdown violation, so an instance can never end
  // up loading whatever lands in its plugin directory.
  const empty = JSON.parse(JSON.stringify(rendered.config)) as Record<string, any>;
  empty.plugins.allow = [];
  const violations = findOpenClawLockdownViolations(empty);
  assert.equal(violations.some((violation) => violation.code === "plugin_allowlist_empty"), true);
  // …and the generated config satisfies its own rule.
  assert.equal(
    findOpenClawLockdownViolations(rendered.config).some((v) => v.code === "plugin_allowlist_empty"),
    false,
  );
});

test("plugin install: a channel plugin's OWN tool surface is switched off, discovered not listed", () => {
  // Only reachable once a channel plugin is installed: `@openclaw/feishu`
  // contributes `channels.feishu.tools` (doc/chat/wiki/drive/perm/scopes/
  // bitable/base), which the GLOBAL tools.* lockdown does not reach. Their own
  // audit found it the moment a credential was configured:
  //   channels.feishu.doc_owner_open_id [warn] "feishu_doc action \"create\"
  //   can grant document access to the trusted requesting Feishu user."
  // A radio must not be able to create documents in the owner's tenant.
  const schema = {
    properties: {
      channels: {
        properties: {
          feishu: {
            properties: {
              tools: {
                properties: {
                  doc: { type: "boolean" },
                  drive: { type: "boolean" },
                  perm: { type: "boolean" },
                },
              },
            },
          },
          // No tools node at all — the ordinary case, and it must contribute
          // nothing rather than an empty object.
          line: { properties: {} },
        },
      },
    },
  };

  const resolved = resolveOpenClawChannelToolFlags(schema, ["feishu", "line"]);
  assert.deepEqual(resolved.findings, []);
  assert.deepEqual(
    resolved.disable.map((entry) => `${entry.channelId}.${entry.flag}`).sort(),
    ["feishu.doc", "feishu.drive", "feishu.perm"],
  );

  const rendered = renderOpenClawConfig(
    { ...plan([policy({ channelId: "feishu" }), policy({ channelId: "line" })]), channelToolFlags: resolved.disable },
    { gatewayToken: "t" },
  );
  assert.deepEqual(channelBlock(rendered, "feishu").tools, { doc: false, drive: false, perm: false });
  assert.equal(channelBlock(rendered, "line").tools, undefined);

  // A flag shape we cannot switch off is a FINDING, and a shape finding
  // refuses the whole run — never a silent "leave it on".
  const odd = resolveOpenClawChannelToolFlags(
    {
      properties: {
        channels: { properties: { feishu: { properties: { tools: { properties: { doc: { type: "object" } } } } } } },
      },
    },
    ["feishu"],
  );
  assert.deepEqual(odd.disable, []);
  assert.equal(odd.findings.length, 1);
  assert.equal(odd.findings[0].code, "unhandled_channel_tool");
});

test("plugin install: a channel tool surface we cannot switch off REFUSES the whole run", async () => {
  const schema = schemaFixture() as Record<string, any>;
  schema.properties.channels.properties.feishu.properties.tools = {
    properties: { doc: { type: "object" } },
  };
  const result = await fakeProvisioner(
    {
      version: OPENCLAW_PINNED_VERSION,
      schema,
      effective: effectiveFor([policy({ channelId: "feishu" })], ["feishu"]),
      audit: { findings: [] },
      patchCode: 0,
    },
    { plan: plan([policy({ channelId: "feishu", installPlugin: true })]) },
  ).provision();
  assert.equal(result.status, "refused");
  assert.equal(result.refusal?.code, "openclaw_channel_shape_drift");
  assert.match(result.refusal?.detail ?? "", /unhandled_channel_tool/);
});

test("the profile state dir is locked to 0700 on EVERY run, not just at creation", async () => {
  // Measured on a real box: the directory came out 755 (a `mkdir` inheriting a
  // systemd service's 022 umask, or OpenClaw creating it first), OpenClaw's own
  // audit reports `fs.state_dir.perms_readable` at WARN, and warn is blocking.
  // So the first run provisioned and every run after it would have refused
  // with `openclaw_security_audit_not_clean` — provision once, refuse forever.
  // The directory holds the gateway token and the conversation state.
  const chmods: Array<[string, number]> = [];
  const files = new Map<string, string>();
  const state: FakeCliState = {
    version: OPENCLAW_PINNED_VERSION,
    schema: schemaFixture(),
    effective: effectiveFor([policy({ channelId: "feishu" })]),
    audit: { findings: [] },
    patchCode: 0,
  };
  const result = await fakeProvisioner(state, {
    fs: {
      readFile: async (filePath: string) => {
        const found = files.get(filePath);
        if (found === undefined) throw new Error("ENOENT");
        return found;
      },
      writeFile: async (filePath: string, contents: string) => {
        files.set(filePath, contents);
      },
      mkdir: async () => undefined,
      rm: async (filePath: string) => {
        files.delete(filePath);
      },
      chmod: async (dirPath: string, mode: number) => {
        chmods.push([dirPath, mode]);
      },
    },
  }).provision();

  assert.equal(result.status, "provisioned", result.refusal?.detail);
  const locked = chmods.find(([dir]) => dir.endsWith(".openclaw-acme"));
  assert.ok(locked, `expected the profile state dir to be chmod'ed, got ${JSON.stringify(chmods)}`);
  assert.equal(locked![1], 0o700);
});
