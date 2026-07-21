import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { PassThrough } from "node:stream";
import type { spawn as nodeSpawn } from "node:child_process";

import { ClaudeCliPrewarmPool, type ClaudePrewarmParams } from "../llm/claude-cli-prewarm";

// ---------------------------------------------------------------------------
// ADVERSARIAL GAP TEST -- NOW UPDATED TO ASSERT THE FIX (resource-safety leg
// of the reliability wave audit).
//
// claude-cli-prewarm.test.ts thoroughly proves conversation ISOLATION
// (never reusing a process across turns) but had no test for what happens
// when a claimed process just... hangs: never emits a `result` line, never
// exits.
//
// FIXED: generate() (llm/claude-cli-prewarm.ts) races `outcome` (resolves/
// rejects off the child's stdout) against `deadline` (a bare setTimeout that
// only rejects the promise). Its `finally` block now checks whether
// `claimed.turn` is still set once the race settles -- true exactly when
// neither a `result` line (handleLine's own retireAfterTurn path) nor the
// child's own "exit"/"error" handler ever cleared it, i.e. `deadline` won
// the race (a genuine timeout) while the child is presumably still alive.
// In that case it now nulls `claimed.turn` and calls `this.retire(claimed)`
// itself -- the same teardown (lines.close() + child.kill("SIGTERM")) every
// other terminal path already goes through. Before this, a genuinely hung
// `claude` child process (spawned, no output, never exits) was orphaned
// forever: the OS process kept running for the gateway's entire lifetime,
// `this.liveCount` never decremented (the "exit" handler that would
// decrement it never fires for a truly hung process), and this repeated --
// one more leaked process -- every single time a turn hit this pool's
// timeout.
// ---------------------------------------------------------------------------

interface FakeChild {
  killed: (NodeJS.Signals | undefined)[];
}

interface CreatedProcess {
  fake: FakeChild;
  stdout: PassThrough;
  handle: { kill: (signal?: NodeJS.Signals) => boolean };
}

/** Same spirit as claude-cli-prewarm.test.ts's makeSpawnFake(), but this
 *  fake's `kill()` does NOT auto-emit "exit" -- it just records the call.
 *  That's the whole point here: production code is never expected to call
 *  it for a hung, claimed entry, and this lets the test tell the
 *  difference between "kill was never attempted" (the gap) and "kill was
 *  attempted but the process ignored the signal" (a different, unrelated
 *  problem). A real hung `claude` process would behave the same way as
 *  this fake: no output, no exit, ever -- that's what "hung" means. */
function makeHangingSpawnFake(): { spawnImpl: typeof nodeSpawn; created: CreatedProcess[] } {
  const created: CreatedProcess[] = [];
  const spawnImpl = ((_command: string, _args: string[]) => {
    const stdout = new PassThrough();
    const stderr = new PassThrough();
    const proc = new EventEmitter();
    const killed: (NodeJS.Signals | undefined)[] = [];
    const handle = {
      stdout,
      stderr,
      stdin: { write: (_chunk: string) => true },
      on: (event: string, listener: (...unknownArgs: unknown[]) => void) => {
        proc.on(event, listener);
        return handle;
      },
      // Deliberately does NOT emit "exit" -- simulates a genuinely hung
      // process that ignores SIGTERM (or, in the gap this test proves, a
      // process nobody ever sent SIGTERM to in the first place).
      kill: (signal?: NodeJS.Signals) => {
        killed.push(signal);
        return true;
      },
    };
    created.push({ fake: { killed }, stdout, handle });
    return handle as unknown as ReturnType<typeof nodeSpawn>;
  }) as typeof nodeSpawn;
  return { spawnImpl, created };
}

function baseParams(overrides: Partial<ClaudePrewarmParams> = {}): ClaudePrewarmParams {
  return { prompt: "hi", model: "opus", systemPrompt: "you are terse", timeoutMs: 30, ...overrides };
}

test(
  "FIXED: a claimed process that hangs past its deadline is killed by generate()'s own finally block",
  async () => {
    const { spawnImpl, created } = makeHangingSpawnFake();
    const pool = new ClaudeCliPrewarmPool(process.env, spawnImpl);

    // Never write anything to stdout, never emit "exit" -- a genuinely hung
    // process. timeoutMs is tiny (30ms) so the deadline wins quickly.
    await assert.rejects(
      pool.generate(baseParams()),
      /timed out/i,
      "generate() must still reject the CALLER's promise on timeout",
    );

    // generate()'s finally block also fires refillSpare() regardless of
    // outcome (claude-cli-prewarm.ts), which speculatively spawns a SECOND
    // (unclaimed, background) process for the next caller -- so two
    // processes exist after one timed-out turn: created[0] is the one that
    // actually served (and hung on) this turn; created[1] is the
    // speculative refill sitting in `spare`, unrelated to the fix being
    // tested here.
    assert.equal(created.length, 2, "one claimed (hung) process plus one speculative background refill");
    assert.deepEqual(
      created[0].fake.killed,
      ["SIGTERM"],
      "FIXED: generate()'s finally block now calls retire(claimed) on a timeout, which sends SIGTERM to the " +
        "hung child instead of abandoning it (llm/claude-cli-prewarm.ts)",
    );

    // Compounding check, now for RECOVERY: run multiple turns that all
    // hang and confirm EVERY one gets killed, not just the first -- i.e.
    // this is the steady-state behavior of the fix, not a one-off. All
    // turns share the same pool key (baseParams only varies `prompt`,
    // which isn't part of poolKey), so each subsequent turn CLAIMS the
    // previous turn's speculative background refill (rather than spawning
    // a brand-new claimed entry) and then spawns exactly one new spare via
    // refillSpare -- i.e. every created entry except the very last
    // (trailing, still-spare, never-claimed) one gets claimed and hung at
    // some point, and must therefore be killed.
    const before = created.length;
    for (let i = 0; i < 3; i += 1) {
      await assert.rejects(pool.generate(baseParams({ prompt: `turn ${i}` })));
    }
    assert.equal(created.length, before + 3, "three more turns hit the same pool, three more processes spawned");

    const claimedAndHungEntries = created.slice(0, created.length - 1);
    for (const entry of claimedAndHungEntries) {
      assert.deepEqual(
        entry.fake.killed,
        ["SIGTERM"],
        "FIXED (steady-state): every claimed process that hung past its deadline was killed, not just the first",
      );
    }
    const trailingSpare = created[created.length - 1];
    assert.deepEqual(
      trailingSpare.fake.killed,
      [],
      "the final speculative background refill was never claimed/timed out, so it correctly was not killed here " +
        "(it's reaped later by scheduleIdleReap instead)",
    );
  },
);
