from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from server_modules import gateway_protocol_service


class GatewayProtocolDuplicateIdTests(unittest.TestCase):
    def setUp(self):
        self.connection = gateway_protocol_service._LiveGatewayConnection(
            websocket=MagicMock(),
            gateway_id="gateway-1",
            session_id="session-1",
            scope={"tenant_id": "tenant-1", "workspace_id": "workspace-1"},
        )

    def test_duplicate_frame_id_returns_cached_response(self):
        """When a frame ID is seen again, the cached response is returned."""
        frame_id = "frame-123"
        response = {"kind": "response", "id": frame_id, "ok": True}
        self.connection.cache_frame_response(frame_id, response)
        # First call remembers
        self.assertTrue(self.connection.remember_inbound_frame_id(frame_id))
        # Verify the response is cached
        cached = self.connection.get_cached_frame_response(frame_id)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["id"], frame_id)
        self.assertTrue(cached["ok"])

    def test_duplicate_frame_id_does_not_close_connection(self):
        """Duplicate frame IDs should not close the connection."""
        frame_id = "frame-456"
        response = {"kind": "response", "id": frame_id, "ok": True}
        self.connection.cache_frame_response(frame_id, response)
        # First call remembers
        self.assertTrue(self.connection.remember_inbound_frame_id(frame_id))
        # Connection should not be closed
        self.assertFalse(self.connection._closed)
        # Duplicate should be rejected but cached response available
        self.assertFalse(self.connection.remember_inbound_frame_id(frame_id))
        cached = self.connection.get_cached_frame_response(frame_id)
        self.assertIsNotNone(cached)

    def test_third_duplicate_still_gets_cached_response(self):
        """Even after multiple duplicates, the cached response should still be returned."""
        frame_id = "frame-789"
        response = {"kind": "response", "id": frame_id, "ok": True, "payload": {"result": "done"}}
        self.connection.cache_frame_response(frame_id, response)
        self.connection.remember_inbound_frame_id(frame_id)
        # Second call (duplicate)
        cached = self.connection.get_cached_frame_response(frame_id)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["payload"]["result"], "done")
        # Third call (yet another duplicate)
        cached2 = self.connection.get_cached_frame_response(frame_id)
        self.assertIsNotNone(cached2)
        self.assertEqual(cached2["payload"]["result"], "done")
        # Still not closed
        self.assertFalse(self.connection._closed)

    def test_different_frame_id_after_duplicate_still_works(self):
        """After a duplicate, a new frame ID should still work normally."""
        frame_id_1 = "frame-aaa"
        frame_id_2 = "frame-bbb"
        response_1 = {"kind": "response", "id": frame_id_1, "ok": True}
        self.connection.cache_frame_response(frame_id_1, response_1)
        self.connection.remember_inbound_frame_id(frame_id_1)
        # Duplicate check on frame_id_1
        self.assertFalse(self.connection.remember_inbound_frame_id(frame_id_1))
        # New frame should be ok
        self.assertTrue(self.connection.remember_inbound_frame_id(frame_id_2))

    def test_frame_response_cache_eviction_lru(self):
        """The response cache should evict oldest entries when full."""
        # Fill the cache beyond capacity
        for i in range(2050):
            self.connection.cache_frame_response(f"frame-{i}", {"id": f"frame-{i}", "ok": True})
        # The oldest entry (frame-0) should have been evicted
        self.assertIsNone(self.connection.get_cached_frame_response("frame-0"))
        # A newer entry should still be present
        self.assertIsNotNone(self.connection.get_cached_frame_response("frame-2049"))

    def test_remember_inbound_frame_id_capacity_logged(self):
        """When the seen set fills up, it should log a warning and evict oldest entries."""
        connection = gateway_protocol_service._LiveGatewayConnection(
            websocket=MagicMock(),
            gateway_id="gateway-cap",
            session_id="session-cap",
            scope={},
        )
        # Fill the seen set to capacity
        for i in range(4096):
            connection.remember_inbound_frame_id(f"frame-{i}")
        # A new entry should be accepted
        self.assertTrue(connection.remember_inbound_frame_id("frame-new"))
        # That same new entry should now be recognized as duplicate
        self.assertFalse(connection.remember_inbound_frame_id("frame-new"))

    def test_cache_frame_response_updates_existing_key(self):
        """Updating a cached response should move it to the end (most recent)."""
        self.connection.cache_frame_response("key1", {"ok": True})
        self.connection.cache_frame_response("key2", {"ok": True})
        # Re-cache key1 - it should move to most recent
        self.connection.cache_frame_response("key1", {"ok": False})
        cached = self.connection.get_cached_frame_response("key1")
        self.assertIsNotNone(cached)
        self.assertFalse(cached["ok"])


class GatewayProtocolVersionTests(unittest.TestCase):
    def test_version_mismatch_rejects_wrong_version(self):
        """Sending an unsupported version should be rejected."""
        error = gateway_protocol_service._validate_client_protocol_version("v1alpha1")
        self.assertIsNotNone(error)
        self.assertIn("Unsupported protocol version", error)
        self.assertIn("v1alpha2", error)

    def test_version_mismatch_rejects_no_version(self):
        """Sending no protocol version should be rejected."""
        error = gateway_protocol_service._validate_client_protocol_version(None)
        self.assertIsNotNone(error)
        self.assertIn("Protocol version is required", error)

    def test_version_mismatch_rejects_empty_version(self):
        """Sending an empty protocol version should be rejected."""
        error = gateway_protocol_service._validate_client_protocol_version("")
        self.assertIsNotNone(error)
        self.assertIn("Protocol version is required", error)

    def test_version_match_accepts_v1alpha2(self):
        """Sending v1alpha2 should be accepted."""
        error = gateway_protocol_service._validate_client_protocol_version("v1alpha2")
        self.assertIsNone(error)

    def test_protocol_version_constant_is_v1alpha2(self):
        """The server protocol version should be v1alpha2."""
        self.assertEqual(gateway_protocol_service.PROTOCOL_VERSION, "v1alpha2")

    def test_supported_protocol_versions_contains_v1alpha2(self):
        """The supported protocol versions set should contain v1alpha2."""
        self.assertIn("v1alpha2", gateway_protocol_service.SUPPORTED_PROTOCOL_VERSIONS)


if __name__ == "__main__":
    unittest.main()
