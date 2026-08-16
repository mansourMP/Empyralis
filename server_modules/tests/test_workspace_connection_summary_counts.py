"""The workspace home's "N channels connected" number, counted honestly.

This counter has now been wrong in BOTH directions inside a week, and each
wrong direction told the founder a different lie about his own workspace:

    too HIGH   four enabled `telegram_personal` rows — bindings for the
               retired first-party lane, which this build cannot carry a
               message on — were summed into "5 channels connected".
               A row nothing can read is not a connection; it is litter the
               cutover left behind.

    too LOW    the fix for that read each row as
               `row.get("channel_key") or row.get("channel")`. NEITHER KEY
               EXISTS. agent_bindings_repository._list selects
               `{key_col} AS key`, so every row carries "key" — the set was
               all empty strings, the intersection with the live channels
               emptied, and a genuinely enabled `slack` binding rendered as
               "0 Channels connected".

The second one is why the fake rows below are built from the repository's
real SELECT column list — `id, tenant_id, workspace_id, agent_install_id,
key, enabled, binding` — and never from field names invented here.
CLAUDE.md's own rule: "a fixture that invents its own input cannot notice
the real input is shaped differently." A fixture carrying `channel_key`
would have passed against the broken code AND against the fix, agreeing
with itself about a shape production never produces.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Set

from server_modules import connection_catalog_service


def _run(coro):
    """`asyncio.run`, not `get_event_loop().run_until_complete` — the latter
    picks up (and can be broken by) a loop another test in the same session
    already created or closed. Same idiom as
    test_openclaw_provisioning_service.py's own `_run`."""
    return asyncio.run(coro)


