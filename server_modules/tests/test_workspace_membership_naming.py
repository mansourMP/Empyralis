"""A brand-new workspace's `name` must be a name, and it must not be a PERSON's.

Two defects, one column, fixed a fortnight apart -- these assertions are the
record of both.

    was  ws_b5c1fa225ae6        a machine id on screen, read as a breach
    was  "Ada Lovelace's Workspace"   a personal folder, read as "a second account"
    now  "Acme"                 the company the signup email belongs to
    now  "Workspace"            when the email carries no company at all

THE SECOND HALF OF THIS FILE IS INVERTED, NOT WEAKENED. Every assertion here
that used to require the possessive `"<person>'s Workspace"` now requires that
it never appears -- so a revival of the old naming fails this file rather than
passing it quietly. That matters more than usual: one workspace per account
(2026-08-23) means the name minted here is the only one a customer ever sees.

These tests exercise the SQLite fallback path (DATABASE_URL unset in the test
environment per this repo's standing rule -- see server_modules/tests/conftest.py's
_isolate_empyralis_state, which points control_plane_repository.LOCAL_IDENTITY_DB_FILE
at a per-test file), which is exactly the path `login_external_user` /
`provision_user_account` hit for OAuth/mobile login on a fresh machine.
"""

import sqlite3
import uuid

import pytest

from server_modules import control_plane_repository, workspace_naming


