//! Whether this copy of the app can replace itself, and when it should try.
//!
//! THE FOUNDER'S BRIEF, and it is the whole design: *"this icon must have
//! upgraded button once we push something it must reload! nobody is going to
//! press this button to reload... It must actually work!"* So updates are
//! AUTOMATIC. A visible control may exist to trigger one sooner; it is never
//! the mechanism.
//!
//! WHY A SEPARATE MODULE AND NOT TWO MORE FUNCTIONS IN lib.rs: everything
//! here is a DECISION — may this copy update, and is now a safe moment — and
//! a decision that only exists inside a `#[tauri::command]` cannot be tested.
//! The impure half (reading `current_exe`, probing a directory for write
//! access) is three functions at the bottom; every rule above them is pure.
//!
//! ## The rule this module exists to enforce
//!
//! CLAUDE.md, MAN-331: **never advertise an update whose success could not be
//! observed.** The desktop app had that defect in its purest form —
//! `tauri.conf.json`'s `version` was the literal `0.1.0` and had never been
//! bumped, exactly like `GATEWAY_VERSION`, so "it updated" and "it did
//! nothing" produced the identical `current_version` afterwards. That half is
//! fixed in CI (the version is stamped per build from the commit count, so it
//! moves on its own with nobody remembering).
//!
//! The half that lives HERE is the other direction: refusing to try when the
//! attempt is structurally incapable of sticking. Three such cases exist on
//! macOS and every one of them is silent — the updater reports success and
//! the next launch is the same build:
//!
//! ```text
//! BUILD OUTPUT     the .app sits under target/{debug,release}/bundle.
//!                  Replacing it is meaningless: the next `cargo tauri
//!                  build` overwrites the "update" with the local tree.
//!                  THIS IS THE FOUNDER'S OWN MAC TODAY.
//! TRANSLOCATED     macOS App Translocation. An UNSIGNED, quarantined app
//!                  run from ~/Downloads is mounted read-only at
//!                  /private/var/folders/.../AppTranslocation/<uuid>/d/.
//!                  `rename()` onto that path cannot work, and the app
//!                  cannot even see where it really lives. This app ships
//!                  unsigned by decision, so this is the DEFAULT state of a
//!                  freshly downloaded copy, not an edge case.
//! NOT WRITABLE     the .app's parent directory refuses a write (a copy in
//!                  another user's tree, a read-only volume, a DMG).
//! ```
//!
//! Each is a different fact with a different single action, so each carries
//! its own message. None of them is "up to date" and none of them is
//! "failed" — they are the fourth state, "cannot receive updates, and here
//! is why", which the Hardware page already had to grow for the gateway.

use std::path::{Path, PathBuf};
use std::time::Duration;

/// How long after launch the first check runs.
///
/// Not zero: launch is the busiest moment this process ever has — it resolves
/// its own identity, brings the tray up, and may immediately resume a paired
/// Agent Computer. An update that lands in that window would restart the app
/// underneath its own startup. 90s is past all of it and still inside the
/// first coffee, so a customer who opens the app the morning after a release
/// has the fix before they have done anything with it.
pub const FIRST_CHECK_DELAY: Duration = Duration::from_secs(90);

/// How often it checks after that.
///
/// SIX HOURS, and the number is a deliberate trade rather than a default.
/// This is a menu-bar accessory that is never quit — it runs for weeks, so
/// "on launch" alone would leave a machine on a build from a month ago. The
/// bound to pick is how stale a running copy may get:
///
/// ```text
/// 1h    24 requests/day/box for a product that ships a few times a week.
///       Real cost, no real gain: nothing is that urgent on a desktop shell.
/// 6h    <- 4 requests/day. A fix merged in the morning is on every running
///       box the same working day, without anyone opening anything.
/// 24h   a morning fix reaches the founder tomorrow. That is the complaint
///       this whole change exists to answer, one day later.
/// ```
///
/// Each check is one conditional GET of a ~400 byte JSON document, so the
/// interval is chosen for staleness alone; bandwidth does not enter into it.
pub const CHECK_INTERVAL: Duration = Duration::from_secs(6 * 60 * 60);

