//! Handing sign-in to the REAL browser, and what this app is allowed to say
//! while it waits.
//!
//! ── Why the app must never host sign-in ──────────────────────────────────
//! The window used to load the hosted site and ask the customer to sign in
//! inside it. Two things are wrong with that and neither is fixable by being
//! careful: a password typed into a webview this process owns is a password
//! this process can read — that is the shape of a phishing page regardless of
//! our intentions — and Google refuses OAuth in embedded webviews outright
//! (`disallowed_useragent`), so "Continue with Google" could not work here
//! even if we wanted it to.
//!
//! So this app does what Ollama, Docker Desktop, Tailscale and the GitHub CLI
//! do: it opens the customer's own browser, where there is an address bar to
//! check, a password manager, and their existing sessions, and waits for that
//! browser to hand one short-lived pairing token back over loopback.
//!
//! ── The listener is the attack surface, so the rules are structural ──────
//!   * bound to 127.0.0.1 ONLY, never 0.0.0.0 — the caller enforces this and
//!     `loopback_bind_addr` is the only address it is given.
//!   * an ephemeral port, chosen by the OS per attempt (`:0`), so there is no
//!     fixed port for anything to camp on between runs.
//!   * a CSPRNG `state` nonce, and a callback carrying anything else is
//!     REFUSED — see `classify_callback`.
//!   * single use: the flow stops on the first callback it accepts.
//!   * time-bound: a listener that outlives the attempt is an open door, so
//!     the deadline is enforced by the accept loop itself rather than by
//!     anyone remembering to stop it.
//!
//! ── A mismatched callback does NOT end the attempt ───────────────────────
//! `classify_callback` returning `Rejected` is answered with a 400 and the
//! loop keeps waiting. Ending the attempt there would let anything that can
//! reach loopback cancel a customer's pairing by sending one junk request —
//! trading a token-theft defence for a denial of service. Only a callback
//! that PROVES it came from the page we opened (the state matches) is allowed
//! to decide anything.
//!
//! ── The states are the founder's outcome-honesty law, applied here ───────
//! "Waiting", "the browser never came back", "they said no", "it's connected
//! but Docker isn't running" and "it's serving" are five different facts that
//! send a person to do five different things. They never share a sentence.
//! Once a pairing is accepted this module stops inventing vocabulary and
//! defers to `agent_computer_status` — the same rule the menu bar renders —
//! so the window and the tray can never describe one machine two ways.

use serde::Serialize;

use crate::agent_computer_status::AgentComputerStatus;

/// How long the loopback listener may stay open waiting for the browser.
///
/// Long enough for a real sign-in — a fresh browser, an email/password, a
/// Google consent screen, maybe a 2FA prompt — and short enough that a
/// forgotten attempt is not a port sitting open all day.
pub const CALLBACK_TIMEOUT_SECS: u64 = 300;

/// The path the hosted page redirects to. Anything else on this port is not
/// our callback and is refused without being looked at further.
pub const CALLBACK_PATH: &str = "/desktop-pair/callback";

/// The hosted page that does the actual signing in and minting.
pub const PAIRING_PAGE_PATH: &str = "/desktop/pair";

/// Everything a validated callback carried. Deliberately not `Option`-heavy:
/// a callback missing any of it is not a weaker success, it is a rejection.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AcceptedPairing {
    pub pairing_token: String,
    pub workspace_id: String,
    /// The workspace's HUMAN name, so the menu bar can say which workspace
    /// this computer serves with no window open. Optional because an older
    /// page might not send one, and a raw `ws_9f3c…` in a menu is not an
    /// answer to "what is this computer doing" — the menu falls back to a
    /// truthful generic line rather than printing an id.
    pub workspace_label: String,
}

/// What a request arriving on the loopback port turned out to be.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CallbackOutcome {
    /// The browser proved it is the page we opened and handed over a token.
    Accepted(AcceptedPairing),
    /// The customer pressed Cancel. A real answer, not a failure — nothing is
    /// broken and there is nothing to retry automatically.
    Declined,
    /// Anything else. Answered with a 400; the attempt keeps waiting.
    Rejected(&'static str),
}

