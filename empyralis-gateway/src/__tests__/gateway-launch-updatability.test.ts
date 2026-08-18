import test from "node:test";
import assert from "node:assert/strict";

import {
  LAUNCH_CODE_NO_RESTART_ON_CLEAN_EXIT,
  LAUNCH_CODE_PATH_OUTSIDE_LAYOUT,
  classifyGatewayLaunchUpdatability,
  isPathInside,
  launcherResolvesReleaseLayout,
  parseLaunchdKeepAlive,
  parseLaunchdProgramArguments,
  parseSystemdExecStart,
  parseSystemdRestartPolicy,
  parseSystemdUnitFromCgroup,
} from "../update/gateway-launch-updatability";

/**
 * MAN-355, the half `d3d42ce11` could not reach: an ALREADY-supervised box.
 *
 * The unit contents below are not invented. They are what production
 * (165.227.25.201) actually carries, read on 2026-08-18 — including the
 * details that make it un-updatable in two independent ways: an ExecStart
 * outside the release layout, and `Restart=on-failure`, under which the
 * clean exit a self-update ends with leaves the box switched off rather than
 * starting the new build.
 */

const PRODUCTION_UNIT = `[Unit]
Description=Empyralis Gateway (channels-via-gateway build - isolated instance)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=empyralis-gw
Group=empyralis-gw
EnvironmentFile=/etc/empyralis/gateway-channels.env
WorkingDirectory=/opt/empyralis-app/empyralis-gateway
ExecStart=/usr/bin/node /opt/empyralis-app/empyralis-gateway/dist/index.js
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
`;

const PRODUCTION_CGROUP = "0::/system.slice/empyralis-gateway-channels.service\n";

const SYSTEMD_ENV = { INVOCATION_ID: "abc123" } as NodeJS.ProcessEnv;

function readerFor(files: Record<string, string>) {
  return async (filePath: string): Promise<string> => {
    if (filePath in files) {
      return files[filePath];
    }
    const error = new Error(`ENOENT: ${filePath}`) as NodeJS.ErrnoException;
    error.code = "ENOENT";
    throw error;
  };
}

test("production's real unit is reported as not updatable, naming BOTH blockers separately", async () => {
  const result = await classifyGatewayLaunchUpdatability({
    env: SYSTEMD_ENV,
    platform: "linux",
    stateDir: "/var/lib/empyralis-gw/state",
    readFile: readerFor({
      "/proc/self/cgroup": PRODUCTION_CGROUP,
      "/etc/systemd/system/empyralis-gateway-channels.service": PRODUCTION_UNIT,
    }),
  });

  assert.equal(result.status, "not_updatable");
  assert.equal(result.unitPath, "/etc/systemd/system/empyralis-gateway-channels.service");
  assert.equal(
    result.launchCommand,
    "/usr/bin/node /opt/empyralis-app/empyralis-gateway/dist/index.js",
  );
  assert.equal(
    result.expectedEntrypoint,
    "/var/lib/empyralis-gw/gateway-releases/current/gateway/dist/index.js",
  );
  const codes = result.blockers.map((blocker) => blocker.code).sort();
  assert.deepEqual(codes, [LAUNCH_CODE_PATH_OUTSIDE_LAYOUT, LAUNCH_CODE_NO_RESTART_ON_CLEAN_EXIT].sort());
  // Two different facts, two different fixes — never one merged sentence.
  assert.equal(new Set(result.blockers.map((blocker) => blocker.detail)).size, 2);
  assert.equal(result.unknownReason, null);
});

test("an installer-provisioned box, whose launcher resolves the layout at start, is updatable", async () => {
  const runGateway = `#!/usr/bin/env bash
set -Eeuo pipefail
SELF_UPDATE_INSTALL_ROOT="\${EMPYRALIS_GATEWAY_INSTALL_ROOT:-}"
if [[ -n "\${SELF_UPDATE_INSTALL_ROOT}" && -f "\${SELF_UPDATE_INSTALL_ROOT}/current/gateway/dist/index.js" ]]; then
  entry="\${SELF_UPDATE_INSTALL_ROOT}/current/gateway/dist/index.js"
fi
exec node "\${entry}"
`;
  const result = await classifyGatewayLaunchUpdatability({
    env: SYSTEMD_ENV,
    platform: "linux",
    stateDir: "/var/lib/empyralis/agent-computer/gateway",
    readFile: readerFor({
      "/proc/self/cgroup": "0::/system.slice/empyralis-gateway.service\n",
      "/etc/systemd/system/empyralis-gateway.service": [
        "[Service]",
        "ExecStart=/opt/empyralis/agent-computer/bin/run-gateway",
        "Restart=always",
      ].join("\n"),
      "/opt/empyralis/agent-computer/bin/run-gateway": runGateway,
    }),
  });

  assert.equal(result.status, "updatable");
  assert.deepEqual(result.blockers, []);
});

