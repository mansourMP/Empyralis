/**
 * Installing the CHANNEL PLUGINS a provisioned OpenClaw instance needs
 * (CHANNEL-ADOPTION-PLAN.md step 5).
 *
 * THE GAP THIS CLOSES
 * -------------------
 * Twenty of OpenClaw's twenty-seven channels do not ship in the pinned bundle.
 * `dist/extensions/` carries seven — imessage, irc, mattermost, signal, sms,
 * telegram, clickclack — and every other one, Feishu included, is a separate
 * npm package. Provisioning wrote `channels.<id>.*` policy for channels whose
 * implementation was simply absent, and OpenClaw answered an outbound send
 * with a clean rejection that meant TWO different things at once:
 *
 *     BEFORE                          AFTER
 *       channels.feishu written         channels.feishu written
 *       plugin        ABSENT            plugin        INSTALLED + verified
 *       credential    ABSENT            credential    absent
 *              │                                │
 *              ▼                                ▼
 *       "Channel is unavailable:         "Feishu account \"default\"
 *        feishu. Install the               not configured"
 *        official external plugin…"
 *              │                                │
 *       means BOTH are missing.          means exactly ONE thing:
 *       Indistinguishable from           the owner has not attached
 *       "no credential" to any            an account yet.
 *       caller that only sees a
 *       failed send.
 *
 * After this module runs and provisioning reports `provisioned`, "no plugin"
 * is impossible by construction — so any remaining send failure IS "no
 * credential". The two states are also reported SEPARATELY and structurally
 * (`OpenClawChannelPluginState.installed`), never by matching their prose:
 * both sentences above are plain `new Error(...)` inside their
 * `channel-selection` module with no error code attached, and CLAUDE.md's
 * "stale string matching" rule forbids building a product distinction on a
 * sentence that upstream can reword in a patch release.
 *
 * WHY `openclaw plugins install` AND NOT A CONFIG-DECLARED PLUGIN LIST
 * --------------------------------------------------------------------
 * `plugins.load.paths` (which the bridge plugin uses) loads code that is
 * already on disk; it cannot fetch a package. `plugins.entries.<id>.enabled`
 * only toggles an already-discovered plugin. Neither installs anything, and a
 * `channels.<id>` block for an uninstalled plugin is accepted by `config
 * patch` without complaint — which is exactly how this stayed invisible.
 * `openclaw plugins install <npm-spec>` is the only mechanism that acquires
 * the code, and it maintains their own install registry
 * (`plugins registry --json` -> `installRecords`), which is what makes the
 * result verifiable afterwards rather than assumed.
 *
 * IDEMPOTENCY IS OURS TO ENFORCE, BECAUSE THEIRS IS NOT
 * -----------------------------------------------------
 * Measured against the real CLI: re-running an install of an already-installed
 * plugin DOWNLOADS THE PACKAGE AGAIN and then exits **1** with "plugin already
 * exists … (delete it first)". Provisioning re-runs on every boot and on every
 * policy change, so a naive install call would turn every reprovision into a
 * failure — and, worse, into a NETWORK-DEPENDENT failure on a box that is
 * fully installed and working offline.
 *
 * So the install is gated on read state, never on a try/catch:
 *
 *     read `plugins registry --json`  ──▶ installRecords[pluginId]?
 *          │                                        │
 *          │ absent                                 │ present
 *          ▼                                        ▼
 *     `plugins install <spec> --pin`        recorded pin matches?
 *          │                                   yes ──▶ SKIP  (no network)
 *          │                                   no  ──▶ REFUSE (version drift)
 *          ▼
 *     read back + record the resolved spec
 *
 * The skip branch is what makes reprovisioning offline-safe: a box with no
 * network and every plugin already installed does not shell out at all.
 *
 * ON VERSION PINNING, AND WHY THE PIN IS OBSERVED RATHER THAN AUTHORED
 * --------------------------------------------------------------------
 * The channel packages version INDEPENDENTLY of the CLI. `@openclaw/feishu` is
 * at 2026.7.1 today against a 2026.6.10 host pin. Their installer already
 * resolves that, out loud:
 *
 *     Resolved @openclaw/feishu to @openclaw/feishu@2026.7.1, but that version
 *     is incompatible with this OpenClaw runtime; using newest compatible
 *     @openclaw/feishu@2026.6.10.
 *
 * Two consequences, and they pull in opposite directions. Their resolver is
 * the ONLY thing that knows which plugin builds a given host can load, so
 * authoring our own version literal would be transcribing a compatibility
 * decision we cannot verify — the "derive it, never transcribe it" rule that
 * produced the generated manifest in the first place. But "newest compatible"
 * is a moving target: the same command run a month apart can install different
 * code, silently, on two boxes that both report `provisioned`. That is the
 * MAN-306 shape precisely — an out-of-process artifact drifting under a
 * correct-looking deploy.
 *
 * The resolution is to let THEM choose the version once and then hold them to
 * it forever:
 *
 *   1. The manifest carries their npm SPEC verbatim, never a version we
 *      invented. Where they pinned one (all four external packages), it is
 *      passed through unchanged.
 *   2. `--pin` makes the install record an exact `<name>@<version>`.
 *   3. The resolved spec is copied into EMPYRALIS's provisioning record on
 *      first install, and every later run asserts the live install record
 *      still equals it. A `plugins update`, a hand-install, or a re-resolution
 *      to a newer "newest compatible" build all fail the instance closed with
 *      `openclaw_plugin_version_drift` and name both versions.
 *   4. `min_host_version` from their catalog is checked against our OWN pinned
 *      CLI version BEFORE the install runs, so "this plugin needs a newer
 *      OpenClaw than Empyralis is pinned to" is a refusal with a readable
 *      reason rather than a silent downgrade to an older plugin build.
 *
 * A mismatch is therefore loud in all three directions: newer plugin than the
 * host allows (4), a plugin that changed under us (3), and an install that
 * claimed success but produced nothing loadable (`openclaw_plugin_not_loaded`).
 *
 * OFFLINE AND FAILURE
 * -------------------
 * Every non-zero install exit becomes a refusal carrying the CLI's own stderr
 * verbatim, so a 404 ("Package not found on npm: …") and a dead registry
 * ("npm view failed: npm error code ECONNREFUSED") arrive as different,
 * readable reasons without this file pattern-matching either. A refusal stops
 * provisioning before the config is written, so the instance is never left
 * half-provisioned: it is either fully installed and locked down, or it
 * reports why it is not.
 */