def _read_local_workspace_name(workspace_id: str) -> str:
    with sqlite3.connect(control_plane_repository.LOCAL_IDENTITY_DB_FILE) as conn:
        row = conn.execute(
            "SELECT name FROM workspace_registry WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
    assert row is not None, f"workspace_registry row missing for {workspace_id}"
    return str(row[0])


# ── The derivation itself (pure, no database) ───────────────────────────────


def test_a_company_email_names_the_workspace_after_the_company():
    assert workspace_naming.derive_new_workspace_name("ada@acme.com") == "Acme"
    assert workspace_naming.derive_new_workspace_name("ADA@Acme.COM") == "Acme"
    # A subdomain is still that company.
    assert workspace_naming.derive_new_workspace_name("ada@mail.acme.com") == "Acme"


def test_a_multi_part_public_suffix_does_not_become_the_name():
    # The bug this guards: "acme.co.uk" naming the workspace "Co".
    assert workspace_naming.derive_new_workspace_name("ops@acme.co.uk") == "Acme"
    assert workspace_naming.derive_new_workspace_name("ops@acme-corp.co.uk") == "Acme Corp"


def test_consumer_and_reserved_domains_carry_no_company_to_name():
    for email in (
        "ada@gmail.com",
        "ada@outlook.com",
        "ada@icloud.com",
        "ada@qq.com",
        "ada@example.com",  # RFC 2606 -- and the address half this repo tests with
        "ada@localhost",
    ):
        assert workspace_naming.derive_new_workspace_name(email) == "Workspace", email


def test_a_missing_or_malformed_email_still_produces_a_real_name():
    for email in (None, "", "   ", "not-an-email"):
        assert workspace_naming.derive_new_workspace_name(email) == "Workspace", repr(email)


def test_the_derivation_cannot_be_handed_a_person():
    """`display_name` is not a parameter, and that is the whole point.

    Structural, because a behavioural test cannot see a keyword that does not
    exist yet -- adding one back is what would reintroduce the personal
    possessive, and it would type-check perfectly.
    """
    import inspect

    params = inspect.signature(workspace_naming.derive_new_workspace_name).parameters
    assert list(params) == ["email"], (
        "derive_new_workspace_name must take the email and nothing else; a "
        "display_name/name parameter is how a workspace gets named after a person."
    )


# ── End to end, through the real create paths ──────────────────────────────


@pytest.mark.asyncio
async def test_new_workspace_gets_a_human_name_not_its_own_id():
    workspace_id = f"ws_{uuid.uuid4().hex[:12]}"
    tenant_id = f"tenant_{uuid.uuid4().hex[:12]}"

    result = await control_plane_repository.ensure_workspace_membership(
        user_id=str(uuid.uuid4()),
        email="ada@acme.com",
        display_name="Ada Lovelace",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role="owner",
    )
    assert result is not None

    stored_name = _read_local_workspace_name(workspace_id)
    # The first defect: stored_name == workspace_id (the machine id on screen).
    assert stored_name != workspace_id
    # The second: named after the person who happened to sign up.
    assert "Ada" not in stored_name
    assert "'s Workspace" not in stored_name
    assert stored_name == "Acme"

    # End-to-end through the read path the UI actually calls.
    fetched = await control_plane_repository.get_workspace_by_id(workspace_id)
    assert fetched is not None
    assert fetched["name"] == "Acme"


@pytest.mark.asyncio
async def test_a_consumer_email_gets_the_neutral_name_never_the_person():
    workspace_id = f"ws_{uuid.uuid4().hex[:12]}"
    tenant_id = f"tenant_{uuid.uuid4().hex[:12]}"

    await control_plane_repository.ensure_workspace_membership(
        user_id=str(uuid.uuid4()),
        email="grace@gmail.com",
        display_name="Grace Hopper",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role="owner",
    )

    stored_name = _read_local_workspace_name(workspace_id)
    assert stored_name != workspace_id
    assert "Grace" not in stored_name
    assert "grace" not in stored_name  # the email prefix, the old fallback
    assert stored_name == "Workspace"


@pytest.mark.asyncio
async def test_signup_names_the_workspace_the_same_way_membership_does():
    """create_local_password_account is the ORDINARY signup path.

    It is a separate function with its own copy of this decision until the
    shared derivation exists -- so it gets its own assertion, from the other
    end, rather than being trusted to agree.
    """
    email = f"ada-{uuid.uuid4().hex[:8]}@acme.com"
    created = await control_plane_repository.create_local_password_account(
        user_id=str(uuid.uuid4()),
        email=email,
        display_name="Ada Lovelace",
        password_hash="not-a-real-hash",
    )
    assert isinstance(created, dict)
    memberships = created.get("memberships") or []
    assert memberships, "signup must bootstrap exactly one workspace membership"
    workspace_id = str(memberships[0].get("workspace_id") or "").strip()
    assert workspace_id

    stored_name = _read_local_workspace_name(workspace_id)
    assert stored_name == "Acme"
    assert "Ada" not in stored_name


@pytest.mark.asyncio
async def test_existing_workspace_name_is_never_overwritten_by_a_later_membership_update():
    workspace_id = f"ws_{uuid.uuid4().hex[:12]}"
    tenant_id = f"tenant_{uuid.uuid4().hex[:12]}"

    await control_plane_repository.ensure_workspace_membership(
        user_id=str(uuid.uuid4()),
        email="ada@acme.com",
        display_name="Ada Lovelace",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role="owner",
    )
    original_name = _read_local_workspace_name(workspace_id)
    assert original_name == "Acme"

    # A second person joins the SAME already-created workspace, from a
    # DIFFERENT company domain. This must not rename the workspace -- only the
    # create path ever sets the name, and a rename here would take a
    # customer's own chosen name away as a side effect of an invite.
    await control_plane_repository.ensure_workspace_membership(
        user_id=str(uuid.uuid4()),
        email="bob@contoso.com",
        display_name="Bob Someone",
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        role="member",
    )

    assert _read_local_workspace_name(workspace_id) == original_name


# ── The DISPLAY rule, and the fallbacks that manufactured the problem ──────


def test_human_workspace_label_never_returns_an_id():
    label = workspace_naming.human_workspace_label
    assert label("Acme", "ws_b5c1fa225ae6") == "Acme"
    # No name at all.
    assert label("", "ws_b5c1fa225ae6") == "Untitled workspace"
    assert label(None, "ws_b5c1fa225ae6") == "Untitled workspace"
    assert label("   ", "ws_b5c1fa225ae6") == "Untitled workspace"
    # A stored name that IS the id is no name at all. This row really exists.
    assert label("ws_b5c1fa225ae6", "ws_b5c1fa225ae6") == "Untitled workspace"
    # Degrades safely when the id is unknown.
    assert label("Acme", "") == "Acme"
    assert label("", "") == "Untitled workspace"


def test_no_surface_falls_back_to_the_workspace_id_as_a_name():
    """Structural, and it is the ROOT CAUSE of the whole family.

    Three server-side `name or workspace_id` fallbacks manufactured an
    id-as-name, and every downstream surface then had to remember to guard
    it. Most did not: the pending-invite banner, the invite email subject,
    the desktop pairing approval's "It will join <b>...</b>" sentence and the
    switcher's own invite rows all printed a machine id at a person.

    A behavioural test cannot catch a FOURTH one being added -- it would
    type-check, render, and only be wrong for a workspace with no name.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for relative in (
        "server_modules/workspace_bootstrap_service.py",
        "server_modules/routes_workspaces.py",
        "server_modules/mcp_oauth_provider.py",
    ):
        path = root / relative
        source = path.read_text(encoding="utf-8")
        # Comments describe the removed fallback on purpose; scanning them
        # would trip this tripwire on its own explanation.
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        for match in re.finditer(r"\bor\s+(?:invite_)?workspace_id\b", code):
            # Only a fallback for a NAME is the defect. `invite_tenant_id =
            # ... or invite_workspace_id` (routes_workspaces) is a tenant
            # resolution and is correct -- flagging it would be a tripwire
            # nobody can satisfy, which is a tripwire someone disables.
            window = code[max(0, match.start() - 300) : match.start()]
            targets = re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", window, re.M)
            if not targets or not re.search(r"name|label", targets[-1], re.I):
                continue
            line_no = code[: match.start()].count("\n") + 1
            offenders.append(f"{relative}:{line_no}  {targets[-1]} = ... {match.group(0)}")

    assert not offenders, (
        "A workspace id is being used as a fallback NAME. Use "
        "workspace_naming.human_workspace_label(name, workspace_id) instead:\n  "
        + "\n  ".join(offenders)
    )
    # CANARY: the scan must actually be reading real source, or it enforces
    # nothing and reports green.
    sample = (root / "server_modules/workspace_bootstrap_service.py").read_text(encoding="utf-8")
    assert "human_workspace_label" in sample and len(sample) > 2000