test("an OLD launcher — one predating the release-layout block — is correctly reported stuck", async () => {
  const oldRunGateway = `#!/usr/bin/env bash
set -Eeuo pipefail
INSTALL_DIR="/opt/empyralis/agent-computer/current"
exec node "\${INSTALL_DIR}/gateway/dist/index.js"
`;
  const result = await classifyGatewayLaunchUpdatability({
    env: SYSTEMD_ENV,
    platform: "linux",
    stateDir: "/var/lib/empyralis/agent-computer/gateway",
    readFile: readerFor({
      "/proc/self/cgroup": "0::/system.slice/empyralis-gateway.service\n",
      "/etc/systemd/system/empyralis-gateway.service": [
        "[Service]",
        "ExecStart=/opt/empyralis/agent-computer/bin/run-gateway",
        "Restart=always",
      ].join("\n"),
      "/opt/empyralis/agent-computer/bin/run-gateway": oldRunGateway,
    }),
  });

  assert.equal(result.status, "not_updatable");
  assert.deepEqual(
    result.blockers.map((blocker) => blocker.code),
    [LAUNCH_CODE_PATH_OUTSIDE_LAYOUT],
  );
});

test("a unit pointing straight into the release layout is updatable without any launcher script", async () => {
  const result = await classifyGatewayLaunchUpdatability({
    env: SYSTEMD_ENV,
    platform: "linux",
    stateDir: "/var/lib/empyralis-gw/state",
    readFile: readerFor({
      "/proc/self/cgroup": PRODUCTION_CGROUP,
      "/etc/systemd/system/empyralis-gateway-channels.service": [
        "[Service]",
        "ExecStart=/usr/bin/node /var/lib/empyralis-gw/gateway-releases/current/gateway/dist/index.js",
        "Restart=always",
      ].join("\n"),
    }),
  });

  assert.equal(result.status, "updatable");
});

test("EVERY way of not knowing resolves to unknown, never to not_updatable", async () => {
  const unsupervised = await classifyGatewayLaunchUpdatability({
    env: {},
    platform: "linux",
    stateDir: "/var/lib/empyralis-gw/state",
    readFile: readerFor({}),
  });
  assert.equal(unsupervised.status, "unknown");
  assert.ok(unsupervised.unknownReason);

  const noStateDir = await classifyGatewayLaunchUpdatability({
    env: SYSTEMD_ENV,
    platform: "linux",
    readFile: readerFor({}),
  });
  assert.equal(noStateDir.status, "unknown");

  const unreadableUnit = await classifyGatewayLaunchUpdatability({
    env: SYSTEMD_ENV,
    platform: "linux",
    stateDir: "/var/lib/empyralis-gw/state",
    readFile: readerFor({ "/proc/self/cgroup": PRODUCTION_CGROUP }),
  });
  assert.equal(unreadableUnit.status, "unknown");

  const noExecStart = await classifyGatewayLaunchUpdatability({
    env: SYSTEMD_ENV,
    platform: "linux",
    stateDir: "/var/lib/empyralis-gw/state",
    readFile: readerFor({
      "/proc/self/cgroup": PRODUCTION_CGROUP,
      "/etc/systemd/system/empyralis-gateway-channels.service": "[Service]\nRestart=always\n",
    }),
  });
  assert.equal(noExecStart.status, "unknown");
});

test("a probe that throws for EVERY path — including /proc — still answers, and answers unknown", async () => {
  const result = await classifyGatewayLaunchUpdatability({
    env: SYSTEMD_ENV,
    platform: "linux",
    stateDir: "/var/lib/empyralis-gw/state",
    readFile: async () => {
      throw new Error("boom");
    },
  });
  assert.equal(result.status, "unknown");
  assert.deepEqual(result.blockers, []);
});

test("an unsupported platform is unknown, not a claim about updatability", async () => {
  const result = await classifyGatewayLaunchUpdatability({
    env: { INVOCATION_ID: "x" },
    platform: "win32",
    stateDir: "C:/state",
    readFile: readerFor({}),
  });
  assert.equal(result.status, "unknown");
});

