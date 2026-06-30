from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from server_modules.hardware_runtime_adapters import self_hosted_node_adapter


def _attachment(**overrides):
    base = {
        "attachment_id": "self_hosted_business_node:rprof-self",
        "attachment_kind": "self_hosted_business_node",
        "workspace_id": "ws-1",
        "runtime_profile_id": "rprof-self",
        "runtime_profile_slug": "secure-node",
        "runtime_id": "node-1",
        "runtime_node_id": "node-1",
        "machine_id": "machine-1",
        "self_hosted_node_status": "online",
        "owner_approved": True,
        "capabilities": ["shell.execute", "file_access"],
        "online": True,
        "healthy": True,
    }
    base.update(overrides)
    return base


class HardwareSelfHostedNodeAdapterTests(unittest.IsolatedAsyncioTestCase):
    def test_capability_aliases_match_runtime_inventory_terms(self) -> None:
        self.assertTrue(
            self_hosted_node_adapter.self_hosted_capability_available(
                _attachment(capabilities=["file_access"]),
                "filesystem.read",
            )
        )
        self.assertFalse(
            self_hosted_node_adapter.self_hosted_capability_available(
                _attachment(capabilities=["shell.execute"]),
                "screenshot.capture",
            )
        )

    def test_attachment_matching_accepts_node_profile_and_machine_ids(self) -> None:
        attachment = _attachment()
        self.assertTrue(self_hosted_node_adapter.self_hosted_attachment_matches_node(attachment, "node-1"))
        self.assertTrue(self_hosted_node_adapter.self_hosted_attachment_matches_node(attachment, "rprof-self"))
        self.assertTrue(self_hosted_node_adapter.self_hosted_attachment_matches_node(attachment, "machine-1"))
        self.assertFalse(self_hosted_node_adapter.self_hosted_attachment_matches_node(attachment, "other"))

    async def test_select_self_hosted_attachment_filters_by_node_and_capability(self) -> None:
        with (
            patch(
                "server_modules.hardware_runtime_adapters.self_hosted_node_adapter.runtime_attachment_service.list_workspace_runtime_attachments",
                new=AsyncMock(
                    return_value={
                        "attachments": [
                            _attachment(runtime_node_id="node-other", runtime_id="node-other"),
                            _attachment(runtime_node_id="node-1", runtime_id="node-1"),
                        ]
                    }
                ),
            ),
            patch(
                "server_modules.hardware_runtime_adapters.self_hosted_node_adapter.runtime_attachment_service.ensure_self_hosted_node_gate",
                return_value=None,
            ),
        ):
            attachment, reason = await self_hosted_node_adapter.select_self_hosted_attachment(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                node_id="node-1",
                capability_id="shell.execute",
            )

        self.assertEqual(reason, "")
        self.assertEqual(attachment["runtime_node_id"], "node-1")

    async def test_select_self_hosted_attachment_reports_capability_mismatch(self) -> None:
        with (
            patch(
                "server_modules.hardware_runtime_adapters.self_hosted_node_adapter.runtime_attachment_service.list_workspace_runtime_attachments",
                new=AsyncMock(return_value={"attachments": [_attachment(capabilities=["shell.execute"])]}),
            ),
            patch(
                "server_modules.hardware_runtime_adapters.self_hosted_node_adapter.runtime_attachment_service.ensure_self_hosted_node_gate",
                return_value=None,
            ),
        ):
            _attachment_payload, reason = await self_hosted_node_adapter.select_self_hosted_attachment(
                tenant_id="tenant-1",
                workspace_id="ws-1",
                node_id="node-1",
                capability_id="screenshot.capture",
            )

        self.assertEqual(reason, "self_hosted_node_capability_mismatch")
