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
import {
  OPENCLAW_NODE_BIN_DIR_ENV,
  withOpenClawNodeOnPath,
} from "../openclaw/provisioning/openclaw-node-runtime";
import { ensureOpenClawRuntimeInstalled } from "../openclaw/provisioning/openclaw-runtime-install";
import { OpenClawCli } from "../openclaw/provisioning/openclaw-cli";
import {
  OPENCLAW_NODE_MINIMUM_VERSION,
  OPENCLAW_PINNED_VERSION,
  nodeSatisfiesOpenClaw,
} from "../openclaw/provisioning/openclaw-version";

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
  // The directory of the Node running this test, so `node --version` resolves
  // — the plan asks the box which Node the transport would get, and a PATH
  // with no node at all is (correctly) "unusable".
  PATH: `${path.dirname(process.execPath)}:/usr/bin:/bin`,
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

test("a box whose Node is too old gets NO unit and NO provision", async () => {
  // The failure this prevents, measured on a real Ubuntu 24.04 box: the
  // install succeeds under Node 20, the binary lands on PATH, and the unit
  // becomes a five-second restart loop while the installer logs success.
  // No usable Node -> no unit -> the installer has nothing to start.
  const plan = await buildOpenClawInstallPlan({
    env: { ...PLAN_ENV, PATH: "/nonexistent-bin" },
    platform: "linux",
    homeDir: "/home/empyralis",
  });
  assert.equal(plan.nodeRuntime.satisfied, false);
  assert.equal(plan.unit, null, "never hand the installer a unit that cannot start");
  assert.equal(plan.provision, undefined, "and never claim the transport was configured");
  assert.equal(plan.nodeRuntime.minimumVersion, OPENCLAW_NODE_MINIMUM_VERSION);
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

// ── the Node the transport runs on ────────────────────────────────────────
//
// Found by running the real installer end to end on Ubuntu 24.04: openclaw
// requires Node >= 22.19, every Agent Computer installs Node 20, and
// `npm install --global` does NOT enforce `engines`. So the install succeeds,
// the binary lands on PATH, and every invocation exits with an nvm suggestion
// — a supervised unit pointing at it is a five-second restart loop on a box
// that reported a clean install. None of that is visible from a code reading.

test("Node 20 does not satisfy the transport, Node 22.19+ does", async () => {
  assert.equal(nodeSatisfiesOpenClaw("v20.20.2"), false);
  assert.equal(nodeSatisfiesOpenClaw("v22.18.0"), false, "the floor is 22.19, not 22");
  assert.equal(nodeSatisfiesOpenClaw(`v${OPENCLAW_NODE_MINIMUM_VERSION}`), true);
  assert.equal(nodeSatisfiesOpenClaw("v22.20.0"), true);
  assert.equal(nodeSatisfiesOpenClaw("v26.4.0"), true, "the founder's Mac, which is why this was never seen");
  // An unreadable answer is NOT treated as fine. `openclaw --version` on an
  // old Node prints its own error text, and a permissive parse there would
  // read that error as a pass.
  assert.equal(nodeSatisfiesOpenClaw(undefined), false);
  assert.equal(nodeSatisfiesOpenClaw("openclaw: Node.js v22.19+ is required"), false);
});

test("an install onto too-old Node is REFUSED, before npm runs", async () => {
  // Before, not after: a 350MB install that leaves a binary which cannot run
  // is strictly worse than no install, because the box then looks equipped.
  const npmCalls: string[][] = [];
  const outcome = await ensureOpenClawRuntimeInstalled({
    cli: new OpenClawCli({
      profile: "acme",
      env: { PATH: "/usr/bin" },
      exec: async () => ({ code: 127, stdout: "", stderr: "not found" }),
    }),
    nodeVersion: "v20.20.2",
    runNpm: async (args) => {
      npmCalls.push(args);
      return { code: 0, stdout: "", stderr: "" };
    },
  });
  assert.equal(outcome.action, "failed");
  assert.equal(outcome.refusal?.code, "openclaw_runtime_node_too_old");
  assert.equal(npmCalls.length, 0, "nothing may be downloaded onto a Node that cannot run it");
});

test("the transport Node goes FIRST on the child PATH, and only the child's", async () => {
  // The installed `openclaw` bin is `#!/usr/bin/env node`, so the runtime that
  // executes it is whatever PATH resolves at RUN time — not the one that
  // installed it. Install under one Node and supervise under another and the
  // transport installs cleanly, then exits on every call.
  const env = { PATH: "/usr/bin:/bin", [OPENCLAW_NODE_BIN_DIR_ENV]: "/opt/empyralis/openclaw-node/bin" };
  const child = withOpenClawNodeOnPath(env);
  assert.equal(child.PATH, "/opt/empyralis/openclaw-node/bin:/usr/bin:/bin");
  assert.equal(env.PATH, "/usr/bin:/bin", "the caller's own PATH is never mutated");

  // Idempotent — a reconcile must not grow the PATH on every boot.
  assert.equal(withOpenClawNodeOnPath(child).PATH, child.PATH);

  // Unconfigured leaves PATH alone rather than prepending an empty entry,
  // which most resolvers read as "the current directory".
  assert.equal(withOpenClawNodeOnPath({ PATH: "/usr/bin" }).PATH, "/usr/bin");
});