import { GENERATED_OPENCLAW_MANIFEST } from "../generated-openclaw-channels";
import type { OpenClawCli } from "./openclaw-cli";
import { OPENCLAW_PINNED_VERSION } from "./openclaw-version";

/** npm reaches out to a registry and unpacks a tarball; the measured cold
 *  install of `@openclaw/feishu` took ~35s. Generous, but still bounded —
 *  a wedged registry connection must not hang a capability invocation. */
const INSTALL_TIMEOUT_MS = 300_000;
const READ_TIMEOUT_MS = 60_000;

export type OpenClawPluginInstallAction =
  /** Ships inside the pinned bundle; nothing to install, nothing to pin. */
  | "bundled"
  /** Already installed at the recorded pin. No network touched. */
  | "already-installed"
  /** Installed by this run. */
  | "installed"
  /** Adopted a pre-existing install as this box's pin (first run after this
   *  module shipped, or a plugin a human installed before we recorded one). */
  | "adopted"
  /** Needs a plugin, has none, and nobody asked this run to fetch one.
   *  Reported rather than refused — this is the honest "no plugin" state. */
  | "not-installed";

export interface OpenClawChannelPluginState {
  channelId: string;
  /** Undefined for a bundled channel — there is no separate plugin. */
  pluginId?: string;
  /** Whether this channel needs a separately-installed plugin at all. */
  requiresPlugin: boolean;
  /**
   * Whether OpenClaw itself reports the channel's implementation as present.
   * THE load-bearing field: `false` means "no plugin", and after a successful
   * provisioning run it can only be `true`, which is what makes a subsequent
   * send failure mean "no credential" and nothing else.
   */
  installed: boolean;
  /** The exact `<name>@<version>` OpenClaw resolved, from its install
   *  registry. Undefined for bundled channels. */
  resolvedSpec?: string;
  action: OpenClawPluginInstallAction;
}

