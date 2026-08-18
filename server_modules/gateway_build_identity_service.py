"""Is this gateway's build distinguishable, and is an update to it OBSERVABLE?

Pure decision layer sitting under gateway_self_update_service.gateway_update_
status(). No I/O, no database, no network — every input is already carried on
the registration row the caller holds, so this is cheap enough to run inside
the Hardware page's existing gateway-list fetch and testable without a stack.

WHY THIS MODULE EXISTS — MAN-331 proposes "set EMPYRALIS_GATEWAY_LATEST_
VERSION in production", and that fix, applied literally, breaks the fleet it
is meant to heal. The reasoning is short and was verified against the code
and against production, not inferred:

    GATEWAY_VERSION = "0.1.0"     index.ts:41. `git log -S` over the entire
                                  repo returns ONE commit — the one that
                                  introduced it. Never bumped. Not by the
                                  release workflow either; nothing stamps it.
    publish channel = "latest"    release-gateway-linux.yml:93. A push to
                                  main publishes to agent-computer/latest/.

    is_newer("0.1.0", "latest") -> False    (malformed -> "cannot tell")
    is_newer("0.1.0", "0.2.0")  -> True

So there are exactly two ways to set that variable and both are wrong:

    set it to "latest"  -> never fires. Dormant, exactly as today, but now
                           looking configured. MAN-331 reads as fixed and
                           nothing has changed.
    set it to "0.2.0"   -> fires on every box. The update installs the
                           `latest` tarball, whose GATEWAY_VERSION is still
                           the hardcoded "0.1.0". The box reconnects still
                           reporting 0.1.0. update_available is STILL true.
                           It fires again. And again — on every status poll,
                           on every box, forever, each one a full tarball
                           download and a process restart.

    0.1.0 ──update──▶ 0.1.0 ──update──▶ 0.1.0 ──update──▶ ...
      └─ update_available never clears, because nothing it changes is
         anything the box reports.

A self-update loop across every customer's VPS is far worse than the stale
box it was meant to cure, and it is UNRECOVERABLE by the customer: the boxes
nobody can SSH into are exactly the ones that would be restarting themselves
in a loop. So the rule this module enforces is narrow and load-bearing:

    NEVER ADVERTISE AN UPDATE WHOSE SUCCESS COULD NOT BE OBSERVED.

If a completed update would leave the box reporting the identical build
identity it reports now, then "it worked" and "it did nothing" are the same
observation — and per this codebase's own standing law, two different facts
may never share one signal. The honest move is to refuse and say why, which
is what every branch below does.

The fingerprint (empyralis-gateway/src/update/gateway-build-fingerprint.ts)
is what makes an update observable at all: it is derived from the build's
own bytes, so it changes on every real build with nobody remembering to bump
anything — the exact property "0.1.0" lacks.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# Mirrors gateway_self_update_service._version_parts()'s notion of "a version
# string this system can actually compare". Kept as its own predicate here
# rather than imported so that this module stays free of that module's
# dispatch machinery (it imports gateway_execution_service, which reaches the
# network); the two are asserted consistent in
# test_gateway_build_identity_service.py rather than trusted to stay in sync.
def is_comparable_version(value: str) -> bool:
    cleaned = str(value or "").strip()
    if cleaned.lower().startswith("v"):
        cleaned = cleaned[1:]
    if not cleaned:
        return False
    for segment in cleaned.split("."):
        if not any(ch.isdigit() for ch in segment):
            return False
    return True


# Refusal codes. Each names a DIFFERENT fact — they are never collapsed into
# one "no update available", because the operator action that resolves each
# one is different, and a shared message would send someone to fix the wrong
# thing. Stable codes, never matched on prose (this codebase has been bitten
# by string matching before).
REFUSAL_NO_PUBLISHED_BUILD = "no_published_build"
REFUSAL_UNCOMPARABLE_PUBLISHED_VERSION = "uncomparable_published_version"
REFUSAL_UPDATE_WOULD_BE_UNOBSERVABLE = "update_would_be_unobservable"
REFUSAL_PREVIOUS_UPDATE_CHANGED_NOTHING = "previous_update_changed_nothing"
REFUSAL_LAUNCH_PATH_NOT_UPDATABLE = "launch_path_not_updatable"

_REFUSAL_REASONS: Dict[str, str] = {
    REFUSAL_NO_PUBLISHED_BUILD: (
        "No published gateway build is configured, so there is nothing to compare this "
        "computer against."
    ),
    REFUSAL_UNCOMPARABLE_PUBLISHED_VERSION: (
        "The configured published gateway version is not a version number this system can "
        "compare, so it can never be recognised as newer than what this computer is running."
    ),
    REFUSAL_UPDATE_WOULD_BE_UNOBSERVABLE: (
        "This computer reports no build fingerprint, and every build reports the same version "
        "number, so a finished update could not be told apart from one that did nothing. "
        "Updating on that basis would repeat forever."
    ),
    REFUSAL_PREVIOUS_UPDATE_CHANGED_NOTHING: (
        "This computer already installed the published build and came back running exactly the "
        "same code, so installing it again would change nothing."
    ),
    REFUSAL_LAUNCH_PATH_NOT_UPDATABLE: (
        "This computer is set up to start its gateway from a fixed location that updates are "
        "never installed into, so an update would install correctly and then start the old copy "
        "again. Whoever set this computer up has to point it at the update location once; it "
        "cannot change that itself."
    ),
}

# The gateway's own word for "an update here could never take effect" — see
# empyralis-gateway/src/update/gateway-launch-updatability.ts, which computes
# it from the supervisor unit that starts the NEXT process rather than from
# the path this one happens to be running from.
#
# Only this exact value ever refuses. "unknown", a missing field and a build
# too old to report one all mean "keep today's behaviour": a wrong "unknown"
# costs a signal, a wrong refusal takes updates away from a healthy box, and
# most of the fleet reports nothing here at all until it is rebuilt.
LAUNCH_STATUS_NOT_UPDATABLE = "not_updatable"
LAUNCH_STATUS_UPDATABLE = "updatable"
LAUNCH_STATUS_UNKNOWN = "unknown"


def build_identity(registration: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """The build a gateway is actually running, as reported on its last connect.

    Reads registration.metadata rather than the ephemeral session row, for the
    same reason gateway_self_update_service.gateway_update_status() does: it has
    to survive a disconnect, since a box that has gone dark is exactly the one
    whose build identity someone wants to look up.
    """
    metadata = registration.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    version = str(metadata.get("gateway_version") or "").strip() or None
    fingerprint = str(metadata.get("gateway_build_fingerprint") or "").strip() or None
    return {"gateway_version": version, "gateway_build_fingerprint": fingerprint}


def plan_gateway_update_advertisement(
    *,
    current_version: Optional[str],
    latest_version: Optional[str],
    current_fingerprint: Optional[str] = None,
    published_fingerprint: Optional[str] = None,
    last_update_fingerprint: Optional[str] = None,
    version_is_newer: bool = False,
    launch_updatability: Optional[str] = None,
) -> Dict[str, Any]:
    """Decide whether an update may be advertised, and if not, say which fact stopped it.

    `last_update_fingerprint` is the build fingerprint this gateway reported
    the last time a self-update was dispatched to it and it came back. It is
    the loop brake, and it is the only input that can catch an unobservable
    update AFTER the fact rather than before: if the box reconnected still
    running that same build, the update demonstrably did nothing, and the
    honest response is to stop rather than to try the identical thing again.

    `version_is_newer` is supplied by the caller (which owns the comparison
    already) rather than recomputed here, so there is exactly one version
    comparison in the system instead of two that can disagree.
    """
    current_version = str(current_version or "").strip() or None
    latest_version = str(latest_version or "").strip() or None
    current_fingerprint = str(current_fingerprint or "").strip() or None
    published_fingerprint = str(published_fingerprint or "").strip() or None
    last_update_fingerprint = str(last_update_fingerprint or "").strip() or None

    def refuse(code: str) -> Dict[str, Any]:
        return {
            "update_available": False,
            "refusal_code": code,
            "reason": _REFUSAL_REASONS[code],
        }

    # AHEAD OF EVERYTHING, including the loop brake. A box whose supervisor
    # unit points outside the release layout cannot be changed by an update at
    # all, so every other question about it is downstream of this one — and
    # `previous_update_changed_nothing`, which is what such a box eventually
    # reports, is the SYMPTOM. Naming the symptom first would send an operator
    # to look at build fingerprints when the fix is one line in a service
    # definition.
    if str(launch_updatability or "").strip() == LAUNCH_STATUS_NOT_UPDATABLE:
        return refuse(REFUSAL_LAUNCH_PATH_NOT_UPDATABLE)

    if not latest_version and not published_fingerprint:
        return refuse(REFUSAL_NO_PUBLISHED_BUILD)

    # The loop brake comes FIRST, ahead of every "is it newer" question. A box
    # that already installed this exact build and came back unchanged must not
    # be re-offered it no matter how the version numbers compare — that
    # ordering is the whole difference between a self-healing fleet and a
    # restart loop nobody can reach in to stop.
    if (
        last_update_fingerprint
        and current_fingerprint
        and last_update_fingerprint == current_fingerprint
    ):
        return refuse(REFUSAL_PREVIOUS_UPDATE_CHANGED_NOTHING)

    # A fingerprint on both sides is the strong signal and outranks the
    # version entirely: it is derived from the build's own bytes, so it cannot
    # be stale in the way an authored constant can. Equal fingerprints mean
    # the box is already running the published build, whatever either side
    # calls itself.
    if current_fingerprint and published_fingerprint:
        if current_fingerprint == published_fingerprint:
            return {"update_available": False, "refusal_code": None, "reason": ""}
        return {"update_available": True, "refusal_code": None, "reason": ""}

    # No fingerprint to go on — fall back to versions, which is where every
    # unsafe case lives.
    if not latest_version:
        return refuse(REFUSAL_NO_PUBLISHED_BUILD)

    if not is_comparable_version(latest_version):
        # e.g. the literal "latest", which is what the release workflow
        # actually publishes under. Today this silently reads as "no update";
        # naming it means an operator who sets the variable to the channel
        # name finds out, instead of concluding the feature works.
        return refuse(REFUSAL_UNCOMPARABLE_PUBLISHED_VERSION)

    if not version_is_newer:
        return {"update_available": False, "refusal_code": None, "reason": ""}

    # The version says newer, and there is no fingerprint on either side to
    # confirm it. This is the MAN-331 trap exactly: the published build's
    # version is hardcoded, so the box will come back reporting whatever it
    # reports now, and nothing will ever clear this flag. Refuse — a stale box
    # is a bad state, and a box that reinstalls the same build forever is a
    # worse one.
    if not current_fingerprint:
        return refuse(REFUSAL_UPDATE_WOULD_BE_UNOBSERVABLE)

    # The box reports a fingerprint but we have no published one to compare
    # against. The update is still observable — if it changes anything, the
    # fingerprint moves, and the loop brake above catches it if it does not.
    return {"update_available": True, "refusal_code": None, "reason": ""}


def launch_report(registration: Dict[str, Any]) -> Dict[str, Any]:
    """What this gateway last said about whether an update could reach it.

    Same source as build_identity(): registration.metadata, not the ephemeral
    session row — a box that has gone dark is exactly the one someone wants to
    look this up for.

    A build that predates the check reports nothing, which reads as UNKNOWN
    rather than as a problem. That direction is load-bearing: every box in the
    fleet reports nothing here until it is rebuilt, and refusing them all would
    be a far worse bug than the stale box this exists to surface.
    """
    metadata = registration.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    report = metadata.get("gateway_launch_updatability")
    if not isinstance(report, dict):
        return {"status": LAUNCH_STATUS_UNKNOWN, "reported": False}
    status = str(report.get("status") or "").strip() or LAUNCH_STATUS_UNKNOWN
    if status not in (
        LAUNCH_STATUS_UPDATABLE,
        LAUNCH_STATUS_NOT_UPDATABLE,
        LAUNCH_STATUS_UNKNOWN,
    ):
        status = LAUNCH_STATUS_UNKNOWN
    blockers = report.get("blockers")
    if not isinstance(blockers, list):
        blockers = []
    repair = report.get("repair")
    if not isinstance(repair, dict):
        repair = {}
    return {
        "status": status,
        "reported": True,
        "supervisor": str(report.get("supervisor") or "").strip() or None,
        "unit_path": str(report.get("unitPath") or "").strip() or None,
        "launch_command": str(report.get("launchCommand") or "").strip() or None,
        "expected_entrypoint": str(report.get("expectedEntrypoint") or "").strip() or None,
        "blockers": [
            {
                "code": str(entry.get("code") or "").strip(),
                "detail": str(entry.get("detail") or "").strip(),
            }
            for entry in blockers
            if isinstance(entry, dict)
        ],
        "repair_launcher_path": str(repair.get("launcherPath") or "").strip() or None,
        "repair_exec_start_line": str(repair.get("execStartLine") or "").strip() or None,
        "repair_verified": bool(repair.get("verified")),
        "repair_failure_reason": str(repair.get("failureReason") or "").strip() or None,
    }


def plan_gateway_launch_repair(report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The exact one-time change a human has to make, or an honest refusal to
    hand them one.

    Three states, never collapsed, because they send a person to three
    different places:

        ready        the gateway wrote a launcher AND proved it runs. Here is
                     the line, the reload and the restart.
        unverified   the gateway could not prove its own launcher runs. Saying
                     "put this in your ExecStart" anyway is how a stale box
                     becomes a dead one, so the instruction is withheld and
                     the reason is named instead.
        None         nothing is wrong, or nothing is known.

    The commands are shown rather than performed, and that is not a shortcut:
    the gateway runs unprivileged, inside a mount namespace where `/` is
    read-only, with NoNewPrivileges set — all three measured on production
    rather than assumed. There is no version of this it can do for itself, and
    a button that cannot work is a dead control.
    """
    if str(report.get("status") or "") != LAUNCH_STATUS_NOT_UPDATABLE:
        return None
    unit_path = report.get("unit_path")
    exec_start_line = report.get("repair_exec_start_line")
    if not report.get("repair_verified") or not exec_start_line or not unit_path:
        detail = "This computer could not prepare its own repair"
        if report.get("repair_failure_reason"):
            detail += f" — {report['repair_failure_reason']}"
        detail += ". Whoever set it up will have to look at it directly."
        return {
            "state": "unverified",
            "unit_path": unit_path,
            "current_launch_command": report.get("launch_command"),
            "blockers": report.get("blockers") or [],
            "detail": detail,
        }
    unit_name = str(unit_path).rsplit("/", 1)[-1]
    launcher_path = str(exec_start_line).split("=", 1)[-1].strip()
    if str(report.get("supervisor") or "") == "launchd":
        commands = [
            f"/usr/bin/plutil -replace ProgramArguments -json '[\"/bin/sh\", \"{launcher_path}\"]' {unit_path}",
            f"/usr/bin/plutil -replace KeepAlive -bool true {unit_path}",
            f"launchctl bootout gui/$(id -u)/{unit_name.replace('.plist', '')} || true",
            f"launchctl bootstrap gui/$(id -u) {unit_path}",
        ]
    else:
        # A systemd DROP-IN, never `sed` over the shipped unit. Three reasons,
        # each of which has bitten something in this repo before: the empty
        # `ExecStart=` is systemd's own documented way to reset a list, so it
        # works whether the unit has one ExecStart or several; `Restart=always`
        # is ADDED rather than substituted, so a unit with no `Restart=` line
        # at all (systemd's default is `no`) is fixed too, which a substitution
        # would silently miss; and reverting is deleting one file rather than
        # reconstructing a line from memory on a box nobody can reach.
        #
        # Restart=always belongs in the same edit, not a later one: the clean
        # exit an update ends with only brings the box back under `always`, and
        # production carries `on-failure`.
        drop_in_dir = f"/etc/systemd/system/{unit_name}.d"
        drop_in_path = f"{drop_in_dir}/empyralis-updatable.conf"
        commands = [
            f"sudo mkdir -p {drop_in_dir}",
            f"sudo tee {drop_in_path} >/dev/null <<'EOF'\n"
            "[Service]\n"
            "ExecStart=\n"
            f"{exec_start_line}\n"
            "Restart=always\n"
            "EOF",
            "sudo systemctl daemon-reload",
            f"sudo systemctl restart {unit_name}",
        ]
    return {
        "state": "ready",
        "unit_path": unit_path,
        "unit_name": unit_name,
        "current_launch_command": report.get("launch_command"),
        "exec_start_line": exec_start_line,
        "launcher_path": report.get("repair_launcher_path"),
        "blockers": report.get("blockers") or [],
        "commands": commands,
        "detail": (
            "Run these once on this computer, as someone with administrator access. "
            "Nothing has to be done again afterwards — the new start-up command follows "
            "every future update on its own."
        ),
    }