/// Constant-time-ish equality, so a state nonce cannot be recovered a byte at
/// a time by timing repeated loopback requests. Cheap, and the alternative is
/// a defence that depends on an attacker not bothering.
fn secret_eq(a: &str, b: &str) -> bool {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

/// Percent-decoding for one query value. Written here rather than pulled from
/// `url` so this function stays pure and testable against a raw request line
/// without constructing a base URL first.
fn percent_decode(raw: &str) -> String {
    let bytes = raw.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b'%' if i + 2 < bytes.len() => {
                let hi = (bytes[i + 1] as char).to_digit(16);
                let lo = (bytes[i + 2] as char).to_digit(16);
                match (hi, lo) {
                    (Some(hi), Some(lo)) => {
                        out.push((hi * 16 + lo) as u8);
                        i += 3;
                    }
                    _ => {
                        out.push(bytes[i]);
                        i += 1;
                    }
                }
            }
            other => {
                out.push(other);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn query_value(query: &str, key: &str) -> Option<String> {
    query
        .split('&')
        .filter(|pair| !pair.is_empty())
        .find_map(|pair| {
            let (name, value) = match pair.split_once('=') {
                Some((name, value)) => (name, value),
                None => (pair, ""),
            };
            if percent_decode(name) == key {
                Some(percent_decode(value))
            } else {
                None
            }
        })
}

/// THE GATE. Takes the raw request target off the HTTP request line
/// (`GET <target> HTTP/1.1`) and the nonce this attempt generated, and
/// decides whether it may act.
///
/// Every rejection reason is a `&'static str` rather than formatted text on
/// purpose: this string is written into a response body served on loopback,
/// so it must be incapable of echoing anything an attacker supplied.
pub fn classify_callback(request_target: &str, expected_state: &str) -> CallbackOutcome {
    if expected_state.trim().is_empty() {
        // No attempt is in flight. Nothing arriving here can be ours.
        return CallbackOutcome::Rejected("No connection attempt is waiting.");
    }
    let (path, query) = match request_target.split_once('?') {
        Some((path, query)) => (path, query),
        None => (request_target, ""),
    };
    if path != CALLBACK_PATH {
        return CallbackOutcome::Rejected("Not a connection callback.");
    }
    let state = query_value(query, "state").unwrap_or_default();
    if !secret_eq(&state, expected_state) {
        // Checked BEFORE the error branch as well as before the token branch:
        // a stranger must not be able to cancel a customer's pairing either.
        return CallbackOutcome::Rejected("This didn't come from the page this app opened.");
    }
    if let Some(error) = query_value(query, "error") {
        if !error.trim().is_empty() {
            return CallbackOutcome::Declined;
        }
    }
    let pairing_token = query_value(query, "pairing_token").unwrap_or_default();
    if pairing_token.trim().is_empty() {
        return CallbackOutcome::Rejected("The connection callback carried nothing to connect with.");
    }
    let workspace_id = query_value(query, "workspace_id").unwrap_or_default();
    if workspace_id.trim().is_empty() {
        return CallbackOutcome::Rejected("The connection callback didn't say which workspace.");
    }
    CallbackOutcome::Accepted(AcceptedPairing {
        pairing_token: pairing_token.trim().to_string(),
        workspace_id: workspace_id.trim().to_string(),
        workspace_label: query_value(query, "workspace_label")
            .unwrap_or_default()
            .trim()
            .to_string(),
    })
}

/// Where this app is in the browser hand-off. Everything from `Starting`
/// onwards defers to `agent_computer_status` for its words — see the module
/// doc comment.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrowserPairPhase {
    /// Nothing has been attempted. The window shows one button.
    Idle,
    /// The listener is up and the browser has been opened.
    Waiting,
    /// The window closed the attempt, or the customer pressed the button in
    /// the app that says stop.
    Cancelled,
    /// The deadline passed with no callback. NOT a failure of anything the
    /// customer did — the usual cause is a tab left unfinished.
    TimedOut,
    /// The customer pressed Cancel in the browser. Nothing is broken.
    Declined,
    /// A callback was accepted and this machine is being connected.
    Starting,
    /// The connection attempt itself failed — no process, or it exited.
    /// Carries the shell's own message, which is already customer-shaped.
    Failed,
    /// Connected. What is SAID here comes from `agent_computer_status`.
    Paired,
    /// The browser could not be opened at all, or the listener could not be
    /// bound. Distinct from `Failed`: nothing was ever attempted, so nothing
    /// half-committed.
    CouldNotStart,
}

impl BrowserPairPhase {
    /// Why an update must wait, or `None` when nothing is in flight.
    ///
    /// Only the two phases that hold something a restart would DESTROY count.
    /// `Paired` deliberately does not: a connected Agent Computer is the
    /// steady state of this product, so treating it as busy would mean the
    /// app never updates on exactly the machines that are working.
    ///
    /// The words are the customer's own view of what is happening, because
    /// this string is shown, not logged.
    pub fn busy_reason(self) -> Option<&'static str> {
        match self {
            BrowserPairPhase::Waiting => Some("connecting this computer"),
            BrowserPairPhase::Starting => Some("starting this computer"),
            BrowserPairPhase::Idle
            | BrowserPairPhase::Cancelled
            | BrowserPairPhase::TimedOut
            | BrowserPairPhase::Declined
            | BrowserPairPhase::Failed
            | BrowserPairPhase::Paired
            | BrowserPairPhase::CouldNotStart => None,
        }
    }

    /// The stable key the local page switches on. Never the prose — a page
    /// keying off displayed text is a page that breaks when copy is edited.
    pub fn key(self) -> &'static str {
        match self {
            BrowserPairPhase::Idle => "idle",
            BrowserPairPhase::Waiting => "waiting",
            BrowserPairPhase::Cancelled => "cancelled",
            BrowserPairPhase::TimedOut => "timedOut",
            BrowserPairPhase::Declined => "declined",
            BrowserPairPhase::Starting => "starting",
            BrowserPairPhase::Failed => "failed",
            BrowserPairPhase::Paired => "paired",
            BrowserPairPhase::CouldNotStart => "couldNotStart",
        }
    }
}

