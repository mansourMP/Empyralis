/**
 * The seams that make a box come up channel-ready with nobody typing
 * anything: the loopback secrets, the supervised account, and the plan the
 * root installer is handed.
 *
 * Each of these guards a failure that is SILENT by construction — a missing
 * secret that simply never constructs a runtime, a systemd unit that runs the
 * transport as the wrong user against a different home directory, a shell
 * script quietly carrying its own copy of a version pin. None of them throws,
 * logs, or fails a build.
 */

import assert from "assert";
import os from "os";
import path from "path";
import test from "node:test";

import {
  openClawLocalSecretsPath,
  resolveOpenClawLocalSecrets,
} from "../openclaw/openclaw-local-secrets";
import { buildOpenClawInstallPlan } from "../openclaw/provisioning/openclaw-install-plan-cli";
import {
  buildOpenClawSupervisedEnv,
  resolveExpectedOpenClawSupervisorUnit,
} from "../openclaw/provisioning/openclaw-supervisor-unit";
import { renderSystemdUnit } from "../update/gateway-supervisor-install";
import { OPENCLAW_PINNED_VERSION } from "../openclaw/provisioning/openclaw-version";

/** In-memory fs, so no test writes a developer's real state directory. */
function memoryFs() {
  const files = new Map<string, string>();
  return {
    files,
    io: {
      readFile: async (filePath: string) => {
        const found = files.get(filePath);
        if (found === undefined) throw new Error("ENOENT");
        return found;
      },
      writeFile: async (filePath: string, contents: string) => {
        files.set(filePath, contents);
      },
      mkdir: async () => undefined,
    },
  };
}

// ── the two loopback secrets ──────────────────────────────────────────────

test("a box with nothing configured mints BOTH secrets and persists them", async () => {
  // The bug: index.ts constructed the inbound listener, the outbound client,
  // the channel runtimes and `openclaw.provision` only when both of these
  // were set, and no installer in this repo ever set either. The whole
  // transport was un-constructed on every box, silently.
  const { files, io } = memoryFs();
  const secrets = await resolveOpenClawLocalSecrets({ stateDir: "/state", fs: io });

  assert.ok(secrets.bridgeToken.length >= 32);
  assert.ok(secrets.gatewayToken.length >= 32);
  assert.notEqual(secrets.bridgeToken, secrets.gatewayToken, "two different secrets, never one reused");
  assert.deepEqual(secrets.generated.sort(), ["bridge_token", "gateway_token"]);
  assert.ok(files.has(openClawLocalSecretsPath("/state")));
});

test("the second boot reads the SAME secrets back", async () => {
  // A gateway that minted fresh tokens every boot would invalidate OpenClaw's
  // stored gateway.auth.token on every restart, and present as a transport
  // that works until the box reboots.
  const { io } = memoryFs();
  const first = await resolveOpenClawLocalSecrets({ stateDir: "/state", fs: io });
  const second = await resolveOpenClawLocalSecrets({ stateDir: "/state", fs: io });

  assert.equal(second.bridgeToken, first.bridgeToken);
  assert.equal(second.gatewayToken, first.gatewayToken);
  assert.deepEqual(second.generated, [], "nothing is minted on a box that already has a pair");
});

test("an explicitly configured secret wins and is never copied to disk", async () => {
  // A value an operator set belongs to them: persisting a copy would make a
  // later edit of their env file silently ineffective, because the copy would
  // be found first on the next boot.
  const { files, io } = memoryFs();
  const secrets = await resolveOpenClawLocalSecrets({
    stateDir: "/state",
    envBridgeToken: "operator-supplied-bridge-token",
    fs: io,
  });

  assert.equal(secrets.bridgeToken, "operator-supplied-bridge-token");
  assert.deepEqual(secrets.generated, ["gateway_token"]);
  const stored = JSON.parse(files.get(openClawLocalSecretsPath("/state")) ?? "{}");
  assert.equal(stored.bridgeToken, undefined, "an env-supplied secret is not persisted");
  assert.equal(stored.gatewayToken, secrets.gatewayToken);
});

test("an unwritable state directory degrades channels, never the gateway", async () => {
  // Boot must not die because a volume is read-only. Channels degrade;
  // nothing else does.
  const secrets = await resolveOpenClawLocalSecrets({
    stateDir: "/state",
    fs: {
      readFile: async () => {
        throw new Error("EACCES");
      },
      writeFile: async () => {
        throw new Error("EROFS");
      },
      mkdir: async () => {
        throw new Error("EROFS");
      },
    },
  });
  assert.ok(secrets.bridgeToken.length >= 32);
  assert.ok(secrets.gatewayToken.length >= 32);
});

// ── who the transport runs as ─────────────────────────────────────────────