def _binding_row(
    *,
    key: str,
    agent_install_id: str = "agent-1",
    enabled: bool = True,
    binding: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One row exactly as `agent_bindings_repository._list` returns it.

    The column list is copied from that function's own SELECT:

        SELECT id, tenant_id, workspace_id, agent_install_id,
               {key_col} AS key, enabled, binding

    The `AS key` alias is the whole point — the channel's identity arrives
    under "key", never under "channel_key" or "channel". Every other caller
    in the repo reads it that way (openclaw_provisioning_service.
    channels_in_use, fleet_tools, agent_channel_router, mcp_server, and
    agent_connection_status in this very module), which is what makes it the
    convention rather than one function's preference.
    """
    return {
        "id": f"achbind_{key}_{agent_install_id}",
        "tenant_id": "t",
        "workspace_id": "w",
        "agent_install_id": agent_install_id,
        "key": key,
        "enabled": enabled,
        "binding": dict(binding or {}),
    }


def _stub(
    monkeypatch,
    *,
    channel_rows: List[Dict[str, Any]],
    connector_rows: Optional[List[Dict[str, Any]]] = None,
    openclaw_channels: Optional[Set[str]] = None,
    openclaw_raises: bool = False,
) -> None:
    """Drive the REAL `workspace_connection_summary` against faked repositories.

    Only the two repositories and the OpenClaw contribution are stubbed; the
    catalog, the lane contract, the intersection and the union are the live
    code under test.
    """
    from server_modules import agent_bindings_repository
    from server_modules import agent_registry_repository
    from server_modules import openclaw_provisioning_service

    async def fake_channel_bindings(*, tenant_id, workspace_id, enabled_only=False):
        return list(channel_rows)

    async def fake_connector_bindings(*, tenant_id, workspace_id, enabled_only=False):
        return list(connector_rows or [])

    monkeypatch.setattr(
        agent_bindings_repository, "list_workspace_channel_bindings", fake_channel_bindings
    )
    monkeypatch.setattr(
        agent_bindings_repository, "list_workspace_connector_bindings", fake_connector_bindings
    )

    async def fake_installs(*, tenant_id, workspace_id, include_master=False):
        return [{"agent_id": "agent-1"}]

    monkeypatch.setattr(
        agent_registry_repository, "list_workspace_agent_installs", fake_installs
    )

    async def fake_channels_in_use(*, tenant_id, workspace_id, agent_id):
        if openclaw_raises:
            raise RuntimeError("control plane unavailable")
        return set(openclaw_channels or set())

    monkeypatch.setattr(
        openclaw_provisioning_service, "channels_in_use", fake_channels_in_use
    )


def _channels_connected(monkeypatch, **kwargs) -> int:
    _stub(monkeypatch, **kwargs)
    summary = _run(
        connection_catalog_service.workspace_connection_summary(
            workspace_id="w", tenant_id="t"
        )
    )
    return int(summary["channels"]["connected"])


# ── The fixture itself has to be right, or nothing below means anything ─────

def test_the_fake_row_matches_the_repositorys_real_select_columns():
    """The guard on this file's own premise.

    If the repository's SELECT ever stops aliasing to `key` — or grows a
    column — this fails here rather than letting five green tests agree with
    a shape production no longer produces.
    """
    import inspect

    from server_modules import agent_bindings_repository

    source = inspect.getsource(agent_bindings_repository._list)
    assert "{key_col} AS key" in source, (
        "The repository no longer aliases the channel/connector key column to "
        "`key`. Every fake row in this file — and every caller in the repo — "
        "reads `row['key']`. Follow the repository, then update this fixture."
    )
    row = _binding_row(key="slack")
    assert set(row) == {
        "id",
        "tenant_id",
        "workspace_id",
        "agent_install_id",
        "key",
        "enabled",
        "binding",
    }


# ── 1. The regression: a real channel must count ────────────────────────────

def test_an_enabled_slack_binding_counts_as_one(monkeypatch):
    """THE REGRESSION. Reading the row under a key that does not exist
    (`channel_key`/`channel`) made this zero, and the founder's workspace home
    read "0 Channels connected" while Slack was genuinely connected.

    `slack` is a business-lane channel, so its authority is the connection
    catalog's own STUDIO lane — it is not, and must not be, in
    PERSONAL_CHANNEL_SPECS.
    """
    assert _channels_connected(monkeypatch, channel_rows=[_binding_row(key="slack")]) == 1


# ── 2. The original bug: a channel this build no longer carries must not ────

def test_four_telegram_personal_ghosts_count_as_zero(monkeypatch):
    """The founder's real workspace carried exactly these four rows, left
    behind by the first-party Telegram lane's deletion. They cannot carry a
    message; summing them into the strip laundered litter into a fact.

    Authority is `channel_lane_contract_service.PERSONAL_CHANNEL_SPECS` — the
    code that CARRIES the channel — never this catalog, which still lists
    `telegram_personal`.
    """
    from server_modules import channel_lane_contract_service

    assert "telegram_personal" not in channel_lane_contract_service.PERSONAL_CHANNEL_SPECS, (
        "This build carries telegram_personal again — then these rows are not "
        "ghosts and this test is asserting the wrong thing."
    )
    rows = [
        _binding_row(key="telegram_personal", agent_install_id=f"agent-{n}")
        for n in range(1, 5)
    ]
    assert _channels_connected(monkeypatch, channel_rows=rows) == 0


def test_whatsapp_personal_is_a_ghost_too(monkeypatch):
    """The other retired first-party key, so the filter is not a
    telegram-shaped special case."""
    from server_modules import channel_lane_contract_service

    assert "whatsapp_personal" not in channel_lane_contract_service.PERSONAL_CHANNEL_SPECS
    assert _channels_connected(
        monkeypatch, channel_rows=[_binding_row(key="whatsapp_personal")]
    ) == 0


# ── 3. Both at once: the founder's actual workspace ─────────────────────────

def test_slack_beside_the_telegram_ghosts_counts_as_one(monkeypatch):
    """The live shape. Neither direction of the bug is allowed: the ghosts
    must not inflate it to 5, and the filter must not swallow Slack down to
    0."""
    rows = [_binding_row(key="slack")] + [
        _binding_row(key="telegram_personal", agent_install_id=f"agent-{n}")
        for n in range(1, 5)
    ]
    assert _channels_connected(monkeypatch, channel_rows=rows) == 1


# ── 4. The OpenClaw lane, which no binding row ever describes ───────────────

def test_an_openclaw_channel_in_use_counts_without_any_binding_row(monkeypatch):
    """Nothing writes `agent_channel_bindings` for the transported channels,
    so a binding-only count is structurally blind to them and could report
    zero forever while a real Telegram conversation worked."""
    assert _channels_connected(
        monkeypatch, channel_rows=[], openclaw_channels={"openclaw_telegram"}
    ) == 1


def test_binding_and_openclaw_key_for_one_channel_count_once(monkeypatch):
    """Two signals, one channel. Unioned by key, never added — otherwise the
    same working channel is reported twice."""
    assert _channels_connected(
        monkeypatch,
        channel_rows=[_binding_row(key="openclaw_telegram")],
        openclaw_channels={"openclaw_telegram"},
    ) == 1


def test_a_failing_openclaw_read_lowers_the_count_and_never_raises(monkeypatch):
    """A missing contribution understates; raising would blank the strip
    entirely. Slack still counts."""
    assert _channels_connected(
        monkeypatch,
        channel_rows=[_binding_row(key="slack")],
        openclaw_raises=True,
    ) == 1
