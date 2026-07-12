import test from "node:test";
import assert from "node:assert/strict";

import { HeartbeatLoop } from "../cloud/heartbeat";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

test("a sendHeartbeat call that settles after losing the race does not crash the process as an unhandled rejection", async () => {
  // Regression test for a real production crash: HeartbeatLoop races
  // sendHeartbeat() against its own timeout via Promise.race. When the
  // timeout branch won (e.g. the socket was silently dead), the original
  // sendHeartbeat() promise kept running unobserved, and when IT later
  // rejected (e.g. "Cannot read properties of null (reading 'send')" from
  // ws-client.ts once the connection actually closed), nothing was still
  // listening to it — Node flagged it an unhandled rejection and killed
  // the whole gateway process, with no supervisor to bring it back.
  const unhandled: unknown[] = [];
  const onUnhandledRejection = (reason: unknown) => {
    unhandled.push(reason);
  };
  process.on("unhandledRejection", onUnhandledRejection);

  try {
    const loop = new HeartbeatLoop();
    const failures: Error[] = [];

    // Which of {loop timeout, sendHeartbeat's own rejection} settles the
    // race first is timing-dependent (this machine has shown real timer
    // jitter under background throttling — see the 60s-vs-hour stall this
    // test itself caused before that bug was fixed). The property under
    // test doesn't care which one wins: exactly one failure gets reported
    // either way, and the LOSER — whichever promise it turns out to be —
    // must never surface as an unhandled rejection. So the gap between the
    // two delays is kept wide (30ms vs 400ms) for margin, and the
    // assertions below don't depend on which message arrives first.
    await new Promise<void>((resolveTest) => {
      loop.start({
        // HeartbeatLoop.start() schedules its FIRST tick via
        // setTimeout(tick, intervalMs) too, not immediately — so this must
        // stay small or the test just waits out a real intervalMs-long
        // timer for no reason. A second tick is prevented below by calling
        // loop.stop() from inside onHeartbeatFailure (synchronously, before
        // tick()'s own finally block re-arms the next setTimeout), not by
        // making the interval implausibly long.
        intervalMs: 10,
        timeoutMs: 30,
        maxConsecutiveFailures: 3,
        sendHeartbeat: async () => {
          await sleep(400);
          throw new Error("Cannot read properties of null (reading 'send')");
        },
        onHeartbeatFailure: (error) => {
          failures.push(error);
          loop.stop();
          resolveTest();
        },
      });
    });

    assert.equal(failures.length, 1, "exactly one failure should be reported for this tick");

    // Give the loser promise (whichever one it was) time to settle, then
    // confirm it never became an unhandled rejection.
    await sleep(500);
    assert.deepEqual(unhandled, [], "the orphaned promise's rejection must not reach the process as unhandled");

    loop.stop();
  } finally {
    process.off("unhandledRejection", onUnhandledRejection);
  }
});
