/**
 * A supervisor unit whose program path is not ABSOLUTE cannot start, on
 * either platform, and says nothing when it fails.
 *
 * Observed live on macOS 2026-08-14: the Empyralis-managed OpenClaw plist
 * carried `ProgramArguments[0] == "openclaw"`, `launchctl list` reported
 * `- 78 ai.empyralis.openclaw.empyralis` (EX_CONFIG) permanently, and the log
 * file was ZERO BYTES — launchd fails before exec, so there is no process
 * whose output could have been redirected. The identical argv run by hand
 * with a normal PATH works, which is what proves it is resolution and not the
 * program. systemd is stricter: a non-absolute `ExecStart=` fails unit
 * validation at load.
 *
 * A behavioural test cannot catch this. The bare name type-checks, renders a
 * perfectly well-formed plist, and is CORRECT for OpenClawCli's own execFile
 * calls (execFile does a PATH lookup; launchd does not). So the assertions
 * here are structural: the resolver only ever answers with an absolute path,
 * and the unit renderer refuses everything else instead of emitting a file
 * that provably cannot run.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { resolveOpenClawBinaryPath } from "../openclaw/provisioning/openclaw-binary-path";
import {
  auditAndRepairOpenClawSupervisorUnit,
  buildOpenClawSupervisedEnv,
  resolveExpectedOpenClawSupervisorUnit,
} from "../openclaw/provisioning/openclaw-supervisor-unit";

const ENVIRONMENT = buildOpenClawSupervisedEnv({
  profile: "acme",
  homeDir: "/Users/tester",
  pathEnv: "/usr/local/bin:/usr/bin:/bin",
  bridgeToken: "bridge",
  bridgeEndpointUrl: "http://127.0.0.1:8790/openclaw/inbound",
});

function unitOptions(binaryPath: string | undefined, platform: NodeJS.Platform) {
  return {
    profile: "acme",
    binaryPath,
    gatewayPort: 18789,
    environment: ENVIRONMENT,
    platform,
    homeDir: platform === "darwin" ? "/Users/tester" : "/home/empyralis",
    logDir: "/opt/empyralis/gateway/state/logs",
  };
}

// ---------------------------------------------------------------------------
// resolveOpenClawBinaryPath: absolute, or nothing

test("an absolute configured path is taken as given, with no PATH lookup at all", async () => {
  let looked = 0;
  const resolved = await resolveOpenClawBinaryPath("/opt/homebrew/bin/openclaw", {}, async () => {
    looked += 1;
    return "/should/never/be/consulted";
  });
  assert.equal(resolved, "/opt/homebrew/bin/openclaw");
  assert.equal(looked, 0, "an operator who names an absolute path owns it");
});

test("a bare configured name is looked UP, never returned as the answer", async () => {
  // This is the exact value OpenClawCli defaults to, and the exact value that
  // produced a launchd job dying with status 78 on every start.
  const seen: string[] = [];
  const resolved = await resolveOpenClawBinaryPath("openclaw", { PATH: "/usr/local/bin" }, async (name, env) => {
    seen.push(`${name}|${String(env.PATH)}`);
    return "/usr/local/bin/openclaw";
  });
  assert.equal(resolved, "/usr/local/bin/openclaw");
  assert.deepEqual(seen, ["openclaw|/usr/local/bin"], "the caller's env is what gets searched");
});

test("no configured value looks up `openclaw` — the unset case, i.e. every real box", async () => {
  const seen: string[] = [];
  const resolved = await resolveOpenClawBinaryPath(undefined, {}, async (name) => {
    seen.push(name);
    return "/usr/bin/openclaw";
  });
  assert.equal(resolved, "/usr/bin/openclaw");
  assert.deepEqual(seen, ["openclaw"]);
});

test("a lookup that answers with a RELATIVE path resolves to nothing", async () => {
  // `command -v ./openclaw` answers relatively, and a relative ExecStart is
  // exactly as dead as a bare name.
  assert.equal(await resolveOpenClawBinaryPath(undefined, {}, async () => "./openclaw"), undefined);
  assert.equal(await resolveOpenClawBinaryPath(undefined, {}, async () => "openclaw"), undefined);
});

test("a lookup that finds nothing resolves to nothing, and one that THROWS does too", async () => {
  assert.equal(await resolveOpenClawBinaryPath(undefined, {}, async () => undefined), undefined);
  assert.equal(await resolveOpenClawBinaryPath(undefined, {}, async () => ""), undefined);
  // "openclaw is not installed" and "the lookup itself blew up" have the same
  // correct response — render no unit — so this never propagates out and
  // takes a whole provisioning run down with it.
  assert.equal(
    await resolveOpenClawBinaryPath(undefined, {}, async () => {
      throw new Error("spawn ENOENT");
    }),
    undefined,
  );
});

// ---------------------------------------------------------------------------
// resolveExpectedOpenClawSupervisorUnit: refuses to render what cannot start

test("a bare `openclaw` renders NO unit, on launchd and on systemd alike", () => {
  assert.equal(resolveExpectedOpenClawSupervisorUnit(unitOptions("openclaw", "darwin")), null);
  assert.equal(resolveExpectedOpenClawSupervisorUnit(unitOptions("openclaw", "linux")), null);
});

test("a relative, blank or absent program path renders NO unit either", () => {
  for (const candidate of [undefined, "", "   ", "./openclaw", "bin/openclaw"]) {
    assert.equal(
      resolveExpectedOpenClawSupervisorUnit(unitOptions(candidate, "darwin")),
      null,
      `darwin should refuse ${JSON.stringify(candidate)}`,
    );
    assert.equal(
      resolveExpectedOpenClawSupervisorUnit(unitOptions(candidate, "linux")),
      null,
      `linux should refuse ${JSON.stringify(candidate)}`,
    );
  }
});

test("an absolute program path renders, and it is the FIRST argument of the unit", () => {
  const plist = resolveExpectedOpenClawSupervisorUnit(unitOptions("/opt/homebrew/bin/openclaw", "darwin"));
  assert.ok(plist);
  // ProgramArguments[0] specifically — launchd resolves that one against its
  // own minimal PATH, and nothing else in the file can compensate.
  const args = [...plist!.contents.matchAll(/<string>([^<]*)<\/string>/g)].map((match) => match[1]);
  const programArgsStart = args.indexOf("/opt/homebrew/bin/openclaw");
  assert.ok(programArgsStart >= 0, plist!.contents);
  assert.deepEqual(args.slice(programArgsStart, programArgsStart + 3), [
    "/opt/homebrew/bin/openclaw",
    "--profile",
    "acme",
  ]);

  const unit = resolveExpectedOpenClawSupervisorUnit(unitOptions("/usr/local/bin/openclaw", "linux"));
  assert.ok(unit);
  assert.match(unit!.contents, /^ExecStart=\/usr\/local\/bin\/openclaw --profile acme gateway run /m);
});

test("the rendered unit never carries a bare program name anywhere in argv", () => {
  const unit = resolveExpectedOpenClawSupervisorUnit(unitOptions("/usr/local/bin/openclaw", "linux"));
  const execStart = /^ExecStart=(.*)$/m.exec(unit!.contents)?.[1] ?? "";
  assert.ok(execStart.startsWith("/"), execStart);
});

// ---------------------------------------------------------------------------
// auditAndRepairOpenClawSupervisorUnit: two DIFFERENT unsupported facts

test("an unresolvable binary reports WHY, and writes nothing", async () => {
  const writes: string[] = [];
  const outcome = await auditAndRepairOpenClawSupervisorUnit(
    {
      ...unitOptions("openclaw", "darwin"),
      readFile: async () => {
        throw new Error("ENOENT");
      },
      writeFile: async (filePath) => {
        writes.push(filePath);
      },
      mkdir: async () => undefined,
      registerJob: async () => {
        throw new Error("a job must never be registered for a unit that was refused");
      },
    },
    true,
  );
  assert.equal(outcome.supported, false);
  assert.equal(outcome.definition, null);
  assert.equal(outcome.fileState, "not_applicable");
  // Not merely "unsupported": this one is fixable, and saying only
  // "unsupported" is how a missing binary reads as a platform limitation.
  assert.equal(outcome.unsupportedReason, "binary_path_not_absolute");
  assert.deepEqual(writes, [], "a refused unit must not be written anyway");
});

test("a platform with no supervisor we manage reports a DIFFERENT reason", async () => {
  const outcome = await auditAndRepairOpenClawSupervisorUnit(
    {
      ...unitOptions("C:\\openclaw\\openclaw.exe", "win32"),
      readFile: async () => {
        throw new Error("ENOENT");
      },
      writeFile: async () => undefined,
      mkdir: async () => undefined,
    },
    true,
  );
  assert.equal(outcome.supported, false);
  assert.equal(outcome.unsupportedReason, "platform");
});

test("a resolvable binary on a supervised platform is still installed end to end", async () => {
  const files = new Map<string, string>();
  const registered: string[] = [];
  const outcome = await auditAndRepairOpenClawSupervisorUnit(
    {
      ...unitOptions("/usr/local/bin/openclaw", "darwin"),
      readFile: async (filePath) => {
        const found = files.get(filePath);
        if (found === undefined) throw new Error("ENOENT");
        return found;
      },
      writeFile: async (filePath, contents) => {
        files.set(filePath, contents);
      },
      mkdir: async () => undefined,
      registerJob: async (definition) => {
        registered.push(definition.unitPath);
      },
    },
    true,
  );
  assert.equal(outcome.supported, true);
  assert.equal(outcome.unsupportedReason, undefined);
  assert.equal(outcome.repair?.action, "wrote_new_unit");
  assert.equal(registered.length, 1);
  assert.match(
    files.get("/Users/tester/Library/LaunchAgents/ai.empyralis.openclaw.acme.plist") ?? "",
    /<string>\/usr\/local\/bin\/openclaw<\/string>/,
  );
});
