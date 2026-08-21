//! What the menu bar is allowed to SAY about this computer.
//!
//! Pure data and pure functions in their own module, for the same reason
//! `frontend/lib/desktop/desktop-pairing-state.ts` is one and for the same
//! reason `channel-doors.ts` and `agent-count-shape.ts` are: the test imports
//! THE REAL RULE rather than a literal copied beside it.
//!
//! ── Why this cannot reuse the webview's rule ──────────────────────────────
//! `desktop-pairing-state.ts` answers the same question from a completely
//! different vantage point: it holds the customer's authenticated session and
//! asks the CONTROL PLANE (`GET /gateway/registrations`) what it thinks of
//! this machine. That is the better answer and it is unavailable here — the
//! menu bar's whole job is to be right while no window exists, with no
//! session, no cookies and possibly no network. So this reads what the
//! gateway itself wrote to disk. Two vantage points, two modules, deliberately
//! not one: collapsing them would mean either the tray needing a live session
//! or the pairing surface trusting a self-report.
//!
//! ── A self-report goes stale the moment the reporter dies ─────────────────
//! `checkpoints.json` is written BY the gateway. CLAUDE.md already records
//! exactly this trap one level up ("a gateway-published health snapshot goes
//! stale the moment the gateway itself goes offline and keeps asserting
//! whatever it last said — any reader must check the gateway is online FIRST
//! or it can paint 'Connected' over a machine that's down"). So this rule
//! checks liveness in two independent ways before it will repeat what the
//! file says:
//!
//!   1. is the process alive at all (the caller's `kill(pid, 0)`), and
//!   2. is the file FRESH — `updatedAt` advances on every successful
//!      heartbeat (ws-client.ts's `sendHeartbeat` ends in
//!      `saveHealthState("online", …)`, and the server-negotiated interval
//!      defaults to 10s in gateway_registry_service.py), so a stale
//!      `updatedAt` beside a live process means the gateway is wedged, not
//!      connected.
//!
//! A live process asserting `online` from a stale file is its own fact
//! (`Unresponsive`), never folded into `Connected` (a lie) and never into
//! `Connecting` (a softer lie that tells the owner to wait for something that
//! is not coming).
//!
//! ── Docker fails OPEN, on purpose ────────────────────────────────────────
//! Docker readiness is a CAVEAT attached to a connected machine, not the
//! primary fact. An unreadable probe therefore resolves to `Connected`, never
//! to `DockerNotRunning`: telling someone to start Docker that is already
//! running sends them to fix a thing that is not broken, and the connected
//! half of the sentence is true either way. Same posture as the invite-mail
//! verification gate CLAUDE.md documents ("fails OPEN on an unreadable
//! status, deliberately").

use std::time::Duration;

/// How long `checkpoints.json` may go unrefreshed before a live process's own
/// claim of `online` stops being believed.
///
/// The gateway writes it on every successful heartbeat and the negotiated
/// interval is 10s (`DEFAULT_GATEWAY_HEARTBEAT_INTERVAL_SECONDS`), so this is
/// ~6 consecutive missed heartbeats. Deliberately generous: a menu bar item
/// that flaps between "Connected" and "not reporting in" on one slow tick is
/// worse than one that takes a minute to notice a genuinely wedged process.
pub const HEALTH_FRESHNESS_WINDOW: Duration = Duration::from_secs(60);

/// Whether a Docker daemon answered on this machine.
///
/// `Unknown` is a real third answer and is NOT a quieter `NotReady` — see the
/// module doc comment. It is what a probe that could not be performed returns.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DockerProbe {
    Ready,
    NotReady,
    Unknown,
}

/// The gateway's own last written word about its connection to the cloud,
/// read out of `<state_dir>/checkpoints.json`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HealthSnapshot {
    /// `checkpoints.json`'s `healthState` — one of online / offline /
    /// reconnecting / degraded (state/checkpoints.ts's `GatewayHealthState`).
    /// Anything unrecognised is carried through verbatim and fails closed
    /// below rather than being coerced into the nearest known value.
    pub health_state: String,
    /// How long ago `updatedAt` was written. `None` when the field is absent
    /// or unparseable — which is NOT the same as "a long time ago": a
    /// checkpoints file that predates this field is unknown-age, and unknown
    /// age must not be read as fresh.
    pub age: Option<Duration>,
}

