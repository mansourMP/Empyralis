"""Regression coverage for the raw-readiness-code leak found in the
2026-08-13 launch-readiness audit.

`gateway_execution_service.gateway_registration_execution_readiness` fails
closed with a correct, SPECIFIC internal token (`gateway_capability_missing`,
`gateway_offline`, ...) and `execute_tool_via_gateway` raises that token
VERBATIM as `ValueError(readiness_reason)`. `openclaw_provisioning_service`
and `openclaw_channel_setup_service` each caught that exception and wrapped
`str(exc)` UNCHANGED into `OpenClawProvisioningError`, which a route turns
straight into an HTTP `detail` string — so a customer clicking "Set up N
channels" or saving a channel credential on a box that has never had the
transport installed saw the literal text `gateway_capability_missing` in the
error banner. No explanation, no next step, indistinguishable from a bug
report.

`gateway_reason_messages.py` (MAN-295) already exists to translate every one
of these tokens into a real sentence — it was simply never called on this
seam. This module proves three things:

1. Every reason token `gateway_registration_execution_readiness` can
   actually produce (the PRODUCER) has a message
   `gateway_reason_messages` (a SEPARATE source) actually recognizes — a
   drift test, not a hand-copied list, so a 10th reason added later fails
   loudly instead of leaking. CLAUDE.md's own rule: the expected set and the
   actual set must come from different sources.
2. `openclaw_provisioning_service.provision_openclaw_gateway` and
   `openclaw_channel_setup_service.read_channel_setup_state` /
   `write_channel_credential` all humanize the caught reason before it
   becomes a customer-facing message, and `gateway_capability_missing` does
   not collapse onto the same sentence as `gateway_offline` — the box is
   reachable; the transport was simply never installed, which is a
   different fact and (per the founder's own instruction) offers no button
   that could ever fix it from the browser.
3. A ValueError that is NOT one of the known internal tokens (i.e. already
   human prose, like the sentences `_require_active_gateway_registration`
   raises directly) passes through unchanged rather than being mangled into
   the generic fallback — the fix must only intercept the raw-token leak,
   not every exception this seam can raise.
"""
from __future__ import annotations

import inspect
import re
import unittest
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

from server_modules import (
    gateway_execution_service,
    gateway_reason_messages,
    openclaw_channel_setup_service,
    openclaw_provisioning_service,
    personal_channels_service,
)
from server_modules.openclaw_provisioning_service import OpenClawProvisioningError


class ReadinessReasonEnumerationDriftTests(unittest.TestCase):
    """The expected set (source-scanned from the PRODUCER) and the actual set
    (gateway_reason_messages's own known-token registry) must come from
    different sources — a hand-copied list of today's tokens would only ever
    confirm itself."""

    def test_every_producer_reason_token_has_a_known_message(self) -> None:
        source = inspect.getsource(gateway_execution_service.gateway_registration_execution_readiness)
        tokens = set(re.findall(r'return False,\s*"([a-z_]+)"', source))
        # Guards the extraction itself: if this regex stops matching (the
        # producer's shape changed), the test must fail loudly rather than
        # silently enumerate zero tokens and pass vacuously.
        self.assertGreaterEqual(
            len(tokens),
            8,
            f"source-scan extracted too few tokens ({sorted(tokens)}) — "
            "the regex likely no longer matches gateway_registration_execution_readiness's shape",
        )
        missing = tokens - gateway_reason_messages.KNOWN_REASON_TOKENS
        self.assertEqual(
            missing,
            set(),
            f"gateway_reason_messages.py has no message for reason token(s): {sorted(missing)} "
            "— a customer hitting one of these will see the raw internal token verbatim.",
        )


def _run(coro):
    import asyncio

    return asyncio.run(coro)


async def _fake_load_dm(*, tenant_id, workspace_id, agent_id, channel_key):
    return {"mode": "open", "allowlist": [], "pending_pairing": {}}


async def _fake_load_group(*, tenant_id, workspace_id, agent_id, channel_key):
    return {"mode": "disabled", "allowlist": [], "require_mention": True}