/// Where this copy of the app lives, and therefore whether replacing it can
/// stick. Every non-`Installable` variant is a REFUSAL that names itself.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UpdateSite {
    /// The bundle can be swapped in place.
    Installable { bundle: PathBuf },
    /// Running from `target/{debug,release}/bundle/...` — a build output.
    BuildOutput { bundle: PathBuf },
    /// macOS App Translocation: read-only, and the real location is hidden.
    Translocated,
    /// The bundle exists at a normal path whose parent refuses a write.
    ParentNotWritable { bundle: PathBuf },
    /// No `.app` (or AppImage) anywhere above the executable.
    NotAnInstalledApp { executable: PathBuf },
}

impl UpdateSite {
    pub fn can_install(&self) -> bool {
        matches!(self, UpdateSite::Installable { .. })
    }

    /// A stable code the frontend and the logs can both match on. Never
    /// classify one of these by its prose — CLAUDE.md, "stale string
    /// matching".
    pub fn code(&self) -> &'static str {
        match self {
            UpdateSite::Installable { .. } => "installable",
            UpdateSite::BuildOutput { .. } => "build_output",
            UpdateSite::Translocated => "translocated",
            UpdateSite::ParentNotWritable { .. } => "parent_not_writable",
            UpdateSite::NotAnInstalledApp { .. } => "not_an_installed_app",
        }
    }

    /// What is true. States the fact; it does not lecture.
    pub fn detail(&self) -> String {
        match self {
            UpdateSite::Installable { .. } => "Empyralis can update itself.".to_string(),
            UpdateSite::BuildOutput { bundle } => format!(
                "This copy is running from a build folder ({}), so an update would be overwritten by the next build.",
                bundle.display()
            ),
            UpdateSite::Translocated => {
                "macOS is running this copy from a temporary read-only location, so it cannot replace itself.".to_string()
            }
            UpdateSite::ParentNotWritable { bundle } => format!(
                "Empyralis cannot write to the folder it lives in ({}).",
                bundle
                    .parent()
                    .map(|parent| parent.display().to_string())
                    .unwrap_or_else(|| bundle.display().to_string())
            ),
            UpdateSite::NotAnInstalledApp { .. } => {
                "This copy is not an installed application, so there is nothing to replace.".to_string()
            }
        }
    }

    /// The ONE thing the person does about it, or `None` when there is
    /// nothing for them to do. A refusal with no action is worse than no
    /// refusal — it reads as an accusation.
    pub fn action(&self) -> Option<String> {
        match self {
            UpdateSite::Installable { .. } => None,
            // All three of these are the same single move, and it is
            // deliberately phrased as the move rather than as the mechanism:
            // the customer drags one icon, once, forever.
            UpdateSite::BuildOutput { .. }
            | UpdateSite::Translocated
            | UpdateSite::ParentNotWritable { .. } => {
                Some("Move Empyralis into your Applications folder and open it from there.".to_string())
            }
            UpdateSite::NotAnInstalledApp { .. } => {
                Some("Install Empyralis into your Applications folder and open it from there.".to_string())
            }
        }
    }
}

/// The `.app` bundle root above an executable, if there is one.
///
/// Mirrors tauri-plugin-updater's own `extract_path_from_executable`, which
/// walks up from `Contents/MacOS/<bin>` — this must agree with it or we
/// would judge one path and it would replace another.
pub fn macos_bundle_root(executable: &Path) -> Option<PathBuf> {
    let mut cursor = executable;
    while let Some(parent) = cursor.parent() {
        if parent
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.ends_with(".app"))
        {
            return Some(parent.to_path_buf());
        }
        cursor = parent;
    }
    None
}

/// True when the path is macOS's read-only translocated mount.
///
/// Matched on the path segment Apple actually uses. There is no API that
/// answers this directly without private frameworks, and the segment has
/// been stable since macOS 10.12.
pub fn is_translocated(path: &Path) -> bool {
    path.components().any(|component| {
        component
            .as_os_str()
            .to_str()
            .is_some_and(|part| part == "AppTranslocation")
    })
}

