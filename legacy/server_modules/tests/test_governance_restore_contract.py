import ast
import dataclasses
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_STATE_STORE_PATH = PROJECT_ROOT / "server_modules" / "runtime_state_store.py"
ARTIFACT_SERVICE_PATH = PROJECT_ROOT / "server_modules" / "artifact_service.py"
RUNTIME_CONFIG_PATH = PROJECT_ROOT / "server_modules" / "runtime_config.py"
CONTROL_PLANE_REPOSITORY_PATH = PROJECT_ROOT / "server_modules" / "control_plane_repository.py"


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class GovernanceRestoreContractTests(unittest.TestCase):
    def test_runtime_checkpoint_manifest_and_rehearsal_are_callable(self) -> None:
        runtime_state_store = _load_module("isolated_runtime_state_store", RUNTIME_STATE_STORE_PATH)
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "runtime-state.sqlite3"
            runtime_state_store.init_runtime_state_db(db_path)
            manifest = runtime_state_store.build_runtime_checkpoint_manifest(db_path)
            rehearsal = runtime_state_store.rehearse_runtime_checkpoint_restore(db_path)

        self.assertEqual(manifest["store_id"], "runtime_checkpoint_sqlite")
        self.assertIn("local_pending_queue", manifest["sqlite_tables"])
        self.assertTrue(rehearsal["ok"])
        self.assertIn("table_counts", rehearsal)

    def test_artifact_governance_helpers_are_callable(self) -> None:
        server_modules_pkg = types.ModuleType("server_modules")
        server_modules_pkg.__path__ = []
        telemetry_module = types.ModuleType("server_modules.telemetry")
        telemetry_module.record_reliability_latency_sample = lambda *_args, **_kwargs: None
        sys.modules.setdefault("server_modules", server_modules_pkg)
        sys.modules["server_modules.telemetry"] = telemetry_module
        original_dataclass = dataclasses.dataclass

        def compat_dataclass(*args, **kwargs):
            kwargs.pop("slots", None)
            return original_dataclass(*args, **kwargs)

        dataclasses.dataclass = compat_dataclass
        try:
            artifact_service = _load_module("isolated_artifact_service", ARTIFACT_SERVICE_PATH)
        finally:
            dataclasses.dataclass = original_dataclass

        with tempfile.TemporaryDirectory() as tempdir:
            store_root = Path(tempdir) / "object-store"
            previous_root = os.environ.get("EMPYRALIS_OBJECT_STORAGE_ROOT")
            os.environ["EMPYRALIS_OBJECT_STORAGE_ROOT"] = str(store_root)
            try:
                artifact_service.store_artifact_bytes(
                    b"restore-me",
                    run_id="run-1",
                    kind="report",
                    file_name="report.txt",
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    retention_days=7,
                    created_at="2026-04-12T00:00:00Z",
                )
                manifest = artifact_service.build_artifact_backup_manifest()
                export_payload = artifact_service.export_artifact_scope(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
                retention_plan = artifact_service.plan_artifact_retention(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                    now_iso="2026-04-12T00:00:00Z",
                )
                rehearsal = artifact_service.rehearse_artifact_restore(
                    tenant_id="tenant-1",
                    workspace_id="workspace-1",
                )
            finally:
                if previous_root is None:
                    os.environ.pop("EMPYRALIS_OBJECT_STORAGE_ROOT", None)
                else:
                    os.environ["EMPYRALIS_OBJECT_STORAGE_ROOT"] = previous_root

        self.assertEqual(manifest["store_id"], "artifact_store")
        self.assertEqual(export_payload["record_count"], 1)
        self.assertEqual(retention_plan["expired_count"], 0)
        self.assertTrue(rehearsal["ok"])

    def test_runtime_config_declares_governance_manifest_contract(self) -> None:
        module_ast = ast.parse(RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
        names = {
            node.name
            for node in module_ast.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assignments = {
            target.id
            for node in module_ast.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        assignments.update(
            node.target.id
            for node in module_ast.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        )

        self.assertIn("ACTIVE_GOVERNANCE_MIGRATION_DISCIPLINE", assignments)
        self.assertIn("build_governance_backup_restore_manifest", names)
        self.assertIn("run_governance_restore_rehearsal", names)

    def test_control_plane_repository_declares_governance_hold_contract(self) -> None:
        source = CONTROL_PLANE_REPOSITORY_PATH.read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS governance_holds", source)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_governance_holds_scope", source)
        self.assertIn("async def upsert_governance_hold", source)
        self.assertIn("async def build_governance_scope_export", source)
        self.assertIn("async def plan_governance_operation", source)


if __name__ == "__main__":
    unittest.main()
