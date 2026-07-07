import test from "node:test";
import assert from "node:assert/strict";
import fs from "fs";
import os from "os";
import path from "path";

import {
  checkFilesystemPathPolicy,
  checkShellCommandPolicy,
  commandTouchesProtectedPath,
  hardBlockedCommand,
  hardProtectedPath,
} from "../shell/command-policy";

test("hard-blocked commands are caught", () => {
  const blocked = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs.ext4 /dev/sda",
    "diskutil eraseDisk JHFS+ Empyralis /dev/disk0",
    "dd if=/dev/zero of=/dev/sda",
    ":(){ :|:& };:",
    "shutdown -h now",
    "reboot",
    "halt",
    "poweroff",
    "chmod -R 777 /",
    "CHOWN -R nobody /",
  ];
  for (const command of blocked) {
    assert.equal(hardBlockedCommand(command), true, `expected "${command}" to be hard-blocked`);
    const result = checkShellCommandPolicy(command);
    assert.ok(result, `expected checkShellCommandPolicy to block "${command}"`);
    assert.equal(result?.code, "hard_blocked_command");
  }
});

test("workspace-scoped rm/dd exceptions are NOT blocked by the hard command list", () => {
  const allowed = ["rm -rf ~/workspace", "rm -rf ~/workspace/output", "rm -rf /tmp/scratch"];
  for (const command of allowed) {
    assert.equal(hardBlockedCommand(command), false, `expected "${command}" to be allowed`);
  }
});

test("ordinary commands pass the policy cleanly", () => {
  for (const command of ["ls -la", "cat package.json", "echo hello world", "pwd"]) {
    assert.equal(checkShellCommandPolicy(command), null);
  }
});

test("hard-protected paths are blocked by filesystem policy", (t) => {
  const home = os.tmpdir();
  const original = process.env.HOME;
  process.env.HOME = home;
  t.after(() => {
    process.env.HOME = original;
  });

  assert.equal(checkFilesystemPathPolicy("/etc/empyralis/config.json") !== null, true);
  assert.equal(checkFilesystemPathPolicy("~/.ssh/id_rsa") !== null, true);
  assert.equal(checkFilesystemPathPolicy("/var/lib/empyralis/agent-computer/state.db") !== null, true);
  assert.equal(hardProtectedPath(path.join(home, ".empyralis", "state", "vault", "key")), true);
  assert.equal(checkFilesystemPathPolicy("/tmp/scratch/output.txt"), null);
});

test("shell commands referencing protected paths directly are blocked", () => {
  const result = checkShellCommandPolicy("cat ~/.ssh/id_rsa");
  assert.ok(result);
  assert.equal(result?.code, "hard_protected_path");

  assert.equal(checkShellCommandPolicy("cat /workspace/notes.txt"), null);
});

test("symlink pointing at a protected path is caught even when the raw token looks harmless", (t) => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-policy-test-"));
  t.after(() => {
    fs.rmSync(tempDir, { recursive: true, force: true });
  });
  const fakeVault = path.join(tempDir, ".empyralis", "state", "vault");
  fs.mkdirSync(fakeVault, { recursive: true });
  fs.writeFileSync(path.join(fakeVault, "credentials.json"), "{}");
  const workspaceLink = path.join(tempDir, "workspace_link");
  fs.symlinkSync(fakeVault, workspaceLink);

  const rawCommand = `cat ${workspaceLink}/credentials.json`;
  assert.equal(commandTouchesProtectedPath(rawCommand), true, "raw command string alone should not obviously look protected");

  const result = checkShellCommandPolicy(rawCommand);
  assert.ok(result, "symlink bypass should be caught by canonicalization");
  assert.equal(result?.code, "hard_protected_path");
});

test("filesystem path traversal via symlink to a protected directory is blocked", (t) => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-policy-test-"));
  t.after(() => {
    fs.rmSync(tempDir, { recursive: true, force: true });
  });
  const fakeSshDir = path.join(tempDir, ".ssh");
  fs.mkdirSync(fakeSshDir, { recursive: true });
  const link = path.join(tempDir, "not_obviously_ssh");
  fs.symlinkSync(fakeSshDir, link);

  assert.equal(hardProtectedPath(path.join(link, "id_rsa")), true);
});

test("a not-yet-created output path is still checked against its parent directory", (t) => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "empyralis-policy-test-"));
  t.after(() => {
    fs.rmSync(tempDir, { recursive: true, force: true });
  });
  const protectedDir = path.join(tempDir, "var", "lib", "empyralis", "agent-computer");
  fs.mkdirSync(protectedDir, { recursive: true });
  const notYetCreatedFile = path.join(protectedDir, "brand-new-file.txt");

  assert.equal(fs.existsSync(notYetCreatedFile), false);
  assert.equal(hardProtectedPath(notYetCreatedFile), true);
});