/// True when the bundle sits inside a Cargo build output.
///
/// Requires BOTH a `target` component and a `debug`/`release` component
/// under it, because `target` alone is an ordinary word a customer could
/// have in a folder name, and condemning a real installation is the
/// expensive direction of this mistake.
pub fn is_cargo_build_output(bundle: &Path) -> bool {
    let parts: Vec<&str> = bundle
        .components()
        .filter_map(|component| component.as_os_str().to_str())
        .collect();
    parts
        .iter()
        .position(|part| *part == "target")
        .is_some_and(|index| {
            parts[index + 1..]
                .iter()
                .any(|part| *part == "debug" || *part == "release")
        })
}

/// The whole rule, with every impure fact already gathered.
///
/// ORDER IS LOAD-BEARING. Translocation is checked before anything else
/// because a translocated bundle's path tells you nothing true about where
/// the app lives — asking "is its parent writable" of a temporary mount
/// answers a question about the wrong directory.
pub fn classify_site(
    executable: &Path,
    bundle: Option<PathBuf>,
    parent_is_writable: bool,
) -> UpdateSite {
    if is_translocated(executable) {
        return UpdateSite::Translocated;
    }
    let Some(bundle) = bundle else {
        return UpdateSite::NotAnInstalledApp {
            executable: executable.to_path_buf(),
        };
    };
    if is_cargo_build_output(&bundle) {
        return UpdateSite::BuildOutput { bundle };
    }
    if !parent_is_writable {
        return UpdateSite::ParentNotWritable { bundle };
    }
    UpdateSite::Installable { bundle }
}