test("the OpenClaw systemd unit names a User, and it is not root", async () => {
  // A unit in /etc/systemd/system with no `User=` runs as root. Twice wrong:
  // absurd authority for a radio, and `--profile <p>` resolves against $HOME,
  // so root would keep its state in /root/.openclaw-<p> while the gateway
  // reads and writes its own — one config, two instances, no error.
  const unit = resolveExpectedOpenClawSupervisorUnit({
    profile: "acme",
    binaryPath: "/usr/local/bin/openclaw",
    gatewayPort: 18789,
    environment: buildOpenClawSupervisedEnv({
      profile: "acme",
      homeDir: "/home/empyralis",
      pathEnv: "/usr/bin",
      bridgeToken: "bridge",
      bridgeEndpointUrl: "http://127.0.0.1:8790/openclaw/inbound",
    }),
    platform: "linux",
    homeDir: "/home/empyralis",
    logDir: "/var/log/empyralis",
    systemdUser: "empyralis",
  });

  assert.ok(unit);
  assert.match(unit!.contents, /^User=empyralis$/m);
  assert.match(unit!.contents, /^Group=empyralis$/m);
  assert.doesNotMatch(unit!.contents, /^User=root$/m);
  // The lockdown's other half lives in the unit itself: launchd and systemd
  // do not hand a login shell's environment to the jobs they start, so a
  // model credential exported in the operator's shell cannot reach OpenClaw.
  assert.doesNotMatch(unit!.contents, /ANTHROPIC|OPENAI|_API_KEY/);
  assert.match(unit!.contents, /^Environment=HOME=\/home\/empyralis$/m);
});

test("omitting `user` renders the gateway's own unit byte-identically", async () => {
  // The `user` field was added to a SHARED renderer. If its absence changed a
  // single byte, every existing Empyralis gateway would report supervisor
  // drift on its next boot and rewrite a unit that was already correct.
  const withoutUser = renderSystemdUnit({
    label: "empyralis-gateway.service",
    programArguments: ["/usr/bin/node", "/opt/gw/dist/index.js"],
    workingDirectory: "/opt/gw/dist",
    logPath: "/var/log/empyralis/gateway.log",
  });
  assert.doesNotMatch(withoutUser, /^User=/m);
  assert.match(withoutUser, /^Type=simple\nWorkingDirectory=\/opt\/gw\/dist$/m);
});

// ── the plan handed to the root installer ─────────────────────────────────

const PLAN_ENV = {
  PATH: "/usr/bin:/bin",
  EMPYRALIS_GATEWAY_STATE_DIR: path.join(os.tmpdir(), `openclaw-plan-test-${process.pid}`),
  EMPYRALIS_BRIDGE_TOKEN: "bridge-token-for-the-plan-test",
  EMPYRALIS_OPENCLAW_GATEWAY_TOKEN: "gateway-token-for-the-plan-test",
  EMPYRALIS_OPENCLAW_BINARY: "/usr/local/bin/openclaw",
  EMPYRALIS_OPENCLAW_PROFILE: "acme",
};

test("the install plan carries the pin, so no shell script ever transcribes it", async () => {
  // scripts/install-agent-computer.sh must write bytes it was handed and
  // never compose them. A `npm i -g openclaw@X` literal in a script served
  // over the public web — to boxes that fetch it once and never again — is a
  // second copy of a version number whose whole purpose is to be exact.
  const plan = await buildOpenClawInstallPlan({
    env: PLAN_ENV,
    platform: "linux",
    homeDir: "/home/empyralis",
  });
  assert.equal(plan.pinnedVersion, OPENCLAW_PINNED_VERSION);
  assert.equal(plan.packageSpec, `openclaw@${OPENCLAW_PINNED_VERSION}`);
  assert.equal(plan.profile, "acme");
  assert.equal(plan.profileStateDir, "/home/empyralis/.openclaw-acme");
  assert.equal(plan.runtimeInstall, undefined, "a plan without --ensure-runtime installs nothing");
});

test("the Linux plan carries a complete, startable unit", async () => {
  const plan = await buildOpenClawInstallPlan({
    env: PLAN_ENV,
    platform: "linux",
    homeDir: "/home/empyralis",
  });
  assert.ok(plan.unit, "a Linux box needs a unit the installer can write as root");
  assert.equal(plan.unit!.mode, "systemd");
  assert.equal(plan.unit!.path, "/etc/systemd/system/ai.empyralis.openclaw.acme.service");
  // ExecStart must be absolute and must be the binary the plan resolved —
  // systemd refuses a relative ExecStart, and a unit pointing at a binary
  // that is not there is a five-second restart loop, forever.
  assert.match(plan.unit!.contents, /^ExecStart=\/usr\/local\/bin\/openclaw --profile acme gateway run /m);
  assert.match(plan.unit!.contents, /--bind loopback --auth token$/m);
  // The token is NOT in argv: argv is world-readable through `ps`, and the
  // token is already in the config provisioning writes.
  assert.doesNotMatch(plan.unit!.contents, /--token/);
  assert.match(plan.unit!.contents, /^Restart=always$/m);
});

test("the macOS plan carries no unit, because the gateway installs its own", async () => {
  // `~/Library/LaunchAgents` needs no privilege, so the gateway's own
  // provisioning run installs and registers the LaunchAgent. Handing an
  // installer a plist to write would be a second writer for a file with one
  // owner.
  const plan = await buildOpenClawInstallPlan({
    env: PLAN_ENV,
    platform: "darwin",
    homeDir: "/Users/someone",
  });
  assert.equal(plan.unit, null);
  assert.equal(plan.profileStateDir, "/Users/someone/.openclaw-acme");
});
