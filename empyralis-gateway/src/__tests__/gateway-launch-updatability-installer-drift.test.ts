import test from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import path from "path";

import { launcherResolvesReleaseLayout } from "../update/gateway-launch-updatability";

/**
 * The detector's "this launcher resolves the release layout" rule is checked
 * against the REAL launchers, from the repository, rather than against a copy
 * pasted into a fixture.
 *
 * That matters in one specific direction: if someone reworks
 * install-agent-computer.sh's run-gateway heredoc and the detector stops
 * recognising it, every correctly-installed box in the fleet starts reporting
 * itself un-updatable and loses its updates — a false "not_updatable" is the
 * expensive failure here, not a false "unknown". Expected set (the installer's
 * own source) and actual set (the detector) therefore come from two different
 * places, which is this codebase's own rule for a conformance check.
 *
 * Every source-scanning test in this repo now carries a canary, after one was
 * found scanning zero files and reporting green (`6d6de2481`).
 */

function resolveRepoRoot(): string {
  const candidates = [
    // dist/__tests__ -> dist -> empyralis-gateway -> repo root
    path.resolve(__dirname, "..", "..", ".."),
    // src/__tests__ -> src -> empyralis-gateway -> repo root
    path.resolve(__dirname, "..", "..", ".."),
    path.resolve(__dirname, "..", "..", "..", ".."),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, "scripts", "install-agent-computer.sh"))) {
      return candidate;
    }
  }
  throw new Error(
    `could not locate the repo root from ${__dirname} — tried ${candidates.join(", ")}`,
  );
}

const REPO_ROOT = resolveRepoRoot();
const INSTALLER_PATH = path.join(REPO_ROOT, "scripts", "install-agent-computer.sh");
const PACKER_LAUNCHER_PATH = path.join(REPO_ROOT, "deploy", "packer", "files", "run-gateway");

/** The installer writes its launcher as a quoted heredoc, so the script body
 *  is extracted verbatim rather than re-typed here. */
function extractInstallerRunGateway(installerSource: string): string {
  const start = installerSource.indexOf(`cat > "\${BIN_DIR}/run-gateway" <<'EOF'`);
  if (start < 0) {
    throw new Error("the installer no longer writes run-gateway with a quoted heredoc");
  }
  const bodyStart = installerSource.indexOf("\n", start) + 1;
  const end = installerSource.indexOf("\nEOF\n", bodyStart);
  if (end < 0) {
    throw new Error("the installer's run-gateway heredoc is not terminated");
  }
  return installerSource.slice(bodyStart, end);
}

test("CANARY: both real launchers are actually being read", () => {
  const installerSource = fs.readFileSync(INSTALLER_PATH, "utf8");
  assert.ok(installerSource.length > 10_000, "install-agent-computer.sh looks empty or truncated");
  const body = extractInstallerRunGateway(installerSource);
  assert.ok(body.length > 400, "the extracted run-gateway body looks empty or truncated");
  assert.match(body, /^#!/, "the extracted body is not a script");

  const packerSource = fs.readFileSync(PACKER_LAUNCHER_PATH, "utf8");
  assert.ok(packerSource.length > 400, "deploy/packer/files/run-gateway looks empty or truncated");
});

test("the shell launcher the installer writes is recognised as resolving the release layout", () => {
  const body = extractInstallerRunGateway(fs.readFileSync(INSTALLER_PATH, "utf8"));
  assert.equal(
    launcherResolvesReleaseLayout(body),
    true,
    "install-agent-computer.sh's run-gateway is no longer recognised — every installer-provisioned box would start reporting itself un-updatable",
  );
});

test("the baked-image launcher is recognised the same way", () => {
  const packerSource = fs.readFileSync(PACKER_LAUNCHER_PATH, "utf8");
  assert.equal(
    launcherResolvesReleaseLayout(packerSource),
    true,
    "deploy/packer/files/run-gateway is no longer recognised — every image-provisioned box would start reporting itself un-updatable",
  );
});

test("the two launchers still agree, so a box's updatability cannot depend on how it was provisioned", () => {
  const installerBody = extractInstallerRunGateway(fs.readFileSync(INSTALLER_PATH, "utf8"));
  const packerSource = fs.readFileSync(PACKER_LAUNCHER_PATH, "utf8");
  const normalize = (source: string): string =>
    source
      .split("\n")
      .filter((line) => !line.trim().startsWith("#"))
      .map((line) => line.trimEnd())
      .filter((line) => line.length > 0)
      .join("\n");
  assert.equal(
    normalize(installerBody),
    normalize(packerSource),
    "deploy/packer/files/run-gateway is documented as a verbatim copy of the installer's heredoc and has drifted from it",
  );
});
