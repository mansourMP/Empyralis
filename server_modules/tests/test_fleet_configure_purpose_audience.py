"""fleet_configure_agent — purpose_preset / audience wiring.

Both keys have been in fleet_tools._ALLOWED_CONFIGURE_KEYS since
fleet_create_agent shipped, but fleet_create_agent was the ONLY writer of
either — a PATCH carrying "purpose_preset" or "audience" passed the
clean_patch allowlist filter and was then silently dropped: no branch in
fleet_configure_agent's body ever wrote either key into `meta`. The call
returned ok=True (a real, committed update — hardware_access/instructions/
etc. from the same patch would still land) while the one field the caller
actually asked to change never persisted. Exactly the "built, tested, and
never wired" shape CLAUDE.md already documents elsewhere in this codebase,
just with no test ever having exercised these two keys through this route
at all (the only existing coverage was fleet_create_agent's own).

This is what unblocks the frontend control added in
frontend/lib/workspace/fleet/FleetAgentDetail.tsx's GeneralTab (there was
previously no UI at all to change an agent's purpose/audience after
creation — the create-agent wizard was the only writer).

Tests:
  (a) a PATCH with both purpose_preset and audience persists both, verbatim
  (b) a PATCH with only purpose_preset re-derives audience from it
      (_AUDIENCE_BY_PURPOSE_PRESET), matching fleet_create_agent's own
      pairing at creation time
  (c) an invalid purpose_preset value fails loudly (never silently dropped
      or coerced) and never reaches update_workspace_agent_install
  (d) an invalid audience value fails loudly the same way
  (e) a PATCH with only audience (no purpose_preset) sets audience alone,
      leaving purpose_preset untouched
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from server_modules import fleet_tools


def _run(coro):
    return asyncio.run(coro)


def _existing_bundle(agent_id: str) -> dict:
    return {"id": agent_id, "install_metadata": {"purpose_preset": "internal_assistant", "audience": "owner"}}


class FleetConfigureAgentPurposeAudienceTests(unittest.TestCase):
    def test_purpose_preset_and_audience_both_persist(self):
        update_mock = AsyncMock(return_value=_existing_bundle("agent-x"))
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=_existing_bundle("agent-x")),
            ),
            patch("server_modules.agent_registry_repository.update_workspace_agent_install", new=update_mock),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1", workspace_id="ws-1", agent_id="agent-x",
                    patch={"purpose_preset": "customer_facing", "audience": "external"},
                )
            )
        assert result["ok"] is True
        update_mock.assert_awaited_once()
        saved_meta = update_mock.await_args.kwargs["metadata"]
        assert saved_meta["purpose_preset"] == "customer_facing"
        assert saved_meta["audience"] == "external"

    def test_purpose_preset_alone_re_derives_audience(self):
        update_mock = AsyncMock(return_value=_existing_bundle("agent-x"))
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=_existing_bundle("agent-x")),
            ),
            patch("server_modules.agent_registry_repository.update_workspace_agent_install", new=update_mock),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1", workspace_id="ws-1", agent_id="agent-x",
                    patch={"purpose_preset": "customer_facing"},
                )
            )
        assert result["ok"] is True
        saved_meta = update_mock.await_args.kwargs["metadata"]
        assert saved_meta["purpose_preset"] == "customer_facing"
        # Derived from _AUDIENCE_BY_PURPOSE_PRESET, not left at the prior
        # stored "owner" — a caller changing only the preset must not end up
        # with a preset/audience pair that disagrees with each other.
        assert saved_meta["audience"] == "external"

    def test_audience_alone_sets_audience_without_touching_purpose_preset(self):
        update_mock = AsyncMock(return_value=_existing_bundle("agent-x"))
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=_existing_bundle("agent-x")),
            ),
            patch("server_modules.agent_registry_repository.update_workspace_agent_install", new=update_mock),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1", workspace_id="ws-1", agent_id="agent-x",
                    patch={"audience": "external"},
                )
            )
        assert result["ok"] is True
        saved_meta = update_mock.await_args.kwargs["metadata"]
        assert saved_meta["audience"] == "external"
        # Untouched — still whatever was already on the bundle.
        assert saved_meta["purpose_preset"] == "internal_assistant"

    def test_invalid_purpose_preset_fails_loudly_and_never_saves(self):
        update_mock = AsyncMock(side_effect=AssertionError("must not save on an invalid patch"))
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=_existing_bundle("agent-x")),
            ),
            patch("server_modules.agent_registry_repository.update_workspace_agent_install", new=update_mock),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1", workspace_id="ws-1", agent_id="agent-x",
                    patch={"purpose_preset": "bogus"},
                )
            )
        assert result["ok"] is False
        assert "purpose_preset" in result["error"]
        update_mock.assert_not_awaited()

    def test_invalid_audience_fails_loudly_and_never_saves(self):
        update_mock = AsyncMock(side_effect=AssertionError("must not save on an invalid patch"))
        with (
            patch(
                "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
                new=AsyncMock(return_value=_existing_bundle("agent-x")),
            ),
            patch("server_modules.agent_registry_repository.update_workspace_agent_install", new=update_mock),
        ):
            result = _run(
                fleet_tools.fleet_configure_agent(
                    actor_id="owner-1", workspace_id="ws-1", agent_id="agent-x",
                    patch={"audience": "bogus"},
                )
            )
        assert result["ok"] is False
        assert "audience" in result["error"]
        update_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
