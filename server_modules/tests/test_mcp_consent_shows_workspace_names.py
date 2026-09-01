"""The MCP consent screen names workspaces. It never shows a raw id.

THE INCIDENT, 2026-08-22. The founder had a second workspace he had forgotten
creating. The consent screen offered him a choice between `ws_d745c1b2eb2c`
and `ws_b5c1fa225ae6`, he picked the wrong one, and then spent an hour reading
a perfectly working Connector as broken. An id is an ADDRESS; a person cannot
recognise one, and this is the single screen where not recognising one costs
them access to their own data.

WHAT IS SHOWN AND WHAT IS SUBMITTED ARE DIFFERENT THINGS, and only the first
changed:

    shown      the workspace's NAME          <- this is the fix
    submitted  the workspace id, unchanged   <- radio value / hidden input
    authorized the workspace id, unchanged   <- _accessible_workspace_ids,
                                                still the gate on POST

So these tests assert BOTH halves. A "fix" that stopped submitting the id
would break consent entirely while passing a names-only assertion.
"""

from __future__ import annotations

import re

import pytest

from server_modules import mcp_oauth_provider as oauth
from server_modules import workspace_naming


class _Client:
    client_id = "client-1"
    client_name = "Claude"


def _render(workspaces):
    return oauth._render_consent_form(
        client=_Client(),
        scopes=["empyralis:read"],
        workspaces=workspaces,
        ticket="t",
        csrf_token="c",
    )


def _visible_text(html: str) -> str:
    """Everything a person can read — <style> and tag/attribute text removed.

    Attribute values are where the id legitimately still lives (radio `value`,
    the hidden input), so a naive substring search over the raw HTML would
    report the id as visible when it is not.
    """
    body = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", body)


# ── The screen ─────────────────────────────────────────────────────────────


def test_one_workspace_is_named_and_its_id_is_not_on_screen():
    html = _render([{"id": "ws_d745c1b2eb2c", "name": "Acme", "hint": ""}])
    assert "Acme" in _visible_text(html)
    assert "ws_d745c1b2eb2c" not in _visible_text(html)
    # ...but it is still what gets submitted.
    assert '<input type="hidden" name="workspace_id" value="ws_d745c1b2eb2c">' in html


def test_choosing_between_workspaces_is_a_choice_between_NAMES():
    html = _render(
        [
            {"id": "ws_d745c1b2eb2c", "name": "Acme", "hint": ""},
            {"id": "ws_b5c1fa225ae6", "name": "Side Project", "hint": ""},
        ]
    )
    visible = _visible_text(html)
    assert "Acme" in visible and "Side Project" in visible
    # THE INCIDENT: neither id may be the thing a person is asked to pick.
    assert "ws_d745c1b2eb2c" not in visible
    assert "ws_b5c1fa225ae6" not in visible
    # Both are still the submitted values, and the first is preselected.
    assert 'value="ws_d745c1b2eb2c" checked' in html
    assert 'value="ws_b5c1fa225ae6"' in html


def test_an_id_appears_ONLY_to_tell_two_identical_names_apart():
    """Secondary disambiguation, not a label — and only where it is needed.

    Two workspaces really can share a name (nothing enforces uniqueness), and
    two indistinguishable rows are a worse choice than two ugly ones. But
    showing the id on EVERY row would put the machine id back on the screen
    for everyone, which is the defect.
    """
    html = _render(
        [
            {"id": "ws_aaaa1111", "name": "Workspace", "hint": "ws_aaaa1111"},
            {"id": "ws_bbbb2222", "name": "Workspace", "hint": "ws_bbbb2222"},
        ]
    )
    visible = _visible_text(html)
    assert "ws_aaaa1111" in visible and "ws_bbbb2222" in visible
    assert visible.count("Workspace") >= 2


def test_an_unnamed_workspace_says_so_in_words():
    html = _render([{"id": "ws_b5c1fa225ae6", "name": "Untitled workspace", "hint": ""}])
    assert "Untitled workspace" in _visible_text(html)
    assert "ws_b5c1fa225ae6" not in _visible_text(html)


def test_a_name_cannot_inject_markup():
    html = _render([{"id": "ws_1", "name": '<img src=x onerror="alert(1)">', "hint": ""}])
    assert "<img" not in html
    assert "&lt;img" in html


# ── The resolver that feeds it ─────────────────────────────────────────────


