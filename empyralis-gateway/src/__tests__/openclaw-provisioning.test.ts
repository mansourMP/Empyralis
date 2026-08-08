import assert from "node:assert/strict";
import fs from "node:fs";
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

  // None of the five transported channels declares pluginHooks today.
  assert.deepEqual(resolveOpenClawPluginHookFlags(schema, [...OPENCLAW_TRANSPORT_CHANNEL_IDS]).enable, []);
  // WhatsApp does, and would be handled the moment it is transported.
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
  assert.equal(
    parseChannelPolicies([{ channel_id: "telegram", dm_policy: { mode: "open" }, group_policy: { mode: "open" } }])
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
}

function fakeProvisioner(state: FakeCliState, overrides: Record<string, unknown> = {}) {
  const files = new Map<string, string>();
  const cli = new OpenClawCli({
    profile: "acme",
    env: { PATH: "/usr/bin" },
    exec: async (args) => {
      const rest = args.slice(2);
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
    ...overrides,
  });
}

function schemaFixture(): unknown {
  return JSON.parse(fs.readFileSync(fixturePath(), "utf8"));
}

function effectiveFor(channels: EmpyralisChannelPolicy[]): Record<string, unknown> {
  const rendered = renderOpenClawConfig(plan(channels), { gatewayToken: "a-suitably-long-gateway-token" });
  const effective = JSON.parse(JSON.stringify(rendered.config)) as Record<string, any>;
  effective.gateway.auth.token = "__OPENCLAW_REDACTED__";
  return effective;
}

test("a version mismatch refuses BEFORE anything is written", async () => {
  const provisioner = fakeProvisioner({
    version: "2026.7.0",
    schema: schemaFixture(),
    effective: {},
    audit: { findings: [] },
    patchCode: 0,
  });
  const result = await provisioner.provision();
  assert.equal(result.status, "refused");
  assert.equal(result.refusal?.code, "openclaw_version_mismatch");
  assert.equal(result.configFingerprint, undefined, "nothing may be generated before the pin is satisfied");
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
  const refused = await fakeProvisioner({
    version: "2026.7.0",
    schema: schemaFixture(),
    effective: clean,
    audit: { findings: [] },
    patchCode: 0,
  }).provision();
  assert.equal(refused.status, "refused");
  assert.equal(refused.restartRequired, false);
});
