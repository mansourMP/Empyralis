/**
 * The OpenClaw version pin, and the loud failure when the installed CLI is
 * not it (CHANNEL-ADOPTION-PLAN.md step 4).
 *
 * WHY A PIN AT ALL — three things in steps 1-3 are TRANSCRIPTIONS, not
 * negotiated contracts, and every one of them silently becomes wrong if the
 * bundle underneath changes:
 *
 *   1. openclaw-bridge-plugin/types/openclaw-plugin-sdk.d.ts — ambient types
 *      hand-written from their shipped `dist/plugin-sdk`. A renamed hook
 *      argument still compiles here and arrives `undefined` there.
 *   2. openclaw-bridge-plugin/src/cancel-predicate.ts — the two literal
 *      markers ("FailoverError", "No API key found for provider") that
 *      identify OpenClaw's own credential-less failure reply. Reword that
 *      sentence upstream and the deliberate "brain off" reply stops being
 *      suppressed: the customer's contact gets an internal error message.
 *   3. ../outbound-payload.ts — `message.action`'s param names, its
 *      `operator.write` descriptor scope, and the closed `ErrorCodes` set.
 *
 * None of the three fails at build time, and none of them logs anything when
 * it drifts. That is exactly the MAN-306 shape CLAUDE.md documents (a Rust
 * kernel binary silently enforcing pre-fix policy for weeks because nothing
 * rebuilt it): a dependency that is not source, cannot be `grep`ed for
 * callers, and degrades silently. MAN-306's fix was a boot-time staleness
 * gate that REFUSES to run on a mismatch; this is the same gate for the same
 * class of risk, applied to an out-of-process, separately-versioned
 * dependency instead of a compiled artifact.
 *
 * So: the pin is exact, the check runs before anything else in provisioning,
 * and a mismatch is a hard failure with the three consequences named — never
 * a warning, never "probably fine, minor version".
 */

/** The one version steps 1-3 were read against and verified live on. */
export const OPENCLAW_PINNED_VERSION = "2026.6.10";

/** What `npm i -g` must install. Exact, never a range. */
export const OPENCLAW_PINNED_PACKAGE_SPEC = `openclaw@${OPENCLAW_PINNED_VERSION}`;

/** Pulls the version out of `openclaw --version` output. Their CLI prints a
 *  bare semver-ish line; `openclaw --help`'s banner prints
 *  "OpenClaw 2026.6.10 (aa69b12)". Both shapes are accepted, and anything
 *  else returns undefined rather than a guess. */
export function parseOpenClawVersion(output: string): string | undefined {
  const text = String(output ?? "");
  const match = text.match(/\b(\d{4}\.\d{1,2}\.\d{1,3})\b/);
  return match ? match[1] : undefined;
}

export interface OpenClawVersionCheck {
  ok: boolean;
  observed?: string;
  expected: string;
  /** Stable machine code. Never match on the sentence. */
  code?: "openclaw_not_installed" | "openclaw_version_unreadable" | "openclaw_version_mismatch";
  detail?: string;
}

const MISMATCH_CONSEQUENCES = [
  "the bridge plugin's ambient SDK types (openclaw-bridge-plugin/types/openclaw-plugin-sdk.d.ts)",
  "the deliberate no-credentials reply suppression (openclaw-bridge-plugin/src/cancel-predicate.ts)",
  "the message.action request/error shape (src/openclaw/outbound-payload.ts)",
].join(", ");

/**
 * Decides whether an observed CLI version may be provisioned against.
 * Never throws — the provisioner turns this into a refusal with a code, so
 * the reason reaches the cloud instead of a stack trace in a log file.
 */
export function checkOpenClawVersion(rawOutput: string | undefined): OpenClawVersionCheck {
  const expected = OPENCLAW_PINNED_VERSION;
  if (rawOutput === undefined) {
    return {
      ok: false,
      expected,
      code: "openclaw_not_installed",
      detail:
        `OpenClaw is not installed on this computer. Install exactly ${OPENCLAW_PINNED_PACKAGE_SPEC} — ` +
        "any other version is refused, because " + MISMATCH_CONSEQUENCES + " are transcribed from that build.",
    };
  }
  const observed = parseOpenClawVersion(rawOutput);
  if (!observed) {
    return {
      ok: false,
      expected,
      code: "openclaw_version_unreadable",
      detail:
        "Could not read a version from the installed OpenClaw CLI. Provisioning refuses to continue rather than " +
        "assume it is the pinned build.",
    };
  }
  if (observed !== expected) {
    return {
      ok: false,
      observed,
      expected,
      code: "openclaw_version_mismatch",
      detail:
        `Installed OpenClaw is ${observed}; Empyralis is pinned to ${expected}. Refusing to provision: ` +
        MISMATCH_CONSEQUENCES +
        " are all transcribed from " +
        `${expected}'s shipped bundle and fail SILENTLY (not at build time, not in a log) when it changes. ` +
        `Install ${OPENCLAW_PINNED_PACKAGE_SPEC}, or re-verify all three against the new build and move the pin.`,
    };
  }
  return { ok: true, observed, expected };
}