export type OpenClawPluginRefusalCode =
  | "openclaw_plugin_host_incompatible"
  | "openclaw_plugin_install_failed"
  | "openclaw_plugin_not_loaded"
  | "openclaw_plugin_version_drift"
  | "openclaw_plugin_state_unreadable";

export interface OpenClawPluginInstallOutcome {
  states: OpenClawChannelPluginState[];
  /** pluginId -> resolved `<name>@<version>`, to be persisted as this box's
   *  pin and asserted on every later run. */
  resolvedPins: Record<string, string>;
  refusal?: { code: OpenClawPluginRefusalCode; detail: string };
}

export interface GeneratedPluginInstallDescriptor {
  required: boolean;
  plugin_id: string;
  npm_package: string;
  npm_spec: string;
  catalog_pinned_version: string | null;
  source: string;
  min_host_version: string | null;
  expected_integrity: string | null;
}

/** The manifest's install descriptor for a channel, or undefined when the
 *  channel is bundled (or is not a channel the pinned OpenClaw carries). */
export function channelPluginInstallDescriptor(
  channelId: string,
): GeneratedPluginInstallDescriptor | undefined {
  const entry = GENERATED_OPENCLAW_MANIFEST.channels.find((channel) => channel.id === channelId);
  const descriptor = entry?.plugin_install as GeneratedPluginInstallDescriptor | null | undefined;
  return descriptor && descriptor.required ? descriptor : undefined;
}

// ── Host compatibility ────────────────────────────────────────────────────
//
// Their `minHostVersion` values are all `>=X.Y.Z` today, including one
// prerelease (`>=2026.5.12-beta.1`). Only that one shape is understood, and
// ANY other shape is a refusal rather than a pass: a range we cannot evaluate
// is a compatibility claim we cannot vouch for, and defaulting it to "fine" is
// how a plugin that needs a newer host gets installed anyway and then fails at
// runtime with something unrelated-looking.

/** Numeric-segment comparison with a prerelease suffix, per semver's rule that
 *  a prerelease sorts BEFORE its release. Enough for their `YYYY.M.P[-tag]`
 *  scheme and deliberately no more. */
export function compareOpenClawVersions(left: string, right: string): number {
  const split = (value: string): { nums: number[]; pre: string } => {
    const [core, ...rest] = String(value).trim().split("-");
    return {
      nums: core.split(".").map((part) => Number.parseInt(part, 10) || 0),
      pre: rest.join("-"),
    };
  };
  const a = split(left);
  const b = split(right);
  for (let index = 0; index < Math.max(a.nums.length, b.nums.length); index += 1) {
    const diff = (a.nums[index] ?? 0) - (b.nums[index] ?? 0);
    if (diff !== 0) return diff < 0 ? -1 : 1;
  }
  if (a.pre === b.pre) return 0;
  // A prerelease is LOWER than the same core version with no prerelease.
  if (!a.pre) return 1;
  if (!b.pre) return -1;
  return a.pre < b.pre ? -1 : 1;
}

export interface HostCompatibilityVerdict {
  ok: boolean;
  detail?: string;
}