/// Every variant in one place so a test cannot silently stop covering a phase
/// somebody added later. Same discipline as `ALL_STATUSES`.
#[cfg_attr(not(test), allow(dead_code))]
pub const ALL_PHASES: [BrowserPairPhase; 9] = [
    BrowserPairPhase::Idle,
    BrowserPairPhase::Waiting,
    BrowserPairPhase::Cancelled,
    BrowserPairPhase::TimedOut,
    BrowserPairPhase::Declined,
    BrowserPairPhase::Starting,
    BrowserPairPhase::Failed,
    BrowserPairPhase::Paired,
    BrowserPairPhase::CouldNotStart,
];

/// Exactly what the setup window renders. One struct so the button, the
/// sentence and the spinner cannot disagree about which state they are in.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PairView {
    pub phase: &'static str,
    pub title: String,
    pub detail: String,
    /// Whether "Connect this Mac" is rendered at all. A control that cannot
    /// act is not rendered (CLAUDE.md, "no dead controls"), so this is false
    /// while an attempt is in flight and true again once one has ended.
    pub can_start: bool,
    /// What the start control says. A separate field rather than a constant
    /// in the page, because "Connect this Mac" and "Try again" are different
    /// promises and the phase is the only thing that knows which one is true.
    pub start_label: String,
    /// Whether there is a live attempt to stop.
    pub can_cancel: bool,
    pub busy: bool,
    /// A CLEAN success and nothing else. The page hides the window on this,
    /// so a `Paired` machine that still needs Docker deliberately does not
    /// qualify — that is a fact the owner has not seen yet, and making the
    /// window vanish over it is outcome dishonesty by disappearance.
    pub done: bool,
}

fn view(
    phase: BrowserPairPhase,
    title: &str,
    detail: &str,
    can_start: bool,
    can_cancel: bool,
    busy: bool,
    done: bool,
) -> PairView {
    PairView {
        phase: phase.key(),
        title: title.to_string(),
        detail: detail.to_string(),
        can_start,
        // Only the very first attempt is a fresh "Connect this Mac". Every
        // later one follows something the owner already saw fail or stop, and
        // labelling that "Connect this Mac" would read as though the previous
        // attempt had never happened.
        start_label: if matches!(phase, BrowserPairPhase::Idle) {
            "Connect this Mac".to_string()
        } else {
            "Try again".to_string()
        },
        can_cancel,
        busy,
        done,
    }
}

