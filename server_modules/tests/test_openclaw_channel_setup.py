"""The generated channel setup form, and the credential that must not stick.

Two things are being defended here and they fail in opposite directions.

THE FORM MUST STAY DERIVED. The moment anyone adds a per-channel branch, a
hand-written field list, or a channel-name literal to this lane, the twenty-
fifth channel OpenClaw ships silently gets no form — and nothing fails, which
is why a behavioural test cannot catch it. Hence the AST/source scans below.

THE CREDENTIAL MUST NOT STICK. It is a pass-through: browser -> cloud ->
device. Any code that writes it to a table, a log, or a response body turns a
value we deliberately never persist into one we do, and that regression is
invisible from the outside — a route returning a token looks exactly like a
route returning a status. Hence the "no value in any output" assertions.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

from server_modules import (
    channel_lane_contract_service,
    openclaw_channel_registry,
    openclaw_channel_setup_service,
)
from server_modules.openclaw_provisioning_service import OpenClawProvisioningError

REPO_ROOT = Path(__file__).resolve().parents[2]


class CredentialShapeIsDerivedTests(unittest.TestCase):
    """Every channel has a form, and nobody typed one."""

    def test_every_channel_carries_a_credential_shape(self) -> None:
        # Not "most" and not "the ones we thought about": the manifest is the
        # whole pinned channel set, so a channel without a shape would render
        # as an empty card with no explanation.
        for channel in openclaw_channel_registry.CHANNELS:
            shape = channel.credential_shape
            self.assertIn(
                shape.get("connect_method"),
                {"credential", "pairing", "plugin_absent"},
                f"{channel.id} has no recognized connect_method.",
            )
            self.assertIsInstance(shape.get("fields"), list, f"{channel.id} has no fields list.")

    def test_a_pairing_channel_offers_no_fields_to_render(self) -> None:
        """"No dead controls" is a product law, enforced at the data.

        A channel that links by QR, by a local database, or by an inbound
        webhook has no token to paste. If it carried fields, the UI would
        render a form whose Save could only ever fail — a control whose own
        label admits it does nothing.
        """
        pairing = [
            channel
            for channel in openclaw_channel_registry.CHANNELS
            if channel.credential_shape.get("connect_method") == "pairing"
        ]
        self.assertGreater(len(pairing), 0, "The derivation found no pairing channels at all.")
        for channel in pairing:
            self.assertEqual(
                channel.credential_shape.get("fields"),
                [],
                f"{channel.id} pairs but still declares form fields.",
            )

    def test_a_credential_channel_offers_at_least_one_field(self) -> None:
        for channel in openclaw_channel_registry.CHANNELS:
            if channel.credential_shape.get("connect_method") != "credential":
                continue
            self.assertGreater(
                len(channel.credential_shape["fields"]),
                0,
                f"{channel.id} says it takes a credential but declares no field to type it into.",
            )

    def test_secret_flags_come_from_openclaws_own_type(self) -> None:
        """Spot-check the derivation against OpenClaw's independent evidence.

        Their per-plugin `secret-contract` module names Feishu's three secret
        paths (appSecret / encryptKey / verificationToken) — a completely
        separate artifact from the config schema this derivation reads. The
        two agreeing is the reason the structural read is trusted on the
        channels whose plugin is not installed here. `appId` sits beside them
        and is NOT a secret in either source, which is the half that would go
        unnoticed if only the positives were checked.
        """
        feishu = openclaw_channel_registry.CHANNELS_BY_ID["feishu"]
        by_name = {field["name"]: field for field in feishu.credential_shape["fields"]}
        for name in ("appSecret", "encryptKey", "verificationToken"):
            self.assertTrue(by_name[name]["secret"], f"feishu.{name} should be typed as a secret.")
        self.assertFalse(by_name["appId"]["secret"], "feishu.appId is an identifier, not a secret.")

    def test_no_channel_name_is_typed_into_the_setup_lane(self) -> None:
        """The drift guard. A behavioural test cannot catch the NEXT branch.

        Every OpenClaw channel already works through one identical path, so a
        channel id appearing as a literal in the setup service or the setup UI
        means somebody has started special-casing — which is exactly how the
        five-channel hand-written registry happened, and it fails silently for
        every channel that is not the one being special-cased.
        """
        ids = {channel.id for channel in openclaw_channel_registry.CHANNELS}
        # `feishu` is quoted in this test file itself (above) as evidence; the
        # scan deliberately covers production sources only.
        sources = [
            REPO_ROOT / "server_modules" / "openclaw_channel_setup_service.py",
            REPO_ROOT / "frontend" / "lib" / "workspace" / "fleet" / "OpenClawChannelsPanel.tsx",
            REPO_ROOT / "empyralis-gateway" / "src" / "openclaw" / "provisioning" / "openclaw-channel-setup.ts",
        ]
        offenders: List[str] = []
        for path in sources:
            text = path.read_text()
            for line_number, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                # Prose in a comment may name a channel as an example; code may
                # not. The distinction matters — the docstrings above are the
                # reason the next reader understands the rule.
                if stripped.startswith(("#", "*", "//", "/*")):
                    continue
                for channel_id in ids:
                    if f'"{channel_id}"' in line or f"'{channel_id}'" in line:
                        offenders.append(f"{path.name}:{line_number}: {stripped[:110]}")
        self.assertEqual(
            offenders,
            [],
            "A channel id was typed into the setup lane. Every channel goes through the same "
            "derived path; a literal here means the twenty-eighth channel silently gets nothing.\n"
            + "\n".join(offenders),
        )


class CredentialWriteFailsClosedTests(unittest.TestCase):
    """A write may only touch fields OpenClaw declares for the channel."""

    def _values(self, channel_key: str, values: Any) -> Dict[str, str]:
        return openclaw_channel_setup_service._validate_credential_values(channel_key, values)

    def test_a_declared_field_is_accepted(self) -> None:
        cleaned = self._values("openclaw_feishu", {"appId": " cli_x ", "appSecret": "s3cret"})
        self.assertEqual(cleaned, {"appId": "cli_x", "appSecret": "s3cret"})

    def test_an_undeclared_field_is_refused_by_name(self) -> None:
        """Named, never dropped.

        A silently ignored field is a save that reports success and changes
        nothing — indistinguishable, from the owner's side, from working. It
        is also the shape that would let a caller aim at `gateway.auth` or
        `plugins.allow` and walk through the provisioning lockdown.
        """
        with self.assertRaises(OpenClawProvisioningError) as caught:
            self._values("openclaw_feishu", {"dmPolicy": "open"})
        self.assertIn("dmPolicy", str(caught.exception))
        self.assertEqual(caught.exception.status_code, 400)

    def test_an_empty_value_is_refused_rather_than_read_as_clear(self) -> None:
        # "An empty string is not a decision." Reading one as "delete this
        # credential" is how a save that meant nothing takes a live channel
        # down; leaving a field alone is expressed by omitting it.
        for value in ("", "   "):
            with self.assertRaises(OpenClawProvisioningError):
                self._values("openclaw_feishu", {"appSecret": value})

    def test_a_non_string_value_is_refused(self) -> None:
        with self.assertRaises(OpenClawProvisioningError):
            self._values("openclaw_feishu", {"appSecret": 1234})

    def test_a_pairing_channel_refuses_a_credential_outright(self) -> None:
        pairing = next(
            channel
            for channel in openclaw_channel_registry.CHANNELS
            if channel.credential_shape.get("connect_method") == "pairing"
            and channel.id in {c.id for c in channel_lane_contract_service.OPENCLAW_ACTIVE_CHANNELS}
        )
        with self.assertRaises(OpenClawProvisioningError):
            self._values(pairing.channel_key, {"token": "anything"})

    def test_an_unknown_channel_key_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            openclaw_channel_setup_service.credential_shape_for_channel_key("openclaw_not_a_channel")


class CredentialNeverComesBackTests(unittest.IsolatedAsyncioTestCase):
    """The value goes one way. Nothing on the return path can carry it."""

    async def test_a_write_returns_the_device_state_and_not_the_value(self) -> None:
        secret = "tok_do_not_leak_9f2a"
        device_result = {
            "capability_id": "openclaw.channel_setup",
            "status": "ok",
            "refusal": None,
            # What the device actually answers: presence, never value.
            "channels": [{"channel_id": "feishu", "fields": [{"name": "appSecret", "set": True}]}],
            "written_fields": ["appSecret"],
            "restart_required": True,
        }
        with patch.object(
            openclaw_channel_setup_service.gateway_execution_service,
            "execute_tool_via_gateway",
            new=AsyncMock(return_value={"result": device_result}),
        ) as dispatch:
            returned = await openclaw_channel_setup_service.write_channel_credential(
                gateway_id="gw-1",
                workspace_id="ws-1",
                channel_key="openclaw_feishu",
                values={"appSecret": secret},
            )

        # It reached the device exactly once, under the right capability...
        self.assertEqual(dispatch.await_count, 1)
        arguments = dispatch.await_args.kwargs["arguments"]
        self.assertEqual(arguments["values"], {"appSecret": secret})
        # ...and nothing on the way back carries it. Serialized rather than
        # walked field by field, so a value nested anywhere in a future
        # response shape is caught too.
        self.assertNotIn(secret, json.dumps(returned))

    async def test_a_read_never_asks_for_values(self) -> None:
        with patch.object(
            openclaw_channel_setup_service.gateway_execution_service,
            "execute_tool_via_gateway",
            new=AsyncMock(return_value={"result": {"status": "ok", "channels": []}}),
        ) as dispatch:
            await openclaw_channel_setup_service.read_channel_setup_state(
                gateway_id="gw-1", workspace_id="ws-1"
            )
        self.assertEqual(dispatch.await_args.kwargs["arguments"], {"action": "read"})

    def test_no_persistence_primitive_is_reachable_from_this_module(self) -> None:
        """Structural, because a behavioural test cannot see a future writer.

        The credential is deliberately NOT stored: not in `vault_credentials`
        (whose scoping story is already weak — no `tenant_id`, and
        `list_all()` is a full-table read), not in any other table, not in a
        log line. This asserts the module has no way to, by scanning its own
        source for the primitives that would do it.
        """
        source = Path(inspect.getfile(openclaw_channel_setup_service)).read_text()
        tree = ast.parse(source)
        banned = {"vault", "secrets_broker", "store_credential", "logger", "logging", "print"}
        offenders: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                if any(token in name for token in banned):
                    offenders.append(f"line {node.lineno}: {name}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                text = ast.unparse(node)
                if any(token in text for token in banned):
                    offenders.append(f"line {node.lineno}: {text}")
        self.assertEqual(
            offenders,
            [],
            "The channel-setup service gained a way to persist or log a credential:\n"
            + "\n".join(offenders),
        )


class SetupCatalogTests(unittest.TestCase):
    def test_the_catalog_is_exactly_the_live_transport_set(self) -> None:
        """Superseded channels are declared but never offered for setup.

        Eight of OpenClaw's channels are platforms an Empyralis first-party
        runtime already owns. Offering setup for one of those would put two
        runtimes on one account — duplicate replies to a real person, and no
        way for them to tell which one spoke.
        """
        catalog = openclaw_channel_setup_service.openclaw_channel_setup_catalog()
        self.assertEqual(
            {entry["channel_id"] for entry in catalog},
            {channel.id for channel in channel_lane_contract_service.OPENCLAW_ACTIVE_CHANNELS},
        )

    def test_the_catalog_carries_no_value_shaped_key(self) -> None:
        # The catalog is served to any workspace viewer. It describes a FORM;
        # if it ever grew a "current value" it would be serving credentials to
        # readers who may not even own the box.
        blob = json.dumps(openclaw_channel_setup_service.openclaw_channel_setup_catalog())
        for banned in ('"value"', '"values"', '"secret_value"', '"current"'):
            self.assertNotIn(banned, blob)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
