"""Proof that `_emit_gateway_risk_decision` (server_modules/routes_gateway.py)
handles the dict-shaped `risk_decision` without raising, and that a genuine
failure in either audit sink now logs loudly rather than vanishing.

Before this fix, `_emit_gateway_risk_decision` already branched on the shape
of `risk_decision` at the top (dict vs. an object with `.as_dict()`) to
build `payload`/`decision_label` -- but the two `try` blocks below that
branch still referenced `risk_decision.decision` / `risk_decision.capability`
directly, unconditionally, instead of the already-shape-normalized
`decision_label` variable. A plain `dict` has no `.decision` attribute, so
every dict-shaped call (exactly the branch
`_gateway_tool_call`/`_gateway_dispatch_tool` -- server_modules/routes_
gateway.py's own call site around `ug_decision.risk_decision if isinstance
(ug_decision.risk_decision, dict) else {}` -- takes) raised AttributeError
inside the `try`, which the bare `except Exception` swallowed, dropping the
whole risk-decision audit write without ever surfacing as a request
failure. This file proves the fixed function:

1. Handles a dict-shaped `risk_decision` without raising, and forwards the
   dict's own `decision`/`capability` values into both audit sinks
   (`security_audit_service.emit_security_audit_event` and
   `gateway_state_repository.record_gateway_event`).
2. Still handles the original object-shaped `risk_decision` (the
   `CapabilityRiskDecision`-like dataclass `classify_gateway_browser_action_
   risk` returns) the same way it always did -- a regression guard, since
   the fix touches every reference to `.decision`/`.capability` in the
   function.
3. Logs (not swallows) when the underlying audit call genuinely fails --
   the "log rather than swallow silently" half of the fix -- by forcing the
   `security_audit_service` call to raise and asserting a WARNING is
   emitted with the exception attached, rather than nothing at all.
4. Still returns quietly for an unrecognized shape (neither dict nor an
   object with `.as_dict()`), but now logs why, instead of failing closed
   in total silence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from unittest.mock import patch

from server_modules import routes_gateway
from server_modules.capability_risk_classifier_service import DECISION_ALLOW, DECISION_BLOCK


@dataclass(frozen=True)
class _FakeCapabilityRiskDecision:
    """Minimal stand-in for capability_risk_classifier_service.
    CapabilityRiskDecision -- the object-shaped branch
    `_emit_gateway_risk_decision` has always had to support (the two
    browser-action call sites at server_modules/routes_gateway.py's
    `start_gateway_browser_session` / `execute_gateway_browser_action`)."""

    decision: str
    capability: str = "browser.start"

    def as_dict(self):
        return {"decision": self.decision, "capability": self.capability}


def test_dict_shaped_risk_decision_does_not_raise_and_is_audited():
    """The exact shape the unified-gate gateway-tool call site passes:
    `ug_decision.risk_decision if isinstance(..., dict) else {}` -- a plain
    dict with a "decision"/"capability" key, never an object."""
    risk_decision = {"decision": "allow", "capability": "shell.execute", "risk_level": 2}

    with patch.object(routes_gateway.security_audit_service, "emit_security_audit_event") as audit_mock, \
         patch.object(routes_gateway.gateway_state_repository, "record_gateway_event") as event_mock:
        routes_gateway._emit_gateway_risk_decision(
            gateway_id="gw-1",
            workspace_id="workspace-1",
            tenant_id="tenant-1",
            risk_decision=risk_decision,
        )

    assert audit_mock.called, "emit_security_audit_event was never called for a dict-shaped risk_decision."
    audit_kwargs = audit_mock.call_args.kwargs
    assert audit_kwargs["action"] == "gateway.risk_decision.allow"
    assert audit_kwargs["status"] == "logged"
    assert "allow" in audit_kwargs["detail"]
    assert "shell.execute" in audit_kwargs["detail"]
    assert audit_kwargs["metadata"]["risk_decision"] == risk_decision

    assert event_mock.called, "record_gateway_event was never called for a dict-shaped risk_decision."
    event_kwargs = event_mock.call_args.kwargs
    assert event_kwargs["message_type"] == "gateway.risk_decision.allow"
    assert event_kwargs["payload"]["risk_decision"] == risk_decision


def test_dict_shaped_blocked_decision_reports_blocked_status():
    risk_decision = {"decision": DECISION_BLOCK, "capability": "filesystem.write"}

    with patch.object(routes_gateway.security_audit_service, "emit_security_audit_event") as audit_mock, \
         patch.object(routes_gateway.gateway_state_repository, "record_gateway_event"):
        routes_gateway._emit_gateway_risk_decision(
            gateway_id="gw-1",
            workspace_id="workspace-1",
            tenant_id="tenant-1",
            risk_decision=risk_decision,
        )

    assert audit_mock.call_args.kwargs["status"] == "blocked"


def test_empty_dict_risk_decision_does_not_raise():
    """The degenerate case the gateway-tool call site produces when the
    unified gate's own `risk_decision` field isn't a dict at all
    (`... else {}`) -- must still not raise, just audit as "unknown"."""
    with patch.object(routes_gateway.security_audit_service, "emit_security_audit_event") as audit_mock, \
         patch.object(routes_gateway.gateway_state_repository, "record_gateway_event"):
        routes_gateway._emit_gateway_risk_decision(
            gateway_id="gw-1",
            workspace_id="workspace-1",
            tenant_id="tenant-1",
            risk_decision={},
        )
    assert audit_mock.call_args.kwargs["action"] == "gateway.risk_decision.unknown"


def test_object_shaped_risk_decision_still_works():
    """Regression guard: the original CapabilityRiskDecision-shaped branch
    (an object with `.as_dict()` and a `.decision` attribute) must keep
    working exactly as before -- the fix touches every `.decision`/
    `.capability` reference in this function, so this is the check that
    none of them silently stopped reading the object shape correctly."""
    risk_decision = _FakeCapabilityRiskDecision(decision=DECISION_ALLOW, capability="browser.start")

    with patch.object(routes_gateway.security_audit_service, "emit_security_audit_event") as audit_mock, \
         patch.object(routes_gateway.gateway_state_repository, "record_gateway_event") as event_mock:
        routes_gateway._emit_gateway_risk_decision(
            gateway_id="gw-1",
            workspace_id="workspace-1",
            tenant_id="tenant-1",
            risk_decision=risk_decision,
        )

    assert audit_mock.call_args.kwargs["action"] == f"gateway.risk_decision.{DECISION_ALLOW}"
    assert audit_mock.call_args.kwargs["status"] == "logged"
    assert audit_mock.call_args.kwargs["metadata"]["risk_decision"] == risk_decision.as_dict()
    assert event_mock.call_args.kwargs["message_type"] == f"gateway.risk_decision.{DECISION_ALLOW}"


def test_object_shaped_blocked_decision_reports_blocked_status():
    risk_decision = _FakeCapabilityRiskDecision(decision=DECISION_BLOCK, capability="shell.execute")

    with patch.object(routes_gateway.security_audit_service, "emit_security_audit_event") as audit_mock, \
         patch.object(routes_gateway.gateway_state_repository, "record_gateway_event"):
        routes_gateway._emit_gateway_risk_decision(
            gateway_id="gw-1",
            workspace_id="workspace-1",
            tenant_id="tenant-1",
            risk_decision=risk_decision,
        )

    assert audit_mock.call_args.kwargs["status"] == "blocked"


def test_audit_sink_failure_logs_loudly_instead_of_vanishing(caplog):
    """The other half of the fix: when the audit call itself genuinely
    fails (a real infra problem, not the shape bug), the failure must be
    logged with the exception attached, not swallowed into nothing. Forces
    `emit_security_audit_event` to raise and asserts a WARNING with
    exc_info lands in the gateway route logger."""
    risk_decision = {"decision": "allow", "capability": "shell.execute"}

    with caplog.at_level(logging.WARNING, logger=routes_gateway.LOGGER.name):
        with patch.object(
            routes_gateway.security_audit_service,
            "emit_security_audit_event",
            side_effect=RuntimeError("audit sink unavailable"),
        ), patch.object(routes_gateway.gateway_state_repository, "record_gateway_event"):
            routes_gateway._emit_gateway_risk_decision(
                gateway_id="gw-1",
                workspace_id="workspace-1",
                tenant_id="tenant-1",
                risk_decision=risk_decision,
            )

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records, "No warning was logged when the audit sink raised -- the failure vanished silently."
    matching = [r for r in warning_records if "risk decision audit" in r.message.lower()]
    assert matching, f"No warning referenced the risk decision audit failure. Got: {[r.message for r in warning_records]}"
    assert matching[0].exc_info is not None, "The warning did not attach exception info for the audit failure."


def test_gateway_event_sink_failure_also_logs_loudly(caplog):
    """Same proof for the second audit sink (`gateway_state_repository.
    record_gateway_event`) -- the two try/except blocks are independent,
    so each needs its own regression guard."""
    risk_decision = {"decision": "allow", "capability": "shell.execute"}

    with caplog.at_level(logging.WARNING, logger=routes_gateway.LOGGER.name):
        with patch.object(routes_gateway.security_audit_service, "emit_security_audit_event"), \
             patch.object(
                 routes_gateway.gateway_state_repository,
                 "record_gateway_event",
                 side_effect=RuntimeError("event store unavailable"),
             ):
            routes_gateway._emit_gateway_risk_decision(
                gateway_id="gw-1",
                workspace_id="workspace-1",
                tenant_id="tenant-1",
                risk_decision=risk_decision,
            )

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    matching = [r for r in warning_records if "risk decision event" in r.message.lower()]
    assert matching, f"No warning referenced the gateway event failure. Got: {[r.message for r in warning_records]}"
    assert matching[0].exc_info is not None


def test_unrecognized_shape_logs_and_skips_without_raising(caplog):
    """Neither a dict nor an object with `.as_dict()` -- must not raise,
    and (new behavior) must say why it's skipping instead of failing in
    total silence."""
    with caplog.at_level(logging.WARNING, logger=routes_gateway.LOGGER.name):
        with patch.object(routes_gateway.security_audit_service, "emit_security_audit_event") as audit_mock, \
             patch.object(routes_gateway.gateway_state_repository, "record_gateway_event") as event_mock:
            routes_gateway._emit_gateway_risk_decision(
                gateway_id="gw-1",
                workspace_id="workspace-1",
                tenant_id="tenant-1",
                risk_decision=object(),
            )

    assert not audit_mock.called
    assert not event_mock.called
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records, "Unrecognized risk_decision shape was skipped with no explanation logged."