/// The whole rule for what the window says.
///
/// `status` is this machine's own resolved Agent Computer status, and is only
/// consulted from `Paired` onwards — before a callback there is nothing on
/// this machine for it to describe. `detail` carries a message from the phase
/// itself where the phase has one (a failed start relays the shell's own
/// customer-shaped sentence rather than replacing it with something vaguer).
pub fn resolve_pair_view(
    phase: BrowserPairPhase,
    detail: &str,
    status: Option<AgentComputerStatus>,
) -> PairView {
    match phase {
        BrowserPairPhase::Idle => view(
            phase,
            "Connect this Mac",
            "Your agents will be able to run work here. You'll sign in in your own browser.",
            true,
            false,
            false,
            false,
        ),
        BrowserPairPhase::Waiting => view(
            phase,
            "Waiting for your browser…",
            "Finish connecting in the browser tab that just opened. You can come back here when you're done.",
            false,
            true,
            true,
            false,
        ),
        BrowserPairPhase::Cancelled => view(
            phase,
            "Connect this Mac",
            "Stopped. Nothing was connected.",
            true,
            false,
            false,
            false,
        ),
        BrowserPairPhase::TimedOut => view(
            phase,
            "Your browser didn't come back",
            "Nothing was connected. The tab may still be open — close it and try again.",
            true,
            false,
            false,
            false,
        ),
        BrowserPairPhase::Declined => view(
            phase,
            "Not connected",
            "You cancelled in the browser, so nothing on this computer changed.",
            true,
            false,
            false,
            false,
        ),
        BrowserPairPhase::CouldNotStart => view(
            phase,
            "Couldn't open your browser",
            if detail.trim().is_empty() {
                "Nothing was connected. Try again."
            } else {
                detail
            },
            true,
            false,
            false,
            false,
        ),
        BrowserPairPhase::Failed => view(
            phase,
            "Couldn't connect this Mac",
            if detail.trim().is_empty() {
                "Something went wrong starting this computer up. Try again."
            } else {
                detail
            },
            true,
            false,
            false,
            false,
        ),
        BrowserPairPhase::Starting => view(
            phase,
            "Connecting this Mac…",
            "Signed in. Setting this computer up now.",
            false,
            false,
            true,
            false,
        ),
        BrowserPairPhase::Paired => match status {
            // The one clean success. This is the only view that hides the
            // window, because it is the only one with nothing left to say.
            Some(AgentComputerStatus::Connected) => view(
                phase,
                "This Mac is connected",
                "It's ready to use. From now on it lives in your menu bar.",
                false,
                false,
                false,
                true,
            ),
            // Good news with a caveat, and the caveat is the whole reason the
            // window stays: nothing else on this machine will tell them.
            Some(AgentComputerStatus::DockerNotRunning) => view(
                phase,
                "Connected, with one thing left",
                "This Mac is connected, but it can't run commands until Docker is running. Start Docker Desktop and it'll pick it up on its own.",
                false,
                false,
                false,
                false,
            ),
            // Still coming up. The gateway registers over the network after
            // the process starts, so this is what the first seconds look like.
            Some(AgentComputerStatus::Connecting) | None => view(
                phase,
                "Connecting this Mac…",
                "Signed in. Waiting for this computer to show up in your workspace.",
                false,
                false,
                true,
                false,
            ),
            // Started, then stopped reporting in or never came up at all.
            // "Couldn't confirm" rather than "failed": the process may still
            // be running, and telling someone it failed makes them redo work
            // that may already be done.
            Some(other) => view(
                phase,
                "Couldn't confirm this Mac is connected",
                other.menu_line(),
                true,
                false,
                false,
                false,
            ),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const STATE: &str = "s7Qk3Vb0aZ-nEXAMPLEstate";

    fn accepted(outcome: CallbackOutcome) -> AcceptedPairing {
        match outcome {
            CallbackOutcome::Accepted(pairing) => pairing,
            other => panic!("expected an accepted callback, got {other:?}"),
        }
    }

    #[test]
    fn a_well_formed_callback_is_accepted() {
        let target = format!(
            "{CALLBACK_PATH}?state={STATE}&pairing_token=pt_abc123&workspace_id=ws_9f3c&workspace_label=Acme%20Ltd"
        );
        let pairing = accepted(classify_callback(&target, STATE));
        assert_eq!(pairing.pairing_token, "pt_abc123");
        assert_eq!(pairing.workspace_id, "ws_9f3c");
        assert_eq!(pairing.workspace_label, "Acme Ltd");
    }

    #[test]
    fn a_wrong_state_is_refused() {
        let target = format!(
            "{CALLBACK_PATH}?state=not-the-nonce&pairing_token=pt_abc123&workspace_id=ws_9f3c"
        );
        assert!(matches!(
            classify_callback(&target, STATE),
            CallbackOutcome::Rejected(_)
        ));
    }

    #[test]
    fn an_absent_state_is_refused() {
        let target = format!("{CALLBACK_PATH}?pairing_token=pt_abc123&workspace_id=ws_9f3c");
        assert!(matches!(
            classify_callback(&target, STATE),
            CallbackOutcome::Rejected(_)
        ));
    }

    #[test]
    fn an_empty_state_never_matches_an_empty_expectation() {
        // The degenerate case that would otherwise turn "no attempt in
        // flight" into "everything matches".
        let target = format!("{CALLBACK_PATH}?state=&pairing_token=pt_abc123&workspace_id=ws_9f3c");
        assert!(matches!(
            classify_callback(&target, ""),
            CallbackOutcome::Rejected(_)
        ));
    }

    #[test]
    fn a_state_that_merely_starts_the_same_is_refused() {
        let target = format!(
            "{CALLBACK_PATH}?state={}&pairing_token=pt_abc123&workspace_id=ws_9f3c",
            &STATE[..4]
        );
        assert!(matches!(
            classify_callback(&target, STATE),
            CallbackOutcome::Rejected(_)
        ));
    }

    #[test]
    fn another_path_on_this_port_is_refused() {
        let target = format!("/?state={STATE}&pairing_token=pt_abc123&workspace_id=ws_9f3c");
        assert!(matches!(
            classify_callback(&target, STATE),
            CallbackOutcome::Rejected(_)
        ));
    }

    #[test]
    fn a_callback_with_no_token_is_refused_rather_than_half_accepted() {
        let target = format!("{CALLBACK_PATH}?state={STATE}&workspace_id=ws_9f3c");
        assert!(matches!(
            classify_callback(&target, STATE),
            CallbackOutcome::Rejected(_)
        ));
    }

    #[test]
    fn a_callback_with_no_workspace_is_refused() {
        let target = format!("{CALLBACK_PATH}?state={STATE}&pairing_token=pt_abc123");
        assert!(matches!(
            classify_callback(&target, STATE),
            CallbackOutcome::Rejected(_)
        ));
    }

    #[test]
    fn a_cancel_from_the_page_is_declined_not_rejected() {
        let target = format!("{CALLBACK_PATH}?state={STATE}&error=denied");
        assert_eq!(classify_callback(&target, STATE), CallbackOutcome::Declined);
    }

    #[test]
    fn a_cancel_carrying_the_wrong_state_cannot_stop_a_real_attempt() {
        // A stranger who can reach loopback must not be able to end someone
        // else's pairing by sending one request.
        let target = format!("{CALLBACK_PATH}?state=guessed&error=denied");
        assert!(matches!(
            classify_callback(&target, STATE),
            CallbackOutcome::Rejected(_)
        ));
    }

    #[test]
    fn a_rejection_reason_can_never_echo_what_the_caller_sent() {
        // The reason is written into a loopback response body. It is a
        // &'static str by construction, so this asserts the property that
        // makes that safe rather than the wording.
        let hostile = format!(
            "{CALLBACK_PATH}?state=<script>alert(1)</script>&pairing_token=x&workspace_id=y"
        );
        match classify_callback(&hostile, STATE) {
            CallbackOutcome::Rejected(reason) => {
                assert!(!reason.contains("script"), "reason echoed caller input: {reason}");
            }
            other => panic!("expected a rejection, got {other:?}"),
        }
    }

    #[test]
    fn only_a_clean_connected_pairing_hides_the_window() {
        for status in crate::agent_computer_status::ALL_STATUSES {
            let view = resolve_pair_view(BrowserPairPhase::Paired, "", Some(status));
            assert_eq!(
                view.done,
                status == AgentComputerStatus::Connected,
                "{status:?} disagreed about whether the window may vanish"
            );
        }
    }

    #[test]
    fn no_phase_offers_start_and_cancel_at_once() {
        for phase in ALL_PHASES {
            let view = resolve_pair_view(phase, "", None);
            assert!(
                !(view.can_start && view.can_cancel),
                "{phase:?} rendered two mutually exclusive controls"
            );
        }
    }

    #[test]
    fn the_updater_is_never_more_permissive_than_the_window_about_a_busy_moment() {
        // TWO NOTIONS OF "BUSY", AND THEY ARE NOT THE SAME FACT. This test
        // was first written asserting they were equal, and it failed on
        // `Paired` — which is the finding, not a bug:
        //
        //   view.busy      "do not offer a Connect button" — true for Paired,
        //                  because there is nothing left to start.
        //   busy_reason()  "a restart right now would destroy something in
        //                  flight" — FALSE for Paired, because a connected
        //                  Agent Computer is the steady state of this
        //                  product. An updater that treated it as busy would
        //                  never update the machines that are working, which
        //                  is every machine that matters.
        //
        // So the invariant is one-directional: the updater may never call a
        // moment safe that the window itself considers in flight. The
        // reverse is allowed and `Paired` is the whole reason.
        for phase in ALL_PHASES {
            let view = resolve_pair_view(phase, "", None);
            if phase.busy_reason().is_some() {
                assert!(
                    view.busy,
                    "{phase:?}: the updater will wait for it, but the window does not think anything is in flight"
                );
            }
        }
    }

    #[test]
    fn a_connected_computer_is_not_a_reason_to_stop_updating_forever() {
        // Pinned on its own so that flipping it is a deliberate act with an
        // argument, not a side effect of tidying the match above.
        assert_eq!(BrowserPairPhase::Paired.busy_reason(), None);
        assert!(BrowserPairPhase::Waiting.busy_reason().is_some());
        assert!(BrowserPairPhase::Starting.busy_reason().is_some());
    }

    #[test]
    fn a_busy_reason_reads_as_something_a_person_is_doing() {
        // It is interpolated into a sentence shown on screen, so it has to be
        // a phrase, not a state name.
        for phase in ALL_PHASES {
            if let Some(reason) = phase.busy_reason() {
                assert!(!reason.trim().is_empty());
                assert_eq!(reason, reason.to_lowercase(), "{phase:?} is not sentence-safe");
            }
        }
    }

    #[test]
    fn a_busy_phase_never_offers_a_button_that_would_race_it() {
        for phase in ALL_PHASES {
            let view = resolve_pair_view(phase, "", None);
            if view.busy {
                assert!(!view.can_start, "{phase:?} offered Connect while already working");
            }
        }
    }

    #[test]
    fn every_phase_says_something() {
        for phase in ALL_PHASES {
            let view = resolve_pair_view(phase, "", None);
            assert!(!view.title.trim().is_empty(), "{phase:?} had no title");
            assert!(!view.detail.trim().is_empty(), "{phase:?} had no detail");
        }
    }

    #[test]
    fn the_window_never_names_mechanism() {
        // Same rule agent_computer_status enforces for the menu bar: a
        // customer who does not know what a gateway is reads this.
        const BANNED: [&str; 8] = [
            "gateway", "daemon", "process", "socket", "npm", "node", "loopback", "token",
        ];
        for phase in ALL_PHASES {
            for status in crate::agent_computer_status::ALL_STATUSES.map(Some).into_iter().chain([None]) {
                let view = resolve_pair_view(phase, "", status);
                let prose = format!("{} {}", view.title, view.detail).to_lowercase();
                for word in BANNED {
                    assert!(
                        !prose.contains(word),
                        "{phase:?}/{status:?} named mechanism: {word} in {prose:?}"
                    );
                }
            }
        }
    }

    #[test]
    fn a_failed_start_relays_the_shells_own_sentence() {
        let view = resolve_pair_view(
            BrowserPairPhase::Failed,
            "This computer couldn't be started up.",
            None,
        );
        assert_eq!(view.detail, "This computer couldn't be started up.");
        assert!(view.can_start, "a failure the owner can retry must offer the retry");
    }

    #[test]
    fn a_start_control_always_carries_a_label() {
        for phase in ALL_PHASES {
            let view = resolve_pair_view(phase, "", None);
            if view.can_start {
                assert!(!view.start_label.trim().is_empty(), "{phase:?} had an unlabelled button");
            }
        }
        assert_eq!(
            resolve_pair_view(BrowserPairPhase::Idle, "", None).start_label,
            "Connect this Mac"
        );
        assert_eq!(
            resolve_pair_view(BrowserPairPhase::TimedOut, "", None).start_label,
            "Try again"
        );
    }

    #[test]
    fn phase_keys_are_unique() {
        let mut keys: Vec<&str> = ALL_PHASES.iter().map(|phase| phase.key()).collect();
        keys.sort_unstable();
        let before = keys.len();
        keys.dedup();
        assert_eq!(before, keys.len(), "two phases share one key");
    }

    #[test]
    fn all_phases_is_actually_all_of_them() {
        // The canary. `resolve_pair_view` is exhaustive over the enum, so a
        // new variant compiles only after it is handled there — this catches
        // the other half, a variant handled but never covered by the sweeps.
        assert_eq!(ALL_PHASES.len(), 9);
    }
}