/// Everything the rule below is allowed to look at. One struct so a caller
/// cannot accidentally pass three booleans in the wrong order, and so a new
/// input has to be added deliberately in one place.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StatusInputs {
    /// Has this machine ever completed a pairing (the gateway state directory
    /// holds anything)? Distinguishes a fresh install from a broken one.
    pub ever_paired: bool,
    /// Did the owner turn this off from the menu? A DELIBERATE stop and an
    /// unexpected one are different facts — one needs no action and the other
    /// is something going wrong — so they must not share a message, and
    /// nothing else can tell them apart: both look like "no process running".
    pub turned_off_by_owner: bool,
    /// Is the gateway child process alive right now — `kill(pid, 0)`, resolved
    /// by the caller. The FIRST gate on believing anything the gateway wrote.
    pub process_alive: bool,
    /// `None` when `checkpoints.json` is missing or unreadable.
    pub health: Option<HealthSnapshot>,
    pub docker: DockerProbe,
}

/// The facts the menu bar may state. The founder named four —
/// Connected / Connecting / Offline / Docker-not-running — and required that
/// they "never collapse into one on-off light". The two extra members are not
/// scope creep, they are that same rule applied honestly: `NotPaired` is a
/// machine nobody has set up (telling them it is "Offline" sends them to fix a
/// connection that was never made), and `Unresponsive` is a running gateway
/// that has stopped reporting in (see the module doc comment).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentComputerStatus {
    NotPaired,
    Connecting,
    Connected,
    DockerNotRunning,
    Unresponsive,
    Offline,
    /// The owner used "Disconnect". Nothing is wrong and there is nothing to
    /// fix — folding this into `Offline` would report a fault for a state the
    /// owner chose on purpose, and would put a "something is wrong" icon in
    /// the menu bar forever.
    TurnedOff,
}

/// Every variant, in one place, so a test cannot silently stop covering a
/// state someone added later.
///
/// Deliberately lives here beside the enum rather than inside `mod tests`:
/// the mechanical copy guards that iterate it are only worth anything if the
/// list is maintained next to the thing it enumerates, and
/// `all_statuses_is_actually_all_of_them` is the canary that fails when it is
/// not. Unused outside tests, and that is the intended shape.
#[cfg_attr(not(test), allow(dead_code))]
pub const ALL_STATUSES: [AgentComputerStatus; 7] = [
    AgentComputerStatus::NotPaired,
    AgentComputerStatus::Connecting,
    AgentComputerStatus::Connected,
    AgentComputerStatus::DockerNotRunning,
    AgentComputerStatus::Unresponsive,
    AgentComputerStatus::Offline,
    AgentComputerStatus::TurnedOff,
];

impl AgentComputerStatus {
    /// The one line in the menu. Present tense, plain language, no mechanism
    /// — a customer who does not know what a gateway is reads this, so
    /// "gateway", "process", "daemon", "heartbeat" and "socket" never appear.
    /// Asserted mechanically by `menu_line_never_names_mechanism`.
    pub fn menu_line(self) -> &'static str {
        match self {
            AgentComputerStatus::NotPaired => "Not set up yet",
            AgentComputerStatus::Connecting => "Connecting…",
            AgentComputerStatus::Connected => "Connected",
            // Names the CONSEQUENCE first, because that is the part the owner
            // cares about, and the cause second, because that is the part they
            // can act on. Same words the pairing surface already uses
            // (desktop-pairing-state.ts's `resolveStartOutcome`) so the two
            // surfaces cannot describe one machine two different ways.
            AgentComputerStatus::DockerNotRunning => "Connected — but can't run commands until Docker starts",
            AgentComputerStatus::Unresponsive => "Running, but not reporting in",
            AgentComputerStatus::Offline => "Offline — your agents can't use this computer",
            AgentComputerStatus::TurnedOff => "Turned off — your agents aren't using this computer",
        }
    }

    /// Whether this state is one a person should look at. Drives the tray
    /// icon's attention rendering — never a second, independently-drifting
    /// opinion about which states are bad.
    pub fn needs_attention(self) -> bool {
        matches!(
            self,
            AgentComputerStatus::DockerNotRunning
                | AgentComputerStatus::Unresponsive
                | AgentComputerStatus::Offline
        )
    }

    /// Which of the two mutually exclusive on/off controls the menu renders.
    ///
    /// Both are never shown at once and neither is ever greyed out — a control
    /// that cannot act is not rendered at all (CLAUDE.md's "no dead controls"),
    /// and a machine that was never paired has nothing to turn on OR off from
    /// here: it needs the window, which is the separate `needs_window` case.
    pub fn power_control(self) -> Option<PowerControl> {
        match self {
            AgentComputerStatus::NotPaired => None,
            AgentComputerStatus::TurnedOff => Some(PowerControl::Reconnect),
            _ => Some(PowerControl::Disconnect),
        }
    }

    /// Whether quitting would take this customer's agents offline right now.
    /// Quitting is never neutral once a machine is serving — but it IS neutral
    /// on a machine that is not, and warning about a consequence that cannot
    /// occur is how a real warning stops being read.
    pub fn quit_takes_agents_offline(self) -> bool {
        !matches!(
            self,
            AgentComputerStatus::NotPaired
                | AgentComputerStatus::Offline
                | AgentComputerStatus::TurnedOff
        )
    }
}

