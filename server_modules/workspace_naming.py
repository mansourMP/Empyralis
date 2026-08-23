"""The name a brand-new workspace is born with.

ONE workspace per account (2026-08-23). Removing the self-serve "New
workspace" path makes the workspace a person opens every day the ONLY one
they have, so the name it is born with is the name they live with -- and
`"Mansur's Workspace"` reads as a personal folder, which is precisely how
the founder came to experience a workspace he had forgotten creating as "a
second account". A workspace is where a COMPANY works. Name it after one.

WHAT SIGNUP ACTUALLY KNOWS, and it is not much:

    POST /api/auth/signup   ->  email, password, name, (invite codes)

There is NO organisation/company field anywhere in the signup form
(frontend/app/signup/page.tsx renders Name / Email / Password / invite code
and nothing else) and none was invented here -- adding a field to the first
screen a customer ever sees is a product decision, not a side effect of a
naming fix. So the one company signal on hand is the EMAIL DOMAIN, and it is
a real one: `ada@acme.com` is Ada at Acme in a way `ada@gmail.com` is not.

    ada@acme.com        ->  "Acme"          the registrable label, title-cased
    ops@acme-corp.co.uk ->  "Acme Corp"     multi-part suffix handled
    ada@gmail.com       ->  "Workspace"     consumer mail: no company to name
    ada@example.com     ->  "Workspace"     RFC 2606 reserved
    (no email at all)   ->  "Workspace"

`"Workspace"` is the deliberate fallback and it is NOT a placeholder to be
improved later with something person-derived. A neutral name says nothing;
a possessive one says the wrong thing, and saying the wrong thing is the
defect being fixed. It is renameable in one field
(frontend/lib/workspace/fleet/WorkspaceNameSection.tsx), which a machine id
never was.

NOTHING HERE EVER RENAMES AN EXISTING WORKSPACE. Every caller uses this only
on the branch that CREATES the row; `ensure_workspace_membership`'s own
comment and test already pin that, and this module inherits it rather than
restating it in code.
"""

from __future__ import annotations

# Mailbox providers a person signs up from as a PERSON. A hit here is not a
# company, so there is nothing to name the workspace after and the neutral
# fallback is the honest answer. Deliberately a denylist and not an
# allowlist: an unknown domain is far more likely to be a real company's
# than a consumer provider we have never heard of, and being wrong that way
# costs a rename rather than a wrong-shaped default for every business.
_CONSUMER_EMAIL_DOMAINS = frozenset(
    {
        "aol.com",
        "duck.com",
        "fastmail.com",
        "gmail.com",
        "gmx.com",
        "gmx.de",
        "gmx.net",
        "googlemail.com",
        "hey.com",
        "hotmail.co.uk",
        "hotmail.com",
        "hotmail.fr",
        "icloud.com",
        "live.co.uk",
        "live.com",
        "mac.com",
        "mail.com",
        "mail.ru",
        "me.com",
        "msn.com",
        "naver.com",
        "outlook.com",
        "pm.me",
        "proton.me",
        "protonmail.com",
        "qq.com",
        "tuta.io",
        "tutanota.com",
        "yahoo.co.jp",
        "yahoo.co.uk",
        "yahoo.com",
        "yandex.com",
        "yandex.ru",
        "zoho.com",
        "126.com",
        "163.com",
    }
)

# RFC 2606 / RFC 6761 reserved names, plus what a local stack produces. These
# are not companies either, and `ada@example.com` -- the address half this
# repo's own tests are written with -- must not mint a workspace called
# "Example".
_RESERVED_EMAIL_DOMAINS = frozenset(
    {
        "example.com",
        "example.net",
        "example.org",
        "invalid",
        "localhost",
        "test",
    }
)

