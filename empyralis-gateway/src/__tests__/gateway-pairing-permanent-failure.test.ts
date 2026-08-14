import test from "node:test";
import assert from "node:assert/strict";
import { promises as fs } from "fs";
import os from "os";
import path from "path";

import {
  attemptGatewayPairing,
  shouldAttemptPairing,
  GatewayPermanentStartupFailure,
  EXIT_PERMANENT_REGISTRATION_FAILURE,
} from "../index";
import { GatewayRegistrationError } from "../cloud/registration-failure";
import { GatewayStateDb } from "../state/db";
import { GatewayJournal } from "../state/journal";
import type { GatewayDeviceIdentity } from "../pairing/device-identity";
import type { GatewayRuntimeMetadata } from "../runtime/runtime-metadata";
import type { GatewayConfig } from "../config";

/**
 * End-to-end proof for the wiring inside main() that decides what happens
 * after a registration attempt fails — the exact decision that, before this
 * fix, let a box with a consumed pairing token restart forever (518 times
 * and counting) because EVERY registration failure (a first-boot DNS
 * hiccup, a transient 5xx, or a permanently-dead token) took the identical
 * exitCode=1 path.
 *
 * attemptGatewayPairing is the real function main() calls — this is not a
 * reimplementation of the decision, it IS the decision, exercised with a
 * fake `client.registerFromPairing` (so no real network call happens) and a
 * REAL GatewayStateDb/GatewayJournal backed by a throwaway tmp directory (so
 * the state-file-write half is genuinely exercised, not mocked away).
 */

const identity: GatewayDeviceIdentity = {
  gatewayId: "gateway_test",
  deviceId: "device_test",
} as GatewayDeviceIdentity;

const runtimeMetadata = {} as GatewayRuntimeMetadata;

async function withTmpStateDir<T>(fn: (stateDir: string) => Promise<T>): Promise<T> {
  const stateDir = await fs.mkdtemp(path.join(os.tmpdir(), "empyralis-gateway-test-"));
  try {
    return await fn(stateDir);
  } finally {
    await fs.rm(stateDir, { recursive: true, force: true });
  }
}

function baseConfig(overrides: Partial<GatewayConfig> = {}): Pick<GatewayConfig, "apiBaseUrl" | "pairingToken" | "gatewayToken"> {
  return {
    apiBaseUrl: "http://127.0.0.1:1/api", // deliberately unreachable — the beacon call must be best-effort
    pairingToken: "gpair_consumed",
    gatewayToken: undefined,
    ...overrides,
  };
}

test("permanent failure (retryable=false): throws GatewayPermanentStartupFailure with the systemd-recognized exit code, writes a state file, and does NOT swallow the failure silently", async () => {
  await withTmpStateDir(async (stateDir) => {
    const db = new GatewayStateDb(stateDir);
    const journal = new GatewayJournal(db);
    const permanentError = new GatewayRegistrationError(
      "Gateway registration failed with status 400: Pairing token is no longer active.",
      { status: 400, detail: "Pairing token is no longer active." },
    );
    let registerCallCount = 0;
    const fakeClient = {
      registerFromPairing: async () => {
        registerCallCount += 1;
        throw permanentError;
      },
    };
    const tokenStoreSaveCalls: unknown[] = [];

    await assert.rejects(
      attemptGatewayPairing({
        client: fakeClient,
        db,
        journal,
        config: baseConfig(),
        identity,
        runtimeMetadata,
        existingTokens: {},
        tokenStore: { save: async (v: any) => { tokenStoreSaveCalls.push(v); return v; } },
      }),
      (error: unknown) => {
        assert.ok(error instanceof GatewayPermanentStartupFailure, "must throw GatewayPermanentStartupFailure, not the raw error");
        assert.equal((error as GatewayPermanentStartupFailure).exitCode, EXIT_PERMANENT_REGISTRATION_FAILURE);
        return true;
      },
    );

    assert.equal(registerCallCount, 1, "registration is attempted exactly once — this function does not itself loop");
    assert.equal(tokenStoreSaveCalls.length, 0, "no gateway token was ever saved for a failed pairing");

    // The state-file half: a human on the box (or a future local diagnostic)
    // must be able to read WHY this box stopped without a stack trace.
    const record = await db.readJson<Record<string, unknown> | null>("registration_failure.json", null);
    assert.ok(record, "registration_failure.json must be written");
    assert.equal(record?.code, "http_4xx");
    assert.equal(record?.httpStatus, 400);
    assert.equal(record?.detail, "Pairing token is no longer active.");
    assert.equal(record?.gatewayId, "gateway_test");
  });
});