/// The single on/off control in the menu. An enum rather than a bool so the
/// menu cannot render a label that disagrees with the action behind it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PowerControl {
    Disconnect,
    Reconnect,
}

impl PowerControl {
    pub fn label(self) -> &'static str {
        match self {
            PowerControl::Disconnect => "Disconnect this computer",
            PowerControl::Reconnect => "Reconnect this computer",
        }
    }
}

/// What this app should do when it is launched.
///
/// The founder's rule, verbatim: "The window appears exactly once: first run,
/// for sign-in and pairing… It never reopens unless the customer explicitly
/// asks or something is broken." So the ONLY launch that shows a window is one
/// that genuinely needs a person — a machine with nothing set up. Everything
/// else is handled without one, which is what makes this a menu bar app rather
/// than a windowed app that happens to have a menu bar item.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LaunchPlan {
    /// Nothing is set up. This is the one launch that needs a person: only a
    /// signed-in webview can mint a pairing intent.
    ShowWindowToPair,
    /// Paired, and something is already serving. The app has nothing to do but
    /// put its icon in the menu bar.
    StayInMenuBar,
    /// Paired, nothing running, and the owner did not ask for that. Start it
    /// here rather than waiting for a window nobody is going to open — this is
    /// what makes an already-paired machine work with no window at all, e.g.
    /// after a login where OS-level supervision did not fire.
    ResumeSilently,
    /// The owner used Disconnect. Their decision outranks every automatic
    /// recovery below it, or the control would undo itself on the next launch.
    RespectTurnedOff,
}

pub fn plan_launch(inputs: &StatusInputs) -> LaunchPlan {
    if !inputs.ever_paired {
        return LaunchPlan::ShowWindowToPair;
    }
    if inputs.turned_off_by_owner {
        return LaunchPlan::RespectTurnedOff;
    }
    if inputs.process_alive {
        return LaunchPlan::StayInMenuBar;
    }
    LaunchPlan::ResumeSilently
}

fn is_fresh(age: Option<Duration>) -> bool {
    // An absent/unparseable `updatedAt` is unknown age, and unknown age is
    // NOT fresh. Defaulting the other way would let a checkpoints file with no
    // timestamp at all assert "Connected" forever.
    matches!(age, Some(age) if age <= HEALTH_FRESHNESS_WINDOW)
}

