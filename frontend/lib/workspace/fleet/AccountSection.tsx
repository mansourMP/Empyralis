"use client";

import { useCallback, useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";

import {
  type EmailVerificationStatus,
  AuthNetworkError,
  getEmailVerificationStatus,
  resendVerificationEmail,
} from "@/lib/auth/auth-client";
import { useAccountShell } from "@/lib/shell/account-shell-context";
import { useRevealedEmail } from "@/lib/shell/use-revealed-email";
import { type GlobalTheme } from "@/lib/shell/account-shell-store";
import { MemberAvatar } from "@/lib/workspace/fleet/MemberAvatarStack";

/**
 * Account — personal settings: the ones that belong to the PERSON, not to the
 * workspace. Nothing workspace-scoped may land here; that is Workspace or
 * Connections.
 *
 * It was an honest empty state until 2026-08-29 ("Nothing here yet"), and
 * CLAUDE.md carried the open question of whether it should get the light/dark
 * preference or lose its row and redirect entirely. The founder answered it:
 * "there is nothing in this account section, I think there should be
 * something, it feels like, because I have seen some things in other
 * software." So it gets the two personal things this product actually has
 * today, both real and both already wired end to end — nothing invented, and
 * no control that cannot do its job.
 *
 * NO <h2>Account</h2>, deliberately: SettingsShell renders no heading block
 * because the breadcrumb's current crumb IS this page's <h1>, and printing
 * the page's own name again directly under it is the same duplication that
 * was just removed from KeyboardShortcutsSection. The group titles below
 * ("You", "Appearance") are real subsection headings, which is what
 * .fleet-detail-section-title is for on a page that has several.
 */

// ── Appearance ────────────────────────────────────────────────────────────

/**
 * SYSTEM WAS ALREADY IMPLEMENTED AND UNREACHABLE, which is what makes this a
 * capability restored rather than a preference re-skinned.
 *
 * AppThemeProvider has always handled all three values — 'system' installs a
 * real prefers-color-scheme matchMedia listener and follows the device live —
 * and account-shell-storage already persists 'system' by name. The only
 * writer in the product was the rail's account-popover row, which is a BINARY
 * toggle ("Switch to light" / "Switch to dark"): it can only ever write
 * 'light' or 'dark', so the moment anyone touched it, 'system' became
 * unreachable for the rest of that account's life on that browser. This is a
 * three-state preference that had a two-state control.
 *
 * The popover toggle STAYS. It is the one-press "it's dark in here" control
 * and it is right where a person reaches for it; this is the full picker,
 * where you go to say "follow my device". Two controls over one value, not
 * two sources of truth — both write the same useAccountShell action.
 */
const THEME_OPTIONS: { value: GlobalTheme; label: string; Icon: typeof Sun }[] = [
  { value: "system", label: "System", Icon: Monitor },
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
];

function AppearanceGroup() {
  const { state, actions } = useAccountShell();
  const preference = state.globalTheme;

  return (
    <section>
      <h2 className="fleet-detail-section-title">Appearance</h2>
      {/* Six words, not a paragraph: the preference lives in this browser's
          own account-shell snapshot (account-shell-storage.ts), never on the
          server, so it genuinely does not follow the person to another
          machine. Saying nothing would let someone open Empyralis on a second
          computer, find it light again, and conclude the setting is broken —
          which is the same "two different facts, one signal" trap this
          codebase keeps paying for, just a quiet one. */}
      <p className="fleet-subtitle" style={{ marginTop: 0 }}>Saved on this browser.</p>
      <div className="fleet-theme-options" role="radiogroup" aria-label="Appearance">
        {THEME_OPTIONS.map(({ value, label, Icon }) => {
          const selected = preference === value;
          return (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={selected}
              // Explicit, not derived from contents: the label sits beside a
              // lucide <svg> inside the button, and the accessibility tree
              // read these back as bare "radio" with no name at all. Same
              // unnamed-icon-button shape PrimaryRail already carries a note
              // about (MAN-145 item 5), fixed the same way.
              aria-label={label}
              className={`fleet-wizard-option${selected ? " is-selected" : ""}`}
              onClick={() => actions.setGlobalTheme(value)}
            >
              <span className="fleet-wizard-option-label">
                <Icon size={14} strokeWidth={1.75} />
                {label}
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

// ── You ───────────────────────────────────────────────────────────────────

/**
 * THE FOUR VERIFICATION ANSWERS ARE NOT TWO. The backend's own vocabulary is
 * `verified` / `pending` / `none`, and `none` means an account that predates
 * the feature — email_verification_service treats it as verified on purpose
 * (routes_auth's own status route reports email_verified for it), so
 * rendering "not verified" there would retroactively accuse every older
 * account. And "the status call hasn't answered / failed" is a fourth thing
 * again: it is not evidence of anything, so it says nothing at all rather
 * than guessing in either direction.
 *
 *   verified   a quiet badge — a fact people come here to check
 *   pending    the real one: invite mail is WITHHELD from an unverified
 *              sender (workspace_invite_email_service's send gate), so this
 *              is the page's one action
 *   none       nothing rendered
 *   unknown    nothing rendered
 */
type VerificationView = "verified" | "pending" | "silent";

function verificationView(status: EmailVerificationStatus | null): VerificationView {
  if (!status) return "silent";
  if (status.status === "verified") return "verified";
  if (status.status === "pending") return "pending";
  return "silent";
}

function YouGroup() {
  const { state } = useAccountShell();
  const account = state.account;
  // account.email is XOR-obfuscated from the moment it is parsed server-side
  // (ssr-safe-email.ts) — this hook is the one sanctioned decode, and it
  // resolves to null until a post-hydration effect runs, so every line below
  // needs a non-email fallback for that window.
  const email = useRevealedEmail(account?.email);
  const displayName = account?.displayName || "";
  const name = displayName || email || "Your account";

  const [status, setStatus] = useState<EmailVerificationStatus | null>(null);
  const [resending, setResending] = useState(false);
  const [resendOutcome, setResendOutcome] = useState<
    { kind: "sent" | "failed" | "unknown"; message: string } | null
  >(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await getEmailVerificationStatus();
        if (!cancelled) setStatus(next);
      } catch {
        // Deliberately silent: a failed status read is not evidence the
        // address is unverified, and this page has nothing to say about it.
        if (!cancelled) setStatus(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleResend = useCallback(async () => {
    setResending(true);
    setResendOutcome(null);
    try {
      await resendVerificationEmail();
      setResendOutcome({ kind: "sent", message: `Sent to ${email || "your address"}. Check your inbox.` });
    } catch (e) {
      if (e instanceof AuthNetworkError) {
        // No response ever arrived, so the send may well have happened —
        // "failed" would be a claim we cannot make.
        setResendOutcome({
          kind: "unknown",
          message: "Couldn't confirm that was sent. It's safe to try again.",
        });
      } else {
        setResendOutcome({
          kind: "failed",
          message: e instanceof Error ? e.message : "Couldn't send the email.",
        });
      }
    } finally {
      setResending(false);
    }
  }, [email]);

  const view = verificationView(status);

  return (
    <section>
      <h2 className="fleet-detail-section-title">You</h2>
      <div className="fleet-list">
        {/* Same row shape as a Workspace > Members row (MembersSection.tsx) —
            avatar, name, address — because it is the same kind of fact about
            the same kind of thing. */}
        <div className="fleet-list-row" style={{ cursor: "default" }}>
          <MemberAvatar name={name} size="sm" />
          <span className="fleet-list-row-main">
            <span className="fleet-list-row-title">{name}</span>
            {email && email !== name ? <span className="fleet-list-row-desc">{email}</span> : null}
          </span>
          {view === "verified" ? (
            <span className="fleet-badge" style={{ marginLeft: 0 }}>Email verified</span>
          ) : null}
        </div>
      </div>

      {view === "pending" ? (
        <div className="fleet-account-verify">
          <p className="fleet-list-row-desc" style={{ margin: 0, color: "var(--warning-text)" }}>
            Email not verified — invites you send won&apos;t be delivered until it is.
          </p>
          <button
            type="button"
            className="fleet-btn fleet-btn--accent-fill"
            onClick={() => { void handleResend(); }}
            disabled={resending}
          >
            {resending ? "Sending…" : "Resend email"}
          </button>
          {resendOutcome ? (
            <p
              className="fleet-list-row-desc"
              style={{
                margin: 0,
                width: "100%",
                color: resendOutcome.kind === "failed" ? "var(--offline-text)" : "var(--text-secondary)",
              }}
              role={resendOutcome.kind === "sent" ? "status" : "alert"}
            >
              {resendOutcome.message}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export function AccountSection() {
  return (
    <div className="fleet-account-sections">
      <YouGroup />
      <AppearanceGroup />
    </div>
  );
}
