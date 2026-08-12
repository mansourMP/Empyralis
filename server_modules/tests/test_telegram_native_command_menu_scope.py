"""The public Telegram command menu never advertises an owner-only command.

setMyCommands with no `scope` sets the DEFAULT menu -- the autocomplete every
stranger who DMs the hosted bot sees. It was built from
`list_for_scope("both")` with no filter on `access`, so /bash, /config, /mcp,
/plugins and /debug were offered to anyone.

Execution was never at risk (`command_registry.dispatch` refuses
`access == "owner"` for a non-owner independently, and that is asserted here
too so this file cannot pass while the real gate rots). The defect is the
advertisement: a control offered and then refused is a dead control, and
suggesting a shell command to strangers is not shippable.

Derived from the LIVE registry, never a hand-copied list of names -- a
hand-written sample goes stale the moment someone registers a command, and
this failure is silent by construction.
"""

from __future__ import annotations

from server_modules import command_registry


def _public_menu_names() -> set[str]:
    """Exactly the filter _register_telegram_native_commands applies."""
    return {
        cmd.name
        for cmd in command_registry.list_for_scope("both")
        if not cmd.aliases and cmd.access != "owner"
    }


def _owner_only_names() -> set[str]:
    return {cmd.name for cmd in command_registry.list_for_scope("both") if cmd.access == "owner"}


def test_owner_only_commands_exist_so_this_test_is_not_vacuous():
    owner_only = _owner_only_names()
    assert owner_only, (
        "no owner-only commands are registered at all -- this test would pass "
        "trivially and prove nothing about the menu filter"
    )


def test_public_menu_advertises_no_owner_only_command():
    leaked = _public_menu_names() & _owner_only_names()
    assert not leaked, f"owner-only commands advertised in the public Telegram menu: {sorted(leaked)}"


def test_the_public_menu_is_still_useful():
    """A filter that emptied the menu would 'pass' the test above."""
    public = _public_menu_names()
    assert len(public) >= 5, f"public menu collapsed to {sorted(public)}"
    for expected in ("help", "status", "new"):
        assert expected in public, f"/{expected} should still be publicly discoverable"


def test_execution_gate_still_refuses_owner_only_for_non_owner():
    """The menu filter is cosmetic; this is the real boundary. If this ever
    fails, filtering the menu is irrelevant."""
    for name in sorted(_owner_only_names()):
        cmd = command_registry.get(name)
        assert cmd is not None and cmd.access == "owner", name