/// The whole rule. Deliberately total and deliberately ordered: liveness
/// outranks the self-report, and the self-report outranks the Docker caveat.
pub fn resolve_status(inputs: &StatusInputs) -> AgentComputerStatus {
    if !inputs.ever_paired {
        // Checked before liveness on purpose. A never-paired machine has no
        // process to be missing, so "Offline" would be a wrong diagnosis of a
        // correct state.
        return AgentComputerStatus::NotPaired;
    }
    if inputs.turned_off_by_owner {
        // Checked before liveness, and deliberately NOT gated on the process
        // being dead: a supervisor that has not finished tearing the child
        // down yet must not make the menu flash "Offline — your agents can't
        // use this computer" at someone who just chose to turn it off. The
        // owner's own decision is the fact here; the process catching up is
        // mechanism.
        return AgentComputerStatus::TurnedOff;
    }
    if !inputs.process_alive {
        // The first and most important gate on the gateway's SELF-REPORT.
        // Nothing below this line is believed about a machine whose gateway is
        // not running, however confidently the file it left behind asserts
        // `online`.
        return AgentComputerStatus::Offline;
    }

    let Some(health) = inputs.health.as_ref() else {
        // Alive, but has not written a checkpoint yet. That is what the first
        // ~seconds of a brand-new pairing look like, so it is `Connecting`
        // rather than an error.
        return AgentComputerStatus::Connecting;
    };

    match health.health_state.trim() {
        "online" => {
            if !is_fresh(health.age) {
                // The file says connected and the process is alive, but
                // nothing has refreshed it. Believing the file here is exactly
                // the stale-self-report trap; calling it `Connecting` would
                // tell the owner to wait for something that is not coming.
                return AgentComputerStatus::Unresponsive;
            }
            match inputs.docker {
                DockerProbe::NotReady => AgentComputerStatus::DockerNotRunning,
                // Ready AND Unknown both resolve here — see the module doc
                // comment on why the caveat fails open.
                DockerProbe::Ready | DockerProbe::Unknown => AgentComputerStatus::Connected,
            }
        }
        // Both are the gateway's own words for "working on it": `reconnecting`
        // after a dropped socket, `degraded` after a failed heartbeat that has
        // not yet torn the socket down. Neither is a resting state and neither
        // is a failure the owner can act on.
        "reconnecting" | "degraded" => AgentComputerStatus::Connecting,
        // A live process that has written `offline` has genuinely disconnected
        // and is retrying underneath — the process being alive is why this is
        // `Connecting` rather than `Offline`, which is reserved for "there is
        // nothing running at all".
        "offline" => AgentComputerStatus::Connecting,
        // Anything unrecognised fails CLOSED. A status vocabulary that grows
        // upstream must never silently start reading as connected here.
        _ => AgentComputerStatus::Unresponsive,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn inputs(health_state: &str, age_secs: Option<u64>, docker: DockerProbe) -> StatusInputs {
        StatusInputs {
            ever_paired: true,
            turned_off_by_owner: false,
            process_alive: true,
            health: Some(HealthSnapshot {
                health_state: health_state.to_string(),
                age: age_secs.map(Duration::from_secs),
            }),
            docker,
        }
    }

    #[test]
    fn a_never_paired_machine_is_not_offline() {
        // "Offline" would send someone to fix a connection that was never
        // made. These are different facts and the whole point of this module
        // is that they do not share one message.
        let status = resolve_status(&StatusInputs {
            ever_paired: false,
            turned_off_by_owner: false,
            process_alive: false,
            health: None,
            docker: DockerProbe::Unknown,
        });
        assert_eq!(status, AgentComputerStatus::NotPaired);
        assert_eq!(status.power_control(), None);
        assert!(!status.quit_takes_agents_offline());
    }

    #[test]
    fn a_deliberate_turn_off_is_not_a_fault() {
        // Collapsing this into Offline would put a "something is wrong" icon
        // in the menu bar forever over a state the owner chose on purpose.
        let mut input = inputs("offline", Some(1), DockerProbe::Ready);
        input.turned_off_by_owner = true;
        input.process_alive = false;
        let status = resolve_status(&input);
        assert_eq!(status, AgentComputerStatus::TurnedOff);
        assert!(!status.needs_attention());
        assert_eq!(status.power_control(), Some(PowerControl::Reconnect));
        assert!(!status.quit_takes_agents_offline());
    }

    #[test]
    fn turning_off_wins_before_the_process_has_finished_exiting() {
        // The teardown is not instant. Reading liveness first would flash
        // "Offline — your agents can't use this computer" at someone who just
        // pressed Disconnect, which is a fault message for a chosen state.
        let mut input = inputs("online", Some(1), DockerProbe::Ready);
        input.turned_off_by_owner = true;
        assert!(input.process_alive);
        assert_eq!(resolve_status(&input), AgentComputerStatus::TurnedOff);
    }

    #[test]
    fn a_never_paired_machine_outranks_a_stale_turned_off_marker() {
        // Disconnect-then-unpair must not leave a marker that hides the fact
        // that there is nothing set up at all.
        let mut input = inputs("online", Some(1), DockerProbe::Ready);
        input.ever_paired = false;
        input.turned_off_by_owner = true;
        assert_eq!(resolve_status(&input), AgentComputerStatus::NotPaired);
    }

    #[test]
    fn a_dead_process_is_offline_however_confidently_its_file_says_online() {
        // THE regression this module exists to prevent: a gateway that was
        // connected, then crashed, leaves `checkpoints.json` asserting
        // `online` with a perfectly fresh timestamp. Reading the file without
        // checking liveness first paints "Connected" over a machine that is
        // down — CLAUDE.md records this exact shape for channel pills.
        let mut input = inputs("online", Some(1), DockerProbe::Ready);
        input.process_alive = false;
        assert_eq!(resolve_status(&input), AgentComputerStatus::Offline);
    }

    #[test]
    fn a_live_process_with_a_stale_online_file_is_unresponsive_not_connected() {
        let input = inputs("online", Some(HEALTH_FRESHNESS_WINDOW.as_secs() + 1), DockerProbe::Ready);
        assert_eq!(resolve_status(&input), AgentComputerStatus::Unresponsive);
    }

    #[test]
    fn freshness_is_inclusive_at_the_boundary() {
        let input = inputs("online", Some(HEALTH_FRESHNESS_WINDOW.as_secs()), DockerProbe::Ready);
        assert_eq!(resolve_status(&input), AgentComputerStatus::Connected);
    }

    #[test]
    fn an_absent_timestamp_is_unknown_age_and_therefore_not_fresh() {
        // Not "assume fresh" — a checkpoints file carrying no `updatedAt` at
        // all would otherwise assert Connected forever.
        let input = inputs("online", None, DockerProbe::Ready);
        assert_eq!(resolve_status(&input), AgentComputerStatus::Unresponsive);
    }

    #[test]
    fn no_checkpoint_yet_is_connecting_because_that_is_what_a_fresh_pairing_looks_like() {
        let mut input = inputs("online", Some(1), DockerProbe::Ready);
        input.health = None;
        assert_eq!(resolve_status(&input), AgentComputerStatus::Connecting);
    }

    #[test]
    fn docker_not_ready_is_its_own_fact_beside_a_real_connection() {
        let input = inputs("online", Some(1), DockerProbe::NotReady);
        let status = resolve_status(&input);
        assert_eq!(status, AgentComputerStatus::DockerNotRunning);
        // Still a connected machine: quitting still takes agents offline, and
        // the line still says so.
        assert!(status.quit_takes_agents_offline());
        assert!(status.menu_line().contains("Connected"));
    }

    #[test]
    fn an_unreadable_docker_probe_fails_open_to_connected() {
        // The caveat is unreadable, the connection is not. Reporting
        // "start Docker" here would send someone to fix a thing that may well
        // already be working.
        let input = inputs("online", Some(1), DockerProbe::Unknown);
        assert_eq!(resolve_status(&input), AgentComputerStatus::Connected);
    }

    #[test]
    fn the_gateways_own_working_on_it_words_all_read_as_connecting() {
        for state in ["reconnecting", "degraded", "offline"] {
            assert_eq!(
                resolve_status(&inputs(state, Some(1), DockerProbe::Ready)),
                AgentComputerStatus::Connecting,
                "{state} should read as Connecting while the process is alive",
            );
        }
    }

    #[test]
    fn an_unrecognised_health_state_fails_closed() {
        // A vocabulary that grows upstream must never silently start reading
        // as connected. `""` covers a present-but-empty field too.
        for state in ["", "revoked", "quantum", "ONLINE"] {
            assert_eq!(
                resolve_status(&inputs(state, Some(1), DockerProbe::Ready)),
                AgentComputerStatus::Unresponsive,
                "{state:?} must not resolve to a connected reading",
            );
        }
    }

    #[test]
    fn whitespace_around_a_real_state_is_still_that_state() {
        assert_eq!(
            resolve_status(&inputs("  online  ", Some(1), DockerProbe::Ready)),
            AgentComputerStatus::Connected,
        );
    }

    #[test]
    fn menu_line_never_names_mechanism() {
        // A mechanical guard rather than a review habit: the leak this
        // prevents always lives inside one branch, never in a heading someone
        // re-reads. Same discipline as the channel-copy test that bans
        // "plugin"/"npm"/"package" from customer-facing remediation text.
        const BANNED: [&str; 10] = [
            "gateway", "process", "daemon", "heartbeat", "socket", "node", "npm", "pid", "websocket",
            "checkpoint",
        ];
        for status in ALL_STATUSES {
            let line = status.menu_line().to_lowercase();
            assert!(!line.is_empty(), "{status:?} must say something");
            for banned in BANNED {
                assert!(
                    !line.contains(banned),
                    "{status:?} menu line names mechanism ({banned:?}): {line:?}",
                );
            }
        }
    }

    #[test]
    fn every_status_says_something_different() {
        // Four facts that "must never collapse into one on-off light" — held
        // to structurally, so a future edit cannot quietly give two states the
        // same sentence.
        let mut lines: Vec<&str> = ALL_STATUSES.iter().map(|status| status.menu_line()).collect();
        lines.sort_unstable();
        let before = lines.len();
        lines.dedup();
        assert_eq!(before, lines.len(), "two statuses share one sentence: {lines:?}");
    }

    #[test]
    fn attention_is_exactly_the_states_a_person_can_do_something_about() {
        assert!(!AgentComputerStatus::Connected.needs_attention());
        assert!(!AgentComputerStatus::Connecting.needs_attention());
        // NotPaired is not "attention" — the window is already open on a
        // machine that has not paired, so the tray shouting about it would be
        // a second alarm for a thing already on screen.
        assert!(!AgentComputerStatus::NotPaired.needs_attention());
        assert!(AgentComputerStatus::DockerNotRunning.needs_attention());
        assert!(AgentComputerStatus::Unresponsive.needs_attention());
        assert!(AgentComputerStatus::Offline.needs_attention());
    }

    #[test]
    fn the_only_launch_that_opens_a_window_is_one_that_needs_a_person() {
        // "The window appears exactly once: first run." Every other launch is
        // handled without one — that is what makes this a menu bar app.
        let mut fresh = inputs("online", Some(1), DockerProbe::Ready);
        fresh.ever_paired = false;
        fresh.process_alive = false;
        assert_eq!(plan_launch(&fresh), LaunchPlan::ShowWindowToPair);

        let serving = inputs("online", Some(1), DockerProbe::Ready);
        assert_eq!(plan_launch(&serving), LaunchPlan::StayInMenuBar);

        let stopped = StatusInputs {
            process_alive: false,
            ..inputs("online", Some(1), DockerProbe::Ready)
        };
        assert_eq!(plan_launch(&stopped), LaunchPlan::ResumeSilently);
    }

    #[test]
    fn a_turned_off_machine_does_not_quietly_restart_itself_at_launch() {
        // Without this the Disconnect control undoes itself on the next login
        // — a control that silently reverses its own effect is worse than no
        // control at all.
        let turned_off = StatusInputs {
            turned_off_by_owner: true,
            process_alive: false,
            ..inputs("offline", Some(1), DockerProbe::Unknown)
        };
        assert_eq!(plan_launch(&turned_off), LaunchPlan::RespectTurnedOff);
    }

    #[test]
    fn an_unpaired_machine_needs_a_window_even_if_a_stale_marker_says_otherwise() {
        let confused = StatusInputs {
            ever_paired: false,
            turned_off_by_owner: true,
            process_alive: false,
            ..inputs("offline", Some(1), DockerProbe::Unknown)
        };
        assert_eq!(plan_launch(&confused), LaunchPlan::ShowWindowToPair);
    }

    #[test]
    fn all_statuses_is_actually_all_of_them() {
        // The canary for every test above that iterates ALL_STATUSES: a new
        // variant added without extending this array would silently narrow
        // the mechanical copy guards rather than fail. `resolve_status` can
        // produce every member, so exercising the inputs that produce each one
        // is what proves the list is complete rather than merely long.
        let produced = [
            resolve_status(&StatusInputs {
                ever_paired: false,
                turned_off_by_owner: false,
                process_alive: false,
                health: None,
                docker: DockerProbe::Unknown,
            }),
            resolve_status(&inputs("reconnecting", Some(1), DockerProbe::Ready)),
            resolve_status(&inputs("online", Some(1), DockerProbe::Ready)),
            resolve_status(&inputs("online", Some(1), DockerProbe::NotReady)),
            resolve_status(&inputs("online", None, DockerProbe::Ready)),
            resolve_status(&StatusInputs {
                process_alive: false,
                ..inputs("online", Some(1), DockerProbe::Ready)
            }),
            resolve_status(&StatusInputs {
                turned_off_by_owner: true,
                ..inputs("online", Some(1), DockerProbe::Ready)
            }),
        ];
        for status in produced {
            assert!(
                ALL_STATUSES.contains(&status),
                "{status:?} is reachable but missing from ALL_STATUSES",
            );
        }
        assert_eq!(
            ALL_STATUSES.len(),
            produced.len(),
            "ALL_STATUSES and the reachable set disagree",
        );
    }
}