def _stub_accessible(monkeypatch, ids: list[str]) -> None:
    """Isolate the seam this file actually tests -- naming/degradation --
    from live membership resolution, which is its own concern with its own
    coverage (test_mcp_oauth_provider.py, test_mcp_oauth_connector_flow.py).

    ``_accessible_workspace_ids`` used to be a pure function of
    ``current_user["workspace_ids"]``, so a bare ``{"workspace_ids": [...]}``
    fixture was a faithful producer shape. It now resolves LIVE membership
    (``auth.workspace_access_map``) and drops anything that does not
    genuinely resolve to a real workspace -- so a bare id with no backing
    row is correctly treated as orphaned, not as "this test's fixture data".
    Stubbing the resolver itself keeps that fixture data meaningful again
    without dragging live control-plane state into a naming test.
    """
    monkeypatch.setattr(oauth, "_accessible_workspace_ids", lambda _current_user: list(ids))


@pytest.mark.asyncio
async def test_named_workspaces_degrade_to_a_word_never_to_an_id(monkeypatch):
    """A workspace whose name cannot be read must not fall back to its id.

    Three cases in one pass, because they must all land in the same place:
    a real name, a workspace whose stored name IS its own id (this really
    exists), and a lookup that raises.
    """

    async def fake_get_workspace_by_id(workspace_id: str):
        if workspace_id == "ws_named":
            return {"name": "Acme"}
        if workspace_id == "ws_selfnamed":
            return {"name": "ws_selfnamed"}
        raise RuntimeError("control plane unavailable")

    from server_modules import control_plane_repository as cpr

    monkeypatch.setattr(cpr, "get_workspace_by_id", fake_get_workspace_by_id)
    _stub_accessible(monkeypatch, ["ws_named", "ws_selfnamed", "ws_unreadable"])

    result = await oauth._named_accessible_workspaces(
        {"workspace_ids": ["ws_named", "ws_selfnamed", "ws_unreadable"]}
    )
    by_id = {row["id"]: row["name"] for row in result}
    assert by_id["ws_named"] == "Acme"
    assert by_id["ws_selfnamed"] == workspace_naming.UNNAMED_WORKSPACE_LABEL
    assert by_id["ws_unreadable"] == workspace_naming.UNNAMED_WORKSPACE_LABEL
    # A failure to read one name must not drop the workspace from the choice.
    assert [row["id"] for row in result] == ["ws_named", "ws_selfnamed", "ws_unreadable"]


@pytest.mark.asyncio
async def test_the_hint_is_populated_only_on_a_collision(monkeypatch):
    async def fake_get_workspace_by_id(workspace_id: str):
        return {"name": "Acme" if workspace_id == "ws_1" else "Twin"}

    from server_modules import control_plane_repository as cpr

    monkeypatch.setattr(cpr, "get_workspace_by_id", fake_get_workspace_by_id)
    _stub_accessible(monkeypatch, ["ws_1", "ws_2"])

    unique = await oauth._named_accessible_workspaces({"workspace_ids": ["ws_1", "ws_2"]})
    assert [row["hint"] for row in unique] == ["", ""]

    async def both_twins(workspace_id: str):
        return {"name": "Twin"}

    monkeypatch.setattr(cpr, "get_workspace_by_id", both_twins)
    collided = await oauth._named_accessible_workspaces({"workspace_ids": ["ws_1", "ws_2"]})
    assert [row["hint"] for row in collided] == ["ws_1", "ws_2"]


# ── The boundary that must NOT have moved ──────────────────────────────────


def test_authorization_still_decides_on_the_ID_not_the_name():
    """Structural. Display changed; the gate did not.

    `decide_consent` re-checks the submitted workspace against
    `_accessible_workspace_ids`. If someone ever swaps that for the named
    resolver, consent starts being granted on a string a workspace can be
    RENAMED to, which is a different and much worse bug than the one fixed.
    """
    import inspect

    source = inspect.getsource(oauth.EmpyralisOAuthProvider.decide_consent)
    assert "_accessible_workspace_ids(current_user)" in source
    assert "_named_accessible_workspaces" not in source


def test_the_form_takes_named_workspaces_and_cannot_be_handed_bare_ids():
    """A `workspace_ids` parameter is what the old, defective form took.

    Structural because the regression is a signature change that would
    type-check and render a perfectly valid page — of ids.
    """
    import inspect

    params = inspect.signature(oauth._render_consent_form).parameters
    assert "workspaces" in params
    assert "workspace_ids" not in params
