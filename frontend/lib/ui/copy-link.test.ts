/**
 * copyTextToClipboard — the one place every "Copy link" control in this
 * codebase talks to navigator.clipboard.
 *
 * Pins the exact bug found live in DocumentDetailView.tsx's first "Copy
 * link" pass: `navigator.clipboard?.writeText(...)` evaluates to `undefined`
 * (not a rejected promise) when `navigator.clipboard` itself is absent, so
 * `await`ing it resolves cleanly with nothing having happened. A caller that
 * doesn't distinguish "resolved because it worked" from "resolved because
 * there was nothing to await" claims success on a click that copied nothing
 * — CLAUDE.md: "never claim success if navigator.clipboard rejected;
 * reporting failure on a success is the worst case, not the safest." These
 * tests prove the three real outcomes (worked / rejected / not present) are
 * told apart, and stay told apart, by asserting the return value alone —
 * never a side channel.
 */
import { copyTextToClipboard } from "./copy-link";

let passed = 0;
let failed = 0;

function check(name: string, cond: boolean, detail = "") {
  if (cond) {
    passed += 1;
  } else {
    failed += 1;
    console.error(`  ✗ ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

async function main() {
  // Node's built-in `navigator` global exists but carries no `.clipboard` —
  // exactly the "insecure context / no permission" shape this module has to
  // survive. Each case installs its own clipboard shape and restores the
  // original afterward, so the three cases below can't leak into each other
  // or into any other test file's process (tsx runs each test file as its
  // own process here, but the discipline is cheap and makes that assumption
  // explicit rather than load-bearing).
  const original = (navigator as { clipboard?: unknown }).clipboard;

  try {
    // ── Case 1: navigator.clipboard is absent entirely ──
    (navigator as { clipboard?: unknown }).clipboard = undefined;
    const resultAbsent = await copyTextToClipboard("https://example.com/x");
    check(
      "absent clipboard resolves false, never true — the exact silent-no-op bug this module exists to close",
      resultAbsent === false,
    );

    // ── Case 2: clipboard present, write genuinely succeeds ──
    let written: string | null = null;
    (navigator as { clipboard?: unknown }).clipboard = {
      writeText: async (text: string) => {
        written = text;
      },
    };
    const resultOk = await copyTextToClipboard("https://example.com/y");
    check("a real successful write resolves true", resultOk === true);
    check("the real URL was actually handed to the clipboard API", written === "https://example.com/y");

    // ── Case 3: clipboard present, write REJECTS (denied permission, etc.) ──
    (navigator as { clipboard?: unknown }).clipboard = {
      writeText: async () => {
        throw new Error("NotAllowedError");
      },
    };
    const resultRejected = await copyTextToClipboard("https://example.com/z");
    check("a rejected write resolves false, not true and not a thrown exception", resultRejected === false);
  } finally {
    (navigator as { clipboard?: unknown }).clipboard = original;
  }

  // CANARY: the module under test actually exports something callable —
  // guards against a refactor silently turning this into a no-op import.
  check("canary: copyTextToClipboard is a function", typeof copyTextToClipboard === "function");

  console.log(`copy-link: ${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
}

void main();
