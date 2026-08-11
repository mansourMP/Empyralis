/**
 * What the QR half of the WhatsApp panel is doing, as ONE value every element
 * on it reads.
 *
 * THE BUG THIS EXISTS FOR
 * -----------------------
 * The panel auto-starts the QR request when it opens in a resting state
 * (without that, a runtime that has just been disconnected sits idle forever
 * and a QR only ever appeared because the Gateway process happened to reconnect
 * on its own boot). That auto-start is correct and stays. What was wrong is
 * that no element agreed on it:
 *
 *     /setup returns 200 as soon as the box ACCEPTS the request; the QR itself
 *     arrives later, on the 2s status poll. So `busy` was false again within
 *     ~100ms while `qr_code` was still null, and the panel rendered:
 *
 *       ┌─────────────┐
 *       │      ◐      │   the QR frame's spinner — "something is happening"
 *       └─────────────┘
 *       [ Generate QR code ]   the start control  — "nothing has happened yet"
 *
 *     Founder, on the live screen: "I have not clicked this button that says
 *     generate QR code. Yet the QR code is already being generated right there."
 *
 * The frame's spinner was also unconditional on "no image yet", so it spun in
 * states where genuinely nothing was running at all.
 *
 * THE STATES
 * ----------
 *   idle      nothing has been asked for. Frame EMPTY, start control shown.
 *   starting  our /setup call is in flight. Spinner, NO start control.
 *   waiting   the box accepted it; the code has not come back yet, or has and
 *             is being drawn. Spinner, NO start control — bounded by a caller-
 *             owned deadline so it cannot become a spinner with no way out.
 *   ready     a QR is on screen. No spinner, no start control.
 *   failed    the attempt failed, or the wait ran out. NO spinner, a control
 *             that says it is retrying, and a line saying what happened.
 *
 * The invariant, which is the whole fix: a spinner and a "start this" control
 * are never on screen together, in either direction. It is not stated as a rule
 * someone has to remember — `resolveQrPanelView` is the only thing that decides
 * either of them, and channel-qr-phase.test-side assertions in
 * openclaw-channel-copy.test.ts drive EVERY combination of its inputs and
 * refute the pairing. A comment cannot catch the next branch.
 *
 * WHY THIS IS A MODULE AND NOT A FEW TERNARIES IN THE COMPONENT
 * ------------------------------------------------------------
 * Same reason channel-doors.ts and openclaw-channel-copy.ts are: the strings
 * below reach the DOM verbatim and the rule below is the product law ("no dead
 * controls") in executable form, and neither can be tested inside a React
 * component that imports a stylesheet — the `tsx` runner cannot load one. Pure
 * data + pure function, so the test drives the REAL thing rather than a
 * transcription of it.
 */

export type QrPhase = "idle" | "starting" | "waiting" | "ready" | "failed";

/** Everything the panel knows, reduced to the six facts the phase turns on.
 *  Deliberately booleans rather than the raw values: the component holds a data
 *  URL, an Error and two timestamps, and none of those distinctions change what
 *  is drawn — flattening them here is what makes the input space small enough
 *  to enumerate exhaustively in a test. */
export type QrPanelInputs = {
  /** A code has been rendered to an image and can be put on screen. */
  qrImageReady: boolean;
  /** Our own /setup call is in flight, or is about to be fired this render by
   *  the auto-start. Both are "we are asking"; the customer cannot tell them
   *  apart and should not have to. */
  requestInFlight: boolean;
  /** A failure we can put in words — a rejected request, or a disconnect
   *  reason the box reported. */
  errorText: string | null;
  /** The box has handed back a code; it may not be drawn yet. */
  codeIssued: boolean;
  /** The box ACCEPTED the request. This is the fact an in-flight flag cannot
   *  carry: the fetch resolves in ~100ms and everything interesting happens
   *  after that. */
  accepted: boolean;
  /** The bounded wait ran out. A wait with no end is indistinguishable from a
   *  hang, so the caller owns a deadline and reports it here. */
  waitExpired: boolean;
};

export type QrPanelView = {
  phase: QrPhase;
  /** The line above the frame. It says what is true right now — the scan
   *  instruction is only correct once there is something to scan. */
  hint: string;
  showQrImage: boolean;
  showSpinner: boolean;
  /** Non-null exactly when the attempt is genuinely over and failed. */
  failureText: string | null;
  /** The start/retry control's label, or null for NO CONTROL AT ALL. Not a
   *  disabled button: a control that cannot be used is not rendered (product
   *  law), and a disabled "Generate QR code" under a spinner would still be
   *  telling the customer that starting it is their job. */
  startControlLabel: string | null;
};

/** The one sentence shown when the box accepted the request and then produced
 *  nothing. It is a failure with no error of its own, so it carries its own
 *  words rather than leaving the customer with a stopped spinner. */
export const QR_NO_CODE_TEXT = "No code came back from this agent's computer.";

/** A code arrived and could not be DRAWN. Its own sentence rather than a
 *  generic setup failure, because the box did its part and retrying is still
 *  the right move — and because routing it through the raw-reason humaniser
 *  would print the internal reason token on a customer's screen. */
export const QR_RENDER_FAILED_TEXT = "That code couldn't be displayed. Try again.";

const HINT_READY = "Open WhatsApp on your phone → Linked Devices → Link a device, then scan:";
const HINT_RESTING = "Open WhatsApp on your phone → Linked Devices → Link a device, then scan the code.";
const HINT_STARTING = "Asking this agent's computer for a code…";
const HINT_WAITING = "Waiting for the code…";

/** ORDER IS THE RULE. A rendered code beats everything (it is the thing the
 *  customer came for); an in-flight request beats a stale error, so a retry
 *  does not paint the previous failure over its own progress; a code that has
 *  arrived but is still being drawn is real work, so it spins honestly; and
 *  "idle" is reached only when genuinely nothing is running — which, thanks to
 *  the auto-start, is rare and brief. */
function resolvePhase(inputs: QrPanelInputs): QrPhase {
  if (inputs.qrImageReady) return "ready";
  if (inputs.requestInFlight) return "starting";
  if (inputs.errorText) return "failed";
  if (inputs.codeIssued) return "waiting";
  if (inputs.accepted) return inputs.waitExpired ? "failed" : "waiting";
  return "idle";
}

export function resolveQrPanelView(inputs: QrPanelInputs): QrPanelView {
  const phase = resolvePhase(inputs);
  const working = phase === "starting" || phase === "waiting";
  return {
    phase,
    hint:
      phase === "ready"
        ? HINT_READY
        : phase === "starting"
          ? HINT_STARTING
          : phase === "waiting"
            ? HINT_WAITING
            : HINT_RESTING,
    showQrImage: phase === "ready",
    showSpinner: working,
    failureText: phase === "failed" ? inputs.errorText || QR_NO_CODE_TEXT : null,
    // The single place the invariant is enforced: a control exists only in the
    // two phases where nothing is running, and never alongside `showSpinner`.
    startControlLabel: phase === "failed" ? "Try again" : phase === "idle" ? "Generate QR code" : null,
  };
}
