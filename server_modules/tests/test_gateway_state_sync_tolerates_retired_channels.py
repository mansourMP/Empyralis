"""A gateway installed before the cutover must not be able to raise inside
the frame handler for every state update it sends.

`sync_gateway_personal_channel_state` runs on EVERY `gateway.state.update`
frame (gateway_protocol_service.py:2960). The 2026-08-14 cutover retired the
first-party `whatsapp_personal` / `telegram_personal` lanes, but a box
installed before it keeps advertising them in its state payload until it
takes an update — and `assert_personal_gateway_channel` RAISES on a key the
lane contract no longer knows.

Caught on PRODUCTION minutes after the deploy, on a real customer box:

    sync_gateway_personal_channel_state
      -> assert_personal_gateway_channel("whatsapp_personal")
      -> ValueError: Channel lane contract rejected non-personal channel

The contract assertion is still the right gate for a key we intend to WRITE.
It is the wrong response to a key somebody else merely mentioned. An old box
mentioning a retired channel is the ordinary state of a fleet mid-rollout,
not an error.
"""

import unittest

from server_modules import personal_channels_service as pcs


def _payload(*channel_keys: str) -> dict:
    return {
        "personal_channels": {
            key: {"provider": "whatever", "status": "connected"} for key in channel_keys
        }
    }


class GatewayStateSyncToleratesRetiredChannelsTests(unittest.TestCase):
    def test_a_retired_whatsapp_key_does_not_raise(self):
        """The exact production failure, as a test."""
        pcs.sync_gateway_personal_channel_state(
            gateway_id="gateway_legacy",
            registration={"workspace_id": "default"},
            payload=_payload("whatsapp_personal"),
        )

    def test_a_retired_telegram_key_does_not_raise(self):
        pcs.sync_gateway_personal_channel_state(
            gateway_id="gateway_legacy",
            registration={"workspace_id": "default"},
            payload=_payload("telegram_personal"),
        )

    def test_both_retired_keys_together_do_not_raise(self):
        pcs.sync_gateway_personal_channel_state(
            gateway_id="gateway_legacy",
            registration={"workspace_id": "default"},
            payload=_payload("whatsapp_personal", "telegram_personal"),
        )

    def test_an_entirely_unknown_key_does_not_raise_either(self):
        """Not just the two we know about — anything a future or foreign box
        reports. The guard is 'does this build carry it', not a denylist."""
        pcs.sync_gateway_personal_channel_state(
            gateway_id="gateway_legacy",
            registration={"workspace_id": "default"},
            payload=_payload("some_channel_that_never_existed"),
        )

    def test_an_empty_payload_is_still_fine(self):
        pcs.sync_gateway_personal_channel_state(
            gateway_id="gateway_legacy",
            registration={"workspace_id": "default"},
            payload={},
        )

    def test_the_lane_contract_itself_still_rejects_the_retired_key(self):
        """The guard must not have been implemented by weakening the contract.
        Skipping an unknown key on a READ is right; letting it through on a
        WRITE would be the two-live-implementations bug all over again."""
        from server_modules import channel_lane_contract_service

        with self.assertRaises(ValueError):
            channel_lane_contract_service.assert_personal_gateway_channel(
                "whatsapp_personal", "whatever"
            )


if __name__ == "__main__":
    unittest.main()
