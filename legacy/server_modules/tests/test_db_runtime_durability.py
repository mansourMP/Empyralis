import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from server_modules import db as runtime_db


class RuntimeDbDurabilityTests(unittest.TestCase):
    def test_durable_runtime_required_for_mobile_beta(self) -> None:
        with patch.dict("server_modules.db.os.environ", {"ORION_MOBILE_BETA_ENABLED": "1"}, clear=True):
            self.assertTrue(runtime_db.durable_runtime_required())
            self.assertEqual(runtime_db.durability_mode(), "required")
            self.assertFalse(runtime_db.sqlite_fallback_allowed())

    def test_sqlite_health_status_blocks_fallback_when_durable_runtime_required(self) -> None:
        with patch.dict("server_modules.db.os.environ", {"ORION_MOBILE_BETA_ENABLED": "1"}, clear=True):
            self.assertEqual(runtime_db.sqlite_health_status(), "fallback_blocked")

    def test_local_dev_keeps_sqlite_health_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "runtime.db"
            with patch.dict(
                "server_modules.db.os.environ",
                {"ORION_ENV": "development", "ORION_RUNTIME_STATE_DB": str(db_path)},
                clear=True,
            ):
                self.assertEqual(runtime_db.sqlite_health_status(), "inactive")


class RuntimeDbHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_postgres_health_status_reports_required_unconfigured_for_beta(self) -> None:
        with patch.dict("server_modules.db.os.environ", {"ORION_MOBILE_BETA_ENABLED": "1"}, clear=True):
            self.assertEqual(await runtime_db.postgres_health_status(), "required_unconfigured")