test("a launchd plist pinned outside the layout, with no KeepAlive, reports both blockers", async () => {
  const plist = `<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.empyralis.agent-computer</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/node</string>
    <string>/Users/someone/checkout/empyralis-gateway/dist/index.js</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
`;
  const result = await classifyGatewayLaunchUpdatability({
    env: { EMPYRALIS_LAUNCHD_LABEL: "ai.empyralis.agent-computer" },
    platform: "darwin",
    homeDir: "/Users/someone",
    stateDir: "/Users/someone/.empyralis/gateway",
    readFile: readerFor({
      "/Users/someone/Library/LaunchAgents/ai.empyralis.agent-computer.plist": plist,
    }),
  });

  assert.equal(result.status, "not_updatable");
  assert.deepEqual(
    result.blockers.map((blocker) => blocker.code).sort(),
    [LAUNCH_CODE_PATH_OUTSIDE_LAYOUT, LAUNCH_CODE_NO_RESTART_ON_CLEAN_EXIT].sort(),
  );
});

test("a launchd plist with KeepAlive true, pointing into the layout, is updatable", async () => {
  const plist = `<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/node</string>
    <string>/Users/someone/.empyralis/gateway-releases/current/gateway/dist/index.js</string>
  </array>
  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
`;
  const result = await classifyGatewayLaunchUpdatability({
    env: { EMPYRALIS_LAUNCHD_LABEL: "ai.empyralis.agent-computer" },
    platform: "darwin",
    homeDir: "/Users/someone",
    stateDir: "/Users/someone/.empyralis/gateway",
    readFile: readerFor({
      "/Users/someone/Library/LaunchAgents/ai.empyralis.agent-computer.plist": plist,
    }),
  });
  assert.equal(result.status, "updatable");
});

test("parsers: the shapes systemd and launchd actually produce", () => {
  assert.equal(
    parseSystemdUnitFromCgroup("0::/system.slice/empyralis-gateway-channels.service\n"),
    "empyralis-gateway-channels.service",
  );
  assert.equal(parseSystemdUnitFromCgroup("0::/user.slice/session-3.scope\n"), null);

  // systemd's own ExecStart prefixes must not leak into the command.
  assert.equal(parseSystemdExecStart("[Service]\nExecStart=-/usr/bin/node /a.js"), "/usr/bin/node /a.js");
  assert.equal(parseSystemdExecStart("[Service]\nExecStart=+/usr/bin/node /a.js"), "/usr/bin/node /a.js");
  assert.equal(
    parseSystemdExecStart("[Service]\nExecStart=/usr/bin/node \\\n  /a.js"),
    "/usr/bin/node /a.js",
  );
  assert.equal(parseSystemdExecStart("[Service]\nRestart=always"), null);

  // Last assignment wins, matching systemd's own parsing.
  assert.equal(parseSystemdRestartPolicy("Restart=no\nRestart=always"), "always");
  assert.equal(parseSystemdRestartPolicy("[Service]\nType=simple"), null);

  assert.deepEqual(
    parseLaunchdProgramArguments(
      "<key>ProgramArguments</key><array><string>/bin/node</string><string>/a.js</string></array>",
    ),
    ["/bin/node", "/a.js"],
  );
  assert.deepEqual(parseLaunchdProgramArguments("<plist></plist>"), []);
  assert.equal(parseLaunchdKeepAlive("<key>KeepAlive</key><true/>"), true);
  assert.equal(parseLaunchdKeepAlive("<key>KeepAlive</key><false/>"), false);
  // A KeepAlive DICT is conditional, so it is not a promise that a
  // deliberate exit comes back.
  assert.equal(
    parseLaunchdKeepAlive("<key>KeepAlive</key><dict><key>Crashed</key><true/></dict>"),
    false,
  );
});

test("path containment does not match a sibling directory that merely shares a prefix", () => {
  assert.equal(isPathInside("/var/lib/gw", "/var/lib/gw/releases/1"), true);
  assert.equal(isPathInside("/var/lib/gw", "/var/lib/gw"), true);
  assert.equal(isPathInside("/var/lib/gw", "/var/lib/gw-old/releases/1"), false);
  assert.equal(isPathInside("/var/lib/gw", "/opt/app/dist/index.js"), false);
});

test("a launcher is only credited when it BOTH tests the layout entrypoint and derives the root", () => {
  assert.equal(
    launcherResolvesReleaseLayout(
      '#!/bin/sh\n[ -f "$R/current/gateway/dist/index.js" ] && exec node "$R/current/gateway/dist/index.js"\nEMPYRALIS_GATEWAY_INSTALL_ROOT=x\n',
    ),
    true,
  );
  // Mentions the root but never resolves through it.
  assert.equal(
    launcherResolvesReleaseLayout("#!/bin/sh\nEMPYRALIS_GATEWAY_INSTALL_ROOT=x\nexec node /opt/a.js\n"),
    false,
  );
  // A binary must never be sniffed as a launcher.
  assert.equal(
    launcherResolvesReleaseLayout("\x7fELF...gateway-releases/current/gateway/dist/index.js"),
    false,
  );
});