# Second-level labels that are a SUFFIX rather than a name, so `acme.co.uk`
# is Acme and not Co. Not a public-suffix list and not trying to be: this
# handles the shapes that actually occur, and anything it misses degrades to
# a slightly-off name in one field a person can edit, never to a wrong id.
_SECOND_LEVEL_SUFFIX_LABELS = frozenset(
    {"ac", "co", "com", "edu", "gov", "govt", "mil", "net", "or", "org", "sch"}
)

NEUTRAL_WORKSPACE_NAME = "Workspace"


def _company_label_from_domain(domain: str) -> str:
    """The registrable label of a domain, or "" when there is no company in it."""
    clean = str(domain or "").strip().lower().strip(".")
    if not clean or "@" in clean or " " in clean:
        return ""
    if clean in _CONSUMER_EMAIL_DOMAINS or clean in _RESERVED_EMAIL_DOMAINS:
        return ""
    labels = [label for label in clean.split(".") if label]
    if not labels:
        return ""
    if len(labels) == 1:
        # A bare hostname with no dot at all ("localhost", an intranet name).
        # Not a registrable domain, so not a company.
        return ""
    # Walk in from the right past the public suffix: the TLD always, plus one
    # more when that one is itself a suffix label ("co" in acme.co.uk).
    index = len(labels) - 2
    if index > 0 and labels[index] in _SECOND_LEVEL_SUFFIX_LABELS:
        index -= 1
    label = labels[index]
    if not label or label in _SECOND_LEVEL_SUFFIX_LABELS:
        return ""
    return label


def _titleize(label: str) -> str:
    """"acme-corp" -> "Acme Corp". Hyphens and underscores are word breaks."""
    words = [word for word in label.replace("_", "-").split("-") if word]
    if not words:
        return ""
    return " ".join(word[:1].upper() + word[1:] for word in words)


def derive_new_workspace_name(email: str | None) -> str:
    """Name a workspace ABOUT TO BE CREATED for the account signing up.

    Takes the email and nothing else. `display_name` is deliberately NOT a
    parameter: it is the one input that can only ever produce a name after a
    person, which is the thing this function exists to stop. Callers that
    still hold a display name should not pass it here -- there is nowhere
    for it to go.
    """
    normalized = str(email or "").strip().lower()
    if "@" not in normalized:
        return NEUTRAL_WORKSPACE_NAME
    domain = normalized.rsplit("@", 1)[1]
    name = _titleize(_company_label_from_domain(domain))
    return name or NEUTRAL_WORKSPACE_NAME


# ── Showing a workspace to a person ────────────────────────────────────────

UNNAMED_WORKSPACE_LABEL = "Untitled workspace"


def human_workspace_label(name: str | None, workspace_id: str | None) -> str:
    """The name to SHOW, given the name that is STORED. Never an id.

    An id is an address. Rendering one at a person reads as a breach of their
    account -- the founder's own words about `ws_b5c1fa225ae6` on screen --
    and on 2026-08-22 it cost him an hour: the MCP consent screen asked him to
    choose between two ids and he consented a Connector to the wrong one.

    THE FALLBACK IS THE WHOLE POINT, and it used to be `or workspace_id` in
    three separate places (workspace_bootstrap_service's `label`,
    _workspace_summary_payload, and the pending-invite listing). Each one
    manufactured an id-as-name that every downstream surface then had to
    remember to guard, and most did not: the invite banner, the invite email
    subject, the desktop pairing approval and the switcher's own invite rows
    all printed it. One fallback, stated once, is the fix.

    A stored name that IS the id counts as no name at all -- that row really
    exists (observed live), partly because onboarding used to auto-submit the
    label it was handed straight back as the workspace's name.

    This does NOT rename anything. It is a display rule; the stored row is
    untouched, and `derive_new_workspace_name` above is the only thing that
    ever chooses a name, and only for a row being created.
    """
    clean_name = str(name or "").strip()
    clean_id = str(workspace_id or "").strip()
    if not clean_name or (clean_id and clean_name == clean_id):
        return UNNAMED_WORKSPACE_LABEL
    return clean_name
