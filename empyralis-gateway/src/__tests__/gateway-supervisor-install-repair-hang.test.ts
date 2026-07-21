import test from "node:test";
import assert from "node:assert/strict";

import {
  repairGatewaySupervisorUnit,
  type GatewaySupervisorUnitDefinition,
} from "../update/gateway-supervisor-install";

// ---------------------------------------------------------------------------
// ADVERSARIAL GAP TEST -- NOW UPDATED TO ASSERT THE FIX (reliability wave
// verification): does the self-repairing supervision mechanism's repair
// path ever get PERMANENTLY STUCK -- not by throwing (gateway-doctor.test.ts
// already proves a throwing registrar is handled: "still reports the write
// as successful when registration itself throws"), but by simply never
// settling?
//
// FIXED: repairGatewaySupervisorUnit() (update/gateway-supervisor-install.ts)
// now races `opts.registerJob(opts.definition)` against an internal
// REGISTER_JOB_TIMEOUT_MS via the module's own withTimeout() helper, so a
// registerJob that never settles (a wedged launchctl/systemctl call, or any
// other hung implementation) can no longer hang this repair -- and
// therefore runGatewayDoctor's whole `gateway.doctor.run(repair:true)`
// capability invocation -- forever. On timeout it now returns the same
// "installed but could not register automatically" failure shape used for
// a throwing registerJob (requiresManualReload: true), just with a message
// naming the timeout instead of an arbitrary rejection reason. The
// production registrars (createLaunchdJobRegistrar/createSystemdJobRegistrar)
// also now pass their own execFile `timeout` option (mirroring
// health/service-inventory.ts's defaultRunCommand, service-inventory.ts:167)
// as a second, independent backstop for the common case where registerJob
// IS execFileAsync-based.
//
// This test proves the fact directly: repairGatewaySupervisorUnit() DOES
// resolve on its own within a generous window even when registerJob never
// settles on its own -- i.e. the internal timeout now guards this call. The
// module's timeout constant is a bare module-level constant (deliberately
// not injectable from a test, same posture as signal-cli-bridge.ts's
// DEFAULT_RECONNECT_POLICY), so -- like signal-cli-reconnect-exhaustion.
// test.ts -- this test clamps the global setTimeout delay for the duration
// of the repair call so the real (multi-second) timeout fires in
// milliseconds instead of making the suite slow.
// ---------------------------------------------------------------------------

function fakeDefinition(contents: string): GatewaySupervisorUnitDefinition {
  return {
    mode: "launchd",
    unitPath: "/Users/tester/Library/LaunchAgents/ai.empyralis.agent-computer.plist",
    name: "ai.empyralis.agent-computer",
    contents,
  };
}

test(
  "FIXED: repairGatewaySupervisorUnit() now times out a registerJob that never settles " +
    "(a wedged launchctl/systemctl call) and returns a clear failure instead of hanging forever",
  async () => {
    const definition = fakeDefinition("expected-contents");
    let registerJobWasCalled = false;

    const originalSetTimeout = global.setTimeout;
    // Only the delay is clamped -- callback identity/args are passed
    // through unchanged -- so the internal REGISTER_JOB_TIMEOUT_MS timer
    // fires almost immediately instead of after several real seconds.
    (global as unknown as { setTimeout: typeof setTimeout }).setTimeout = ((
      fn: (...args: unknown[]) => void,
      ms?: number,
      ...args: unknown[]
    ) => originalSetTimeout(fn, typeof ms === "number" && ms > 20 ? 2 : ms, ...args)) as typeof setTimeout;

    try {
      const outcome = await repairGatewaySupervisorUnit({
        definition,
        fileState: "missing",
        mkdir: async () => undefined,
        writeFile: async () => undefined,
        // Simulates a wedged `launchctl bootstrap`/`systemctl enable` call:
        // the real production default (execFileAsync, update/gateway-
        // supervisor-install.ts) would now itself be bounded by its own
        // execFile `timeout` option, but this directly exercises the
        // module's own internal backstop around ANY registerJob.
        registerJob: async () => {
          registerJobWasCalled = true;
          return new Promise<void>(() => {
            /* never resolves, never rejects — deliberately */
          });
        },
      });

      assert.equal(registerJobWasCalled, true, "test sanity check: the hanging registrar was actually invoked");
      assert.equal(
        outcome.action,
        "wrote_new_unit",
        "the on-disk unit write itself succeeded -- only OS registration timed out",
      );
      assert.equal(outcome.changed, true);
      assert.equal(outcome.permissionDenied, false);
      assert.equal(
        outcome.requiresManualReload,
        true,
        "FIXED: a timed-out registration now reports requiresManualReload instead of hanging indefinitely",
      );
      assert.match(
        outcome.detail,
        /timed out/i,
        "the failure detail should name the timeout, not just an arbitrary rejection reason",
      );
    } finally {
      global.setTimeout = originalSetTimeout;
    }
  },
);
