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
}


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
