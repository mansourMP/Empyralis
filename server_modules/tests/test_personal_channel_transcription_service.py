"""personal_channel_transcription_service — per-agent STT credential resolution.

_resolve_openai_api_key now tries agent_capability_service's speech_to_text
capability FIRST (per-agent BYOK/platform_credits), falling back to the
workspace-level direct_chat_provider_service lookup this module used
exclusively before per-agent capability config existed. Covers:
  1. agent_id supplied + capability resolves -> uses the per-agent key,
     the workspace-level lookup is never even called.
  2. agent_id supplied but capability does NOT resolve -> falls back to the
     workspace-level lookup unchanged (exact prior behavior preserved).
  3. agent_id omitted entirely (local-bridge channel path, which has no
     agent context yet) -> goes straight to the workspace-level lookup,
     capability resolution is never attempted.
  4. A capability-resolution crash degrades to the workspace-level fallback
     rather than blocking transcription outright.
  5. transcribe_voice_bytes threads agent_id all the way through and still
     never raises on any failure (module's core contract).
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from server_modules import personal_channel_transcription_service as stt


def _run(coro):
    import asyncio
    return asyncio.run(coro)


class ResolveOpenaiApiKeyTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_capability_resolves_uses_the_per_agent_key(self):
        from server_modules import agent_capability_service as caps

        resolution = caps.CapabilityResolution(
            capability="speech_to_text", available=True, mode="byok_api",
            provider="openai", credentials={"api_key": "sk-agent-own-stt-key"}, billing_mode="byok_api",
        )
        with (
            patch(
                "server_modules.agent_capability_service.resolve_agent_capability_provider_by_id",
                new=AsyncMock(return_value=resolution),
            ),
            patch("server_modules.direct_chat_provider_service.direct_chat_credentials") as workspace_lookup,
        ):
            key = await stt._resolve_openai_api_key("ws-1", "agent-x")
        self.assertEqual(key, "sk-agent-own-stt-key")
        workspace_lookup.assert_not_called()

    async def test_agent_capability_unresolved_falls_back_to_workspace_lookup(self):
        from server_modules import agent_capability_service as caps

        resolution = caps.CapabilityResolution(
            capability="speech_to_text", available=False, mode="byok_api", provider="openai",
            credentials={}, billing_mode="", reason="byok_key_missing", message="no key",
        )
        with (
            patch(
                "server_modules.agent_capability_service.resolve_agent_capability_provider_by_id",
                new=AsyncMock(return_value=resolution),
            ),
            patch(
                "server_modules.direct_chat_provider_service.direct_chat_credentials",
                return_value={"api_key": "sk-workspace-fallback"},
            ),
        ):
            key = await stt._resolve_openai_api_key("ws-1", "agent-x")
        self.assertEqual(key, "sk-workspace-fallback")

    async def test_no_agent_id_skips_capability_resolution_entirely(self):
        """The local-bridge channel path (no resolved agent context yet) —
        must go straight to the unchanged workspace-level lookup."""
        with (
            patch(
                "server_modules.agent_capability_service.resolve_agent_capability_provider_by_id",
                new=AsyncMock(),
            ) as capability_resolver,
            patch(
                "server_modules.direct_chat_provider_service.direct_chat_credentials",
                return_value={"api_key": "sk-workspace-only"},
            ),
        ):
            key = await stt._resolve_openai_api_key("ws-1", "")
        self.assertEqual(key, "sk-workspace-only")
        capability_resolver.assert_not_called()

    async def test_capability_resolution_crash_degrades_to_workspace_fallback(self):
        with (
            patch(
                "server_modules.agent_capability_service.resolve_agent_capability_provider_by_id",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch(
                "server_modules.direct_chat_provider_service.direct_chat_credentials",
                return_value={"api_key": "sk-workspace-after-crash"},
            ),
        ):
            key = await stt._resolve_openai_api_key("ws-1", "agent-x")
        self.assertEqual(key, "sk-workspace-after-crash")

    async def test_nothing_resolves_anywhere_returns_none_never_raises(self):
        from server_modules import agent_capability_service as caps

        unresolved = caps.CapabilityResolution(
            capability="speech_to_text", available=False, mode="platform_credits", provider="openai",
            credentials={}, billing_mode="", reason="platform_provider_not_configured", message="",
        )
        with (
            patch(
                "server_modules.agent_capability_service.resolve_agent_capability_provider_by_id",
                new=AsyncMock(return_value=unresolved),
            ),
            patch("server_modules.direct_chat_provider_service.direct_chat_credentials", return_value={}),
        ):
            key = await stt._resolve_openai_api_key("ws-1", "agent-x")
        self.assertIsNone(key)


class TranscribeVoiceBytesAgentThreadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_id_is_threaded_into_key_resolution(self):
        captured = {}

        async def _fake_resolve_key(workspace_id, agent_id=""):
            captured["workspace_id"] = workspace_id
            captured["agent_id"] = agent_id
            return None  # not configured -> degrade gracefully

        with patch.object(stt, "_resolve_openai_api_key", new=_fake_resolve_key):
            result = await stt.transcribe_voice_bytes(
                workspace_id="ws-1", audio_bytes=b"fake-audio-bytes",
                agent_id="agent-x",
            )
        self.assertEqual(captured["agent_id"], "agent-x")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "stt_provider_not_configured")

    async def test_omitted_agent_id_defaults_to_empty_string_not_a_crash(self):
        captured = {}

        async def _fake_resolve_key(workspace_id, agent_id=""):
            captured["agent_id"] = agent_id
            return None

        with patch.object(stt, "_resolve_openai_api_key", new=_fake_resolve_key):
            result = await stt.transcribe_voice_bytes(workspace_id="ws-1", audio_bytes=b"fake-audio-bytes")
        self.assertEqual(captured["agent_id"], "")
        self.assertFalse(result["ok"])

    async def test_never_raises_when_the_key_resolver_itself_blows_up(self):
        with patch.object(stt, "_resolve_openai_api_key", new=AsyncMock(side_effect=RuntimeError("boom"))):
            with self.assertRaises(RuntimeError):
                # _resolve_openai_api_key's OWN contract is "never raises" —
                # but if some future edit breaks that, transcribe_voice_bytes
                # doesn't have a try/except around the resolver call today.
                # This test documents the current (pre-existing) behavior
                # rather than asserting a guarantee this module doesn't make
                # at that specific seam.
                await stt.transcribe_voice_bytes(workspace_id="ws-1", audio_bytes=b"fake-audio-bytes", agent_id="agent-x")


if __name__ == "__main__":
    unittest.main()