export function checkPluginHostCompatibility(
  minHostVersion: string | null,
  hostVersion: string = OPENCLAW_PINNED_VERSION,
): HostCompatibilityVerdict {
  const raw = String(minHostVersion ?? "").trim();
  if (!raw) return { ok: true };
  const match = raw.match(/^>=\s*(\d[\w.-]*)$/);
  if (!match) {
    return {
      ok: false,
      detail:
        `OpenClaw declares a plugin host-version range Empyralis cannot evaluate: "${raw}". ` +
        "Only `>=<version>` is understood. Refusing rather than assuming compatibility — an " +
        "unevaluated range is a claim we cannot vouch for.",
    };
  }
  if (compareOpenClawVersions(hostVersion, match[1]) < 0) {
    return {
      ok: false,
      detail:
        `The plugin requires OpenClaw ${raw}, but Empyralis pins OpenClaw ${hostVersion}. ` +
        "Installing anyway would silently get an older plugin build than the catalog describes.",
    };
  }
  return { ok: true };
}

// ── Reading what is actually installed ────────────────────────────────────

interface LiveInstallState {
  /** channelId -> OpenClaw's own `installed` flag from `channels list`. */
  channelInstalled: Map<string, boolean>;
  /** pluginId -> resolved `<name>@<version>` from their install registry. */
  installRecords: Map<string, string>;
}

function parseChannelsList(raw: unknown): Map<string, boolean> {
  const out = new Map<string, boolean>();
  const chat = raw && typeof raw === "object" ? (raw as Record<string, unknown>).chat : undefined;
  if (!chat || typeof chat !== "object") return out;
  for (const [channelId, value] of Object.entries(chat as Record<string, unknown>)) {
    const installed = value && typeof value === "object" ? (value as Record<string, unknown>).installed : undefined;
    out.set(String(channelId).toLowerCase(), installed === true);
  }
  return out;
}

function parseInstallRecords(raw: unknown): Map<string, string> {
  const out = new Map<string, string>();
  const persisted = raw && typeof raw === "object" ? (raw as Record<string, unknown>).persisted : undefined;
  const records =
    persisted && typeof persisted === "object"
      ? (persisted as Record<string, unknown>).installRecords
      : undefined;
  if (!records || typeof records !== "object") return out;
  for (const [pluginId, value] of Object.entries(records as Record<string, unknown>)) {
    if (!value || typeof value !== "object") continue;
    const record = value as Record<string, unknown>;
    // `resolvedSpec` is the exact `<name>@<version>` their installer settled
    // on; `spec` is what was asked for and can be a floating package name.
    const resolved =
      typeof record.resolvedSpec === "string" && record.resolvedSpec.trim()
        ? record.resolvedSpec.trim()
        : typeof record.resolvedName === "string" && typeof record.resolvedVersion === "string"
          ? `${record.resolvedName}@${record.resolvedVersion}`
          : undefined;
    if (resolved) out.set(String(pluginId).toLowerCase(), resolved);
  }
  return out;
}

async function readLiveInstallState(cli: OpenClawCli): Promise<LiveInstallState | undefined> {
  const channels = await cli.run(["channels", "list", "--all", "--json"], { timeoutMs: READ_TIMEOUT_MS });
  const registry = await cli.run(["plugins", "registry", "--json"], { timeoutMs: READ_TIMEOUT_MS });
  if (channels.code !== 0 || registry.code !== 0) return undefined;
  try {
    return {
      channelInstalled: parseChannelsList(JSON.parse(channels.stdout)),
      installRecords: parseInstallRecords(JSON.parse(registry.stdout)),
    };
  } catch {
    return undefined;
  }
}

// ── The install pass ──────────────────────────────────────────────────────

export interface EnsureChannelPluginsOptions {
  cli: OpenClawCli;
  /**
   * REPORTING scope: every channel whose plugin state the caller wants
   * answered. Wider than the install scope on purpose — "feishu has no
   * plugin here" is a fact worth reporting about a channel nobody asked to
   * install, and it is the fact that tells a caller why a send failed.
   */
  channelIds: readonly string[];
  /**
   * INSTALL scope: the subset to actually acquire. Never all twenty, on every
   * box — each is a third-party package that will run inside the instance
   * carrying the customer's messages, and fetching one is a decision, not a
   * default. Anything outside this set is observed and reported, never
   * installed.
   */
  installChannelIds: readonly string[];
  /** pluginId -> resolved spec, as recorded by a previous successful run.
   *  Empty on a box that has never installed a channel plugin. */
  recordedPins?: Record<string, string>;
  hostVersion?: string;
  record?: (messageType: string, payload: Record<string, unknown>) => Promise<unknown>;
}