class OpenClawReadinessLeakTests(unittest.IsolatedAsyncioTestCase):
    async def test_provision_never_leaks_the_raw_capability_missing_token(self) -> None:
        with patch.object(
            personal_channels_service, "_load_agent_dm_policy_config", _fake_load_dm
        ), patch.object(
            personal_channels_service, "_load_agent_group_policy_config", _fake_load_group
        ), patch.object(
            openclaw_provisioning_service.gateway_execution_service,
            "execute_tool_via_gateway",
            new=AsyncMock(side_effect=ValueError("gateway_capability_missing")),
        ):
            with self.assertRaises(OpenClawProvisioningError) as ctx:
                await openclaw_provisioning_service.provision_openclaw_gateway(
                    gateway_id="gw-1", tenant_id="t", workspace_id="w", agent_id="a"
                )
        message = str(ctx.exception)
        self.assertNotEqual(
            message, "gateway_capability_missing", "the raw internal token reached the exception message verbatim"
        )
        # Not the capability id either — "openclaw.provision" is exactly as
        # much an internal enum to a customer as the reason token was.
        self.assertNotIn("openclaw.provision", message)
        self.assertNotIn("openclaw.channel_setup", message)
        self.assertEqual(ctx.exception.reason_code, "gateway_capability_missing")

    async def test_read_channel_setup_state_never_leaks_the_raw_token(self) -> None:
        with patch.object(
            openclaw_channel_setup_service.gateway_execution_service,
            "execute_tool_via_gateway",
            new=AsyncMock(side_effect=ValueError("gateway_capability_missing")),
        ):
            with self.assertRaises(OpenClawProvisioningError) as ctx:
                await openclaw_channel_setup_service.read_channel_setup_state(
                    gateway_id="gw-1", workspace_id="ws-1"
                )
        message = str(ctx.exception)
        self.assertNotEqual(message, "gateway_capability_missing")
        self.assertNotIn("openclaw.channel_setup", message)
        self.assertEqual(ctx.exception.reason_code, "gateway_capability_missing")

    async def test_write_channel_credential_never_leaks_the_raw_token(self) -> None:
        with patch.object(
            openclaw_channel_setup_service.gateway_execution_service,
            "execute_tool_via_gateway",
            new=AsyncMock(side_effect=ValueError("gateway_offline")),
        ):
            with self.assertRaises(OpenClawProvisioningError) as ctx:
                await openclaw_channel_setup_service.write_channel_credential(
                    gateway_id="gw-1",
                    workspace_id="ws-1",
                    channel_key="openclaw_feishu",
                    values={"appId": "x"},
                )
        message = str(ctx.exception)
        self.assertNotEqual(message, "gateway_offline")
        self.assertEqual(ctx.exception.reason_code, "gateway_offline")

    async def test_capability_missing_and_offline_do_not_share_a_message(self) -> None:
        """The box IS reachable in the capability-missing case; it never had
        the transport installed. Collapsing the two onto one sentence is the
        exact bug #2b of the audit: a wrong-but-confident "could not be
        reached" message and a "Re-check this computer" retry that can never
        succeed."""

        async def _raise(token: str):
            with patch.object(
                openclaw_channel_setup_service.gateway_execution_service,
                "execute_tool_via_gateway",
                new=AsyncMock(side_effect=ValueError(token)),
            ):
                with self.assertRaises(OpenClawProvisioningError) as ctx:
                    await openclaw_channel_setup_service.read_channel_setup_state(
                        gateway_id="gw-1", workspace_id="ws-1"
                    )
            return str(ctx.exception)

        capability_missing_message = await _raise("gateway_capability_missing")
        offline_message = await _raise("gateway_offline")
        self.assertNotEqual(capability_missing_message, offline_message)
        self.assertNotIn("reached", capability_missing_message.lower())
        # Nothing invents a "reconnect"/"retry" promise for a capability that
        # was never advertised — there is no working installer behind that
        # button today (a separate, already-known gap), so the honest
        # message makes no promise a retry can fulfil.
        self.assertNotIn("retry", capability_missing_message.lower())

    async def test_non_token_valueerror_passes_through_unchanged(self) -> None:
        """A ValueError that is already human prose (e.g. from
        _require_active_gateway_registration's own direct raises) must not
        be mangled into the generic fallback sentence — only the raw
        snake_case tokens are translated."""
        already_human = "Gateway registration was not found."
        with patch.object(
            openclaw_channel_setup_service.gateway_execution_service,
            "execute_tool_via_gateway",
            new=AsyncMock(side_effect=ValueError(already_human)),
        ):
            with self.assertRaises(OpenClawProvisioningError) as ctx:
                await openclaw_channel_setup_service.read_channel_setup_state(
                    gateway_id="gw-1", workspace_id="ws-1"
                )
        self.assertEqual(str(ctx.exception), already_human)
        self.assertIsNone(ctx.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
