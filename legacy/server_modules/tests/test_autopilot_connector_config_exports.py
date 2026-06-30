import unittest

from server_modules.connectors import autopilot_runtime_exports as ac
from server_modules.connectors import autopilot_connector_config as config


class AutopilotConnectorConfigExportTests(unittest.TestCase):
    def test_connector_re_exports_config_names(self) -> None:
        self.assertIs(ac._telegram_get_updates_process_lock, config._telegram_get_updates_process_lock)
        self.assertIs(ac._resolve_state_file, config._resolve_state_file)
        self.assertIs(ac._resolve_state_dir, config._resolve_state_dir)
        self.assertIs(ac._AUTOPILOT_EVENT_DEDUP, config._AUTOPILOT_EVENT_DEDUP)
        self.assertIs(ac._AUTOPILOT_EVENT_DEDUP_LOCK, config._AUTOPILOT_EVENT_DEDUP_LOCK)
        self.assertIs(ac._CHANNEL_DEAD_LETTER_LOCK, config._CHANNEL_DEAD_LETTER_LOCK)
        self.assertEqual(ac.DEFAULT_CHAT_PREFIX, config.DEFAULT_CHAT_PREFIX)
        self.assertEqual(ac.ORION_LOCAL_LEASE_SECONDS, config.ORION_LOCAL_LEASE_SECONDS)
        self.assertEqual(ac.ORION_TELEGRAM_AUTOPILOT_POLL_SECONDS, config.ORION_TELEGRAM_AUTOPILOT_POLL_SECONDS)
        self.assertEqual(ac.ORION_TELEGRAM_AUTOPILOT_MAX_UPDATES, config.ORION_TELEGRAM_AUTOPILOT_MAX_UPDATES)
        self.assertEqual(ac.ORION_TELEGRAM_MEDIA_MAX_ITEMS, config.ORION_TELEGRAM_MEDIA_MAX_ITEMS)
        self.assertEqual(ac.ORION_TELEGRAM_MEDIA_DIR, config.ORION_TELEGRAM_MEDIA_DIR)
        self.assertEqual(ac.ORION_TELEGRAM_AUTOPILOT_STATE_FILE, config.ORION_TELEGRAM_AUTOPILOT_STATE_FILE)
        self.assertEqual(ac.ORION_WHATSAPP_AUTOPILOT_STATE_FILE, config.ORION_WHATSAPP_AUTOPILOT_STATE_FILE)
        self.assertEqual(ac.ORION_CHANNEL_DEAD_LETTER_LIMIT, config.ORION_CHANNEL_DEAD_LETTER_LIMIT)
        self.assertEqual(ac.EMPYRALIST_RUNTIME_URL, config.EMPYRALIST_RUNTIME_URL)


if __name__ == "__main__":
    unittest.main()