test("transient failure (retryable=true): the ORIGINAL error is rethrown unchanged, no state file, no permanent classification", async () => {
  await withTmpStateDir(async (stateDir) => {
    const db = new GatewayStateDb(stateDir);
    const journal = new GatewayJournal(db);
    const transientError = new GatewayRegistrationError(
      "Gateway registration request failed before a response was received: connect ECONNREFUSED",
      { status: undefined, detail: "connect ECONNREFUSED" },
    );
    const fakeClient = {
      registerFromPairing: async () => {
        throw transientError;
      },
    };

    await assert.rejects(
      attemptGatewayPairing({
        client: fakeClient,
        db,
        journal,
        config: baseConfig(),
        identity,
        runtimeMetadata,
        existingTokens: {},
        tokenStore: { save: async (v: any) => v },
      }),
      (error: unknown) => {
        // Must be the SAME error object, not wrapped — this is what keeps
        // main()'s existing exitCode=1 -> systemd Restart=always path
        // completely unchanged for every retryable failure.
        assert.equal(error, transientError);
        assert.ok(!(error instanceof GatewayPermanentStartupFailure));
        return true;
      },
    );

    const record = await db.readJson<Record<string, unknown> | null>("registration_failure.json", null);
    assert.equal(record, null, "a retryable failure must never write the permanent-failure state file");
  });
});

test("an unclassified error (not a GatewayRegistrationError at all) is also rethrown unchanged", async () => {
  await withTmpStateDir(async (stateDir) => {
    const db = new GatewayStateDb(stateDir);
    const journal = new GatewayJournal(db);
    const weirdError = new TypeError("something else broke");
    const fakeClient = {
      registerFromPairing: async () => {
        throw weirdError;
      },
    };

    await assert.rejects(
      attemptGatewayPairing({
        client: fakeClient,
        db,
        journal,
        config: baseConfig(),
        identity,
        runtimeMetadata,
        existingTokens: {},
        tokenStore: { save: async (v: any) => v },
      }),
      (error: unknown) => {
        assert.equal(error, weirdError);
        return true;
      },
    );
  });
});

test("already paired (gatewayToken already stored): does not call registerFromPairing at all, and succeeds", async () => {
  await withTmpStateDir(async (stateDir) => {
    const db = new GatewayStateDb(stateDir);
    const journal = new GatewayJournal(db);
    let registerCallCount = 0;
    const fakeClient = {
      registerFromPairing: async () => {
        registerCallCount += 1;
        throw new Error("should never be called");
      },
    };

    await attemptGatewayPairing({
      client: fakeClient,
      db,
      journal,
      // Mirrors pairing-skip-on-restart.test.ts's crash-loop scenario: the
      // installer never clears the env-supplied pairing token, so it is
      // still "set" here even though a gatewayToken already exists.
      config: baseConfig({ pairingToken: "gpair_stale_already_consumed" }),
      identity,
      runtimeMetadata,
      existingTokens: { gatewayToken: "ggt_persisted" },
      tokenStore: { save: async (v: any) => v },
    });

    assert.equal(registerCallCount, 0, "shouldAttemptPairing must still prevent re-registration once paired");
    assert.equal(
      shouldAttemptPairing("gpair_stale_already_consumed", "ggt_persisted"),
      false,
      "sanity check on the underlying rule this test exercises",
    );
  });
});