/**
 * Bring every requested channel's plugin into place, or refuse and say why.
 *
 * Refuses on the FIRST problem rather than collecting them: the caller's next
 * step is writing config and starting a supervised process, and doing that
 * against a partially-installed instance is precisely the half-provisioned
 * state this is meant to prevent.
 */
export async function ensureChannelPluginsInstalled(
  options: EnsureChannelPluginsOptions,
): Promise<OpenClawPluginInstallOutcome> {
  const hostVersion = options.hostVersion ?? OPENCLAW_PINNED_VERSION;
  const recordedPins = options.recordedPins ?? {};
  const states: OpenClawChannelPluginState[] = [];
  const resolvedPins: Record<string, string> = { ...recordedPins };
  const normalize = (ids: readonly string[]): string[] =>
    [...new Set(ids.map((id) => String(id).trim().toLowerCase()).filter(Boolean))].sort();
  const toInstall = new Set(normalize(options.installChannelIds));
  // Anything in the install scope is always reported on, even if a caller
  // forgot to also list it for reporting — a channel we fetched code for and
  // then said nothing about is the worst of both.
  const requested = normalize([...options.channelIds, ...toInstall]);

  const refuse = (
    code: OpenClawPluginRefusalCode,
    detail: string,
  ): OpenClawPluginInstallOutcome => ({ states, resolvedPins, refusal: { code, detail } });

  if (requested.length === 0) return { states, resolvedPins };

  let live = await readLiveInstallState(options.cli);
  if (!live) {
    return refuse(
      "openclaw_plugin_state_unreadable",
      "`openclaw channels list --all --json` / `openclaw plugins registry --json` did not return readable " +
        "JSON, so which channel plugins are installed cannot be established. Refusing rather than " +
        "installing over an unknown state.",
    );
  }

  let installedAnything = false;

  for (const channelId of requested) {
    const descriptor = channelPluginInstallDescriptor(channelId);
    if (!descriptor) {
      // Bundled (or not a transported channel at all). `installed` still comes
      // from OpenClaw rather than being assumed true — a bundled plugin can be
      // disabled, and reporting a confident `true` we did not observe is the
      // kind of claim this module exists to stop making.
      states.push({
        channelId,
        requiresPlugin: false,
        installed: live.channelInstalled.get(channelId) ?? false,
        action: "bundled",
      });
      continue;
    }

    const pluginId = descriptor.plugin_id;
    const liveSpec = live.installRecords.get(pluginId);
    const recordedSpec = recordedPins[pluginId];

    if (!toInstall.has(channelId) && !liveSpec) {
      // Observe-only: this channel needs a plugin and does not have one. NOT a
      // refusal — nobody asked for it. Reported as `installed: false`, which
      // is the whole point of reporting: it is the difference between "this
      // channel has no code here" and "this channel has no credential", and
      // it is answerable without anyone attempting a send.
      states.push({
        channelId,
        pluginId,
        requiresPlugin: true,
        installed: live.channelInstalled.get(channelId) ?? false,
        action: "not-installed",
      });
      continue;
    }

    const compatibility = checkPluginHostCompatibility(descriptor.min_host_version, hostVersion);
    if (!compatibility.ok) {
      return refuse(
        "openclaw_plugin_host_incompatible",
        `${channelId} (${descriptor.npm_package}): ${compatibility.detail}`,
      );
    }

    if (liveSpec) {
      if (recordedSpec && recordedSpec !== liveSpec) {
        return refuse(
          "openclaw_plugin_version_drift",
          `${channelId}: this box is pinned to ${recordedSpec} but OpenClaw's install registry now reports ` +
            `${liveSpec}. A channel plugin changed underneath a provisioned instance without Empyralis ` +
            "asking for it (a `openclaw plugins update`, or a hand-install). Refusing rather than carrying " +
            "a customer's messages through code this box was never provisioned with.",
        );
      }
      resolvedPins[pluginId] = liveSpec;
      states.push({
        channelId,
        pluginId,
        requiresPlugin: true,
        installed: live.channelInstalled.get(channelId) ?? false,
        resolvedSpec: liveSpec,
        action: recordedSpec ? "already-installed" : "adopted",
      });
      continue;
    }

    // Not installed. This is the only branch that touches the network.
    const install = await options.cli.run(
      ["plugins", "install", descriptor.npm_spec, "--pin"],
      { timeoutMs: INSTALL_TIMEOUT_MS },
    );
    if (install.code !== 0) {
      return refuse(
        "openclaw_plugin_install_failed",
        `${channelId}: \`openclaw plugins install ${descriptor.npm_spec}\` exited ${install.code}. ` +
          `OpenClaw said: ${install.stderr.trim() || install.stdout.trim() || "(no output)"}`,
      );
    }
    installedAnything = true;
    // Recorded as NOT YET installed. The read-back pass below is what may
    // promote it, and it is the only thing entitled to: their CLI prints
    // "Installed plugin: feishu" and exits 0, which is a claim, not evidence.
    states.push({
      channelId,
      pluginId,
      requiresPlugin: true,
      installed: false,
      action: "installed",
    });
    await options.record?.("openclaw.provision.plugin_installed", {
      channel_id: channelId,
      plugin_id: pluginId,
      npm_spec: descriptor.npm_spec,
      source: descriptor.source,
      // Their installer prints its own compatibility resolution here (e.g.
      // "…2026.7.1 is incompatible with this runtime; using newest compatible
      // …2026.6.10"). Kept verbatim in the journal so a downgrade is visible
      // to a human without anyone parsing the sentence.
      cli_output: install.stdout.trim().slice(0, 2000),
    });
  }

  if (!installedAnything) {
    return { states, resolvedPins };
  }

  // ── Read back, because an install that says "Installed plugin: feishu" is
  //    still only a claim. The two facts asserted are OpenClaw's own: the
  //    channel now reports `installed: true`, and the install registry
  //    carries a resolved spec.
  live = await readLiveInstallState(options.cli);
  if (!live) {
    return refuse(
      "openclaw_plugin_state_unreadable",
      "Channel plugins were installed, but OpenClaw's channel/plugin state could not be read back " +
        "afterwards, so the install cannot be verified.",
    );
  }

  for (const state of states) {
    if (!state.requiresPlugin || state.action === "not-installed") {
      // Bundled, or deliberately not asked for. Its `installed` flag is
      // refreshed from the re-read but it is never held to the post-install
      // assertions below — nobody claimed it would become installed.
      state.installed = live.channelInstalled.get(state.channelId) ?? false;
      continue;
    }
    const pluginId = state.pluginId as string;
    const liveSpec = live.installRecords.get(pluginId);
    const installed = live.channelInstalled.get(state.channelId) ?? false;
    if (!installed || !liveSpec) {
      return refuse(
        "openclaw_plugin_not_loaded",
        `${state.channelId}: the plugin install reported success, but OpenClaw still reports the channel as ` +
          `${installed ? "installed without an install record" : "not installed"}. The channel would keep ` +
          "answering an outbound send with \"Channel is unavailable\", which is indistinguishable from a " +
          "missing credential.",
      );
    }
    if (state.action !== "already-installed" && state.action !== "adopted") {
      state.action = "installed";
    }
    state.installed = true;
    state.resolvedSpec = liveSpec;
    resolvedPins[pluginId] = liveSpec;
  }

  return { states, resolvedPins };
}