/// Can this process create and remove a file in `dir`?
///
/// Probed by DOING IT, never by reading permission bits: the bits do not
/// account for ACLs, an immutable flag, a read-only mount, or macOS's own
/// TCC, and every one of those is a real way `/Applications` says no on a
/// managed Mac. A wrong "yes" here spends a download and reports success on
/// an update that silently did not happen.
pub fn directory_is_writable(dir: &Path) -> bool {
    let probe = dir.join(format!(
        ".empyralis-update-probe-{}",
        std::process::id()
    ));
    match std::fs::write(&probe, b"") {
        Ok(()) => {
            let _ = std::fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

/// Classifies the copy of the app that is running right now.
pub fn resolve_site() -> UpdateSite {
    let Ok(executable) = std::env::current_exe() else {
        // Cannot even name ourselves. "Unknown" must never become a refusal
        // that takes updates away — same posture as the gateway's own
        // launch-updatability check, which resolves every unreadable state
        // to "unknown" rather than "not_updatable". Here the honest
        // equivalent is to let the attempt proceed: the updater itself will
        // fail loudly and truthfully if the path is unusable.
        return UpdateSite::Installable {
            bundle: PathBuf::new(),
        };
    };

    #[cfg(target_os = "macos")]
    let bundle = macos_bundle_root(&executable);
    // On Linux the updater replaces the AppImage file itself, so the
    // "bundle" is that file and its parent is what must be writable.
    #[cfg(not(target_os = "macos"))]
    let bundle = std::env::var("APPIMAGE")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(PathBuf::from);

    let parent_is_writable = bundle
        .as_deref()
        .and_then(Path::parent)
        .map(directory_is_writable)
        .unwrap_or(false);

    classify_site(&executable, bundle, parent_is_writable)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn path(value: &str) -> PathBuf {
        PathBuf::from(value)
    }

    #[test]
    fn the_founders_own_mac_is_recognised_as_a_build_output() {
        // Verbatim from ~/Library/LaunchAgents/ai.empyralis.agent-computer.plist
        // on 2026-08-22 — the state that prompted this whole change.
        let exe = path(
            "/Users/mansur/empyralis/src-tauri/target/release/bundle/macos/Empyralis.app/Contents/MacOS/empyralis-tauri-shell",
        );
        let bundle = macos_bundle_root(&exe).expect("the .app is in that path");
        assert_eq!(
            classify_site(&exe, Some(bundle.clone()), true),
            UpdateSite::BuildOutput { bundle }
        );
    }

    #[test]
    fn an_app_in_applications_can_install() {
        let exe = path("/Applications/Empyralis.app/Contents/MacOS/empyralis-tauri-shell");
        let bundle = macos_bundle_root(&exe).unwrap();
        assert_eq!(bundle, path("/Applications/Empyralis.app"));
        assert!(classify_site(&exe, Some(bundle), true).can_install());
    }

    #[test]
    fn translocation_wins_over_every_other_reading_of_the_path() {
        // The bundle name and its parent both look perfectly ordinary here.
        // Only the mount point says it is read-only, so a check that asked
        // about the parent first would call this installable and then fail.
        let exe = path(
            "/private/var/folders/9k/xx/T/AppTranslocation/1B2C/d/Empyralis.app/Contents/MacOS/empyralis-tauri-shell",
        );
        let bundle = macos_bundle_root(&exe);
        assert_eq!(classify_site(&exe, bundle, true), UpdateSite::Translocated);
    }

    #[test]
    fn a_writable_check_that_says_no_is_not_reported_as_up_to_date() {
        let exe = path("/Volumes/Empyralis/Empyralis.app/Contents/MacOS/empyralis-tauri-shell");
        let bundle = macos_bundle_root(&exe).unwrap();
        let site = classify_site(&exe, Some(bundle.clone()), false);
        assert_eq!(site, UpdateSite::ParentNotWritable { bundle });
        assert!(!site.can_install());
        assert!(site.action().is_some());
    }

    #[test]
    fn a_bare_binary_is_not_an_installed_app() {
        let exe = path("/tmp/empyralis-tauri-shell");
        assert_eq!(
            classify_site(&exe, None, true),
            UpdateSite::NotAnInstalledApp { executable: exe }
        );
    }

    #[test]
    fn the_word_target_alone_never_condemns_a_real_installation() {
        // A customer folder called "target" is not a Cargo build tree. Only
        // target/{debug,release} is, and getting this wrong takes updates
        // away from a machine that was fine.
        assert!(!is_cargo_build_output(&path("/Users/x/target/Empyralis.app")));
        assert!(!is_cargo_build_output(&path("/Users/x/release/Empyralis.app")));
        assert!(is_cargo_build_output(&path("/x/target/release/bundle/macos/Empyralis.app")));
        assert!(is_cargo_build_output(&path("/x/target/debug/bundle/macos/Empyralis.app")));
    }

    #[test]
    fn every_refusal_names_itself_and_offers_exactly_one_way_out() {
        let refusals = [
            UpdateSite::BuildOutput { bundle: path("/x/target/release/bundle/macos/E.app") },
            UpdateSite::Translocated,
            UpdateSite::ParentNotWritable { bundle: path("/Volumes/E/E.app") },
            UpdateSite::NotAnInstalledApp { executable: path("/tmp/e") },
        ];
        let mut codes = Vec::new();
        for refusal in &refusals {
            assert!(!refusal.can_install());
            assert!(!refusal.detail().trim().is_empty(), "{refusal:?} says nothing");
            assert!(refusal.action().is_some(), "{refusal:?} offers no way out");
            codes.push(refusal.code());
        }
        // Distinct codes, because these are distinct facts — collapsing two
        // of them sends a person to fix the wrong thing.
        let unique: std::collections::HashSet<_> = codes.iter().collect();
        assert_eq!(unique.len(), codes.len());
        assert!(UpdateSite::Installable { bundle: path("/Applications/E.app") }.action().is_none());
    }

    #[test]
    fn a_real_temp_directory_probes_writable_and_a_missing_one_does_not() {
        let dir = std::env::temp_dir();
        assert!(directory_is_writable(&dir));
        assert!(!directory_is_writable(&dir.join("empyralis-no-such-dir-8f2a1c")));
    }

    #[test]
    fn the_first_check_waits_for_launch_to_finish_and_the_interval_bounds_a_working_day() {
        // Pinned so a future edit has to argue with the reasoning above
        // rather than nudge a number.
        assert!(FIRST_CHECK_DELAY >= Duration::from_secs(30));
        assert!(FIRST_CHECK_DELAY <= Duration::from_secs(5 * 60));
        assert!(CHECK_INTERVAL >= Duration::from_secs(60 * 60));
        assert!(CHECK_INTERVAL <= Duration::from_secs(12 * 60 * 60));
    }
}
