import test from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import path from "path";

import {
  GATEWAY_DESKTOP_MANAGED_ENV,
  desktopManagedSupervisorEnvironment,
  isDesktopManagedGateway,
} from "../update/gateway-desktop-managed";
import { resolveExpectedSupervisorUnit } from "../update/gateway-supervisor-install";

const DESKTOP_ENV = { [GATEWAY_DESKTOP_MANAGED_ENV]: "1" } as NodeJS.ProcessEnv;

test("a VPS box is not desktop-managed and its unit does not move", () => {
  // THE LOAD-BEARING ASSERTION IN THIS FILE. Every Agent Computer in the
  // fleet renders its unit with no environment today, and the systemd unit
  // lives at /etc/systemd/system, which the unprivileged service user cannot
  // write. If this change made those units render differently, every box
  // would report drift it can never repair — forever, and all at once.
  assert.equal(isDesktopManagedGateway({}), false);
  assert.equal(
    desktopManagedSupervisorEnvironment({
      stateDir: "/var/lib/empyralis-gw/state",
      apiBaseUrl: "https://empyralis.ai/api",
      env: {},
    }),
    undefined,
  );

  const base = {
    platform: "linux" as NodeJS.Platform,
    env: {} as NodeJS.ProcessEnv,
    homeDir: "/home/gw",
    execPath: "/usr/bin/node",
    entryPath: "/opt/empyralis/agent-computer/gateway/dist/index.js",
    logDir: "/var/lib/empyralis-gw/state/logs",
  };
  const before = resolveExpectedSupervisorUnit(base);
  const after = resolveExpectedSupervisorUnit({ ...base, environment: undefined });
  assert.equal(before?.contents, after?.contents);
  assert.ok(!before?.contents.includes("Environment="), "a VPS unit grew an Environment= line");
});

test("a desktop login item carries the state dir and control plane the app resolved", () => {
  // THE BUG THIS CLOSES: the plist used to carry no environment at all, so
  // the gateway launchd started at login fell back to ~/.empyralis/gateway
  // and http://127.0.0.1:8001/api — a directory with none of this machine's
  // pairing credentials, pointed at a control plane that does not exist on a
  // customer's Mac. The Agent Computer only came back while the app was open.
  const environment = desktopManagedSupervisorEnvironment({
    stateDir: "/Users/x/Library/Application Support/com.empyralis.desktop/gateway",
    apiBaseUrl: "https://empyralis.ai/api",
    env: DESKTOP_ENV,
  });
  assert.deepEqual(environment, {
    EMPYRALIS_GATEWAY_API_URL: "https://empyralis.ai/api",
    EMPYRALIS_GATEWAY_STATE_DIR:
      "/Users/x/Library/Application Support/com.empyralis.desktop/gateway",
    [GATEWAY_DESKTOP_MANAGED_ENV]: "1",
  });

  const unit = resolveExpectedSupervisorUnit({
    platform: "darwin",
    env: DESKTOP_ENV,
    homeDir: "/Users/x",
    execPath: "/Applications/Empyralis.app/Contents/Resources/node-runtime/bin/node",
    entryPath: "/Applications/Empyralis.app/Contents/Resources/gateway/dist/index.js",
    logDir: "/Users/x/Library/Application Support/com.empyralis.desktop/gateway/logs",
    environment,
  });
  assert.ok(unit);
  assert.match(unit.contents, /<key>EnvironmentVariables<\/key>/);
  assert.match(unit.contents, /EMPYRALIS_GATEWAY_STATE_DIR/);
  assert.match(unit.contents, /EMPYRALIS_GATEWAY_API_URL/);
  // Carried forward so the login-item gateway reports itself the same way the
  // app-spawned one does. Without it the backend gets two different answers
  // about one machine depending on which process connected last.
  assert.match(unit.contents, new RegExp(GATEWAY_DESKTOP_MANAGED_ENV));
});

test("a half-known environment is refused rather than half-written", () => {
  // Pinning one value and defaulting the other is the original bug wearing a
  // different mask, and it would be harder to see.
  for (const partial of [
    { stateDir: "", apiBaseUrl: "https://empyralis.ai/api" },
    { stateDir: "/s", apiBaseUrl: "" },
  ]) {
    assert.equal(
      desktopManagedSupervisorEnvironment({ ...partial, env: DESKTOP_ENV }),
      undefined,
    );
  }
});

test("only the literal 1 marks a box desktop-managed", () => {
  for (const value of ["", "0", "false", "true", "yes", " "]) {
    assert.equal(
      isDesktopManagedGateway({ [GATEWAY_DESKTOP_MANAGED_ENV]: value }),
      false,
      `"${value}" was treated as desktop-managed`,
    );
  }
  assert.equal(isDesktopManagedGateway({ [GATEWAY_DESKTOP_MANAGED_ENV]: " 1 " }), true);
});

test("the gateway actually reports this to the backend and skips the launchctl repair", () => {
  // WIRING, not behaviour. A pure module nothing calls is this codebase's
  // most-documented defect, and neither of these two call sites can be
  // reached from a unit test: one is inside the connect frame builder, the
  // other inside main()'s startup path.
  //
  // Read from SOURCE rather than dist/, with a canary, because `npm test`
  // runs out of dist/ where there are no .ts files at all — the exact trap
  // that made exec-file-timeout-child-leak.test.ts's own scan vacuous.
  const root = path.resolve(__dirname, "..", "..", "src");
  assert.ok(fs.existsSync(root), `the source tree is not at ${root}; this scan proves nothing`);

  const index = fs.readFileSync(path.join(root, "index.ts"), "utf8");
  assert.match(index, /desktopManagedSupervisorEnvironment\(/, "the login item is written without the app's environment");
  assert.match(index, /isDesktopManagedGateway\(\)/, "nothing asks whether this box is desktop-managed");
  assert.match(
    index,
    /status === "not_updatable" && !desktopManaged/,
    "a desktop box would still be handed launchctl commands to repair a machine that is working",
  );

  const wsClient = fs.readFileSync(path.join(root, "cloud", "ws-client.ts"), "utf8");
  assert.match(
    wsClient,
    /gateway_desktop_managed: runtimeMetadata\.desktopManaged/,
    "the backend is never told which update path this box is on, so it cannot refuse an unobservable gateway update",
  );
});
