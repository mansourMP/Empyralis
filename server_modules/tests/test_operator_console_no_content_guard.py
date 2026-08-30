"""The founder's non-negotiable for the operator console: operators see
metadata and behaviour, never customer content. This is the regex guard
CLAUDE.md asks for -- a scan over every raw SQL string in operator_console_
service.py, with a canary proving the scan actually reaches real SQL text
(not zero strings, and not a scan that would pass a genuinely bad query).

Forbidden columns, verbatim from the task: `project_documents.body`,
`project_document_revisions.body`, `project_document_revisions.diff`,
`project_tasks.description`, `agent_private_memory_notes.content`,
`agent_trace_events.payload`, `agent_channel_events.text`, and
`activity_ledger_events`'s `payload`/`summary`/`artifacts`. Reduced to bare
tokens (a column can be selected unqualified, table-qualified, or aliased,
so the guard cannot require a table prefix): body, diff, description,
content, payload, text, summary, artifacts.

The one legitimate SQL use of any of these words in this module is the
`::text[]` cast in the failures query (agent_action_events.status is TEXT,
and `= ANY($1::text[])` needs the array cast) -- a data TYPE keyword, not a
column reference. The scanner strips `::text`/`::text[]` casts before
looking for the bare word "text" so that legitimate cast does not mask a
real violation elsewhere, and the canary test below proves the strip does
not also swallow a real "text" column reference.
"""

from __future__ import annotations

import re
import unittest

from server_modules import operator_console_service


FORBIDDEN_COLUMN_TOKENS = (
    "body",
    "diff",
    "description",
    "content",
    "payload",
    "text",
    "summary",
    "artifacts",
)

_CAST_STRIP_RE = re.compile(r"::text(\[\])?", re.IGNORECASE)


def find_forbidden_tokens(sql: str) -> list:
    """Return every forbidden token that appears as a whole word in `sql`,
    after stripping legitimate `::text`/`::text[]` type casts. Used both on
    the module's real SQL constants and, in the canary test below, on a
    deliberately bad snippet."""
    scrubbed = _CAST_STRIP_RE.sub("", sql)
    found = []
    for token in FORBIDDEN_COLUMN_TOKENS:
        if re.search(r"\b" + re.escape(token) + r"\b", scrubbed, re.IGNORECASE):
            found.append(token)
    return found


def _discover_sql_constants() -> dict:
    """Every module-level string constant whose name contains '_SQL' --
    deliberately NOT a hand-maintained list of names, so a new query added
    later is picked up automatically rather than silently skipped because
    nobody updated a list in this test file too (CLAUDE.md: 'a check that
    derives its expectations from the thing it checks is blind')."""
    constants = {}
    for name, value in vars(operator_console_service).items():
        if "_SQL" in name and isinstance(value, str):
            constants[name] = value
    return constants


class OperatorConsoleNoContentGuardTests(unittest.TestCase):
    def test_canary_the_scan_actually_reaches_real_sql_and_catches_a_real_violation(self) -> None:
        """Proves find_forbidden_tokens() is a live detector, not a check
        that always passes. If this test ever fails, the guard below is not
        protecting anything."""
        bad_sql = "SELECT id, body FROM project_documents WHERE tenant_id = $1"
        assert find_forbidden_tokens(bad_sql) == ["body"]

        bad_sql_2 = "SELECT summary, artifacts, payload FROM activity_ledger_events"
        assert set(find_forbidden_tokens(bad_sql_2)) == {"summary", "artifacts", "payload"}

    def test_the_text_cast_exclusion_does_not_also_hide_a_real_text_column(self) -> None:
        """Guards the guard's own exclusion: a genuine `agent_channel_
        events.text` reference must still be caught even though this
        module's own `::text[]` cast must not be."""
        legitimate_cast = "WHERE status = ANY($1::text[])"
        assert find_forbidden_tokens(legitimate_cast) == []

        real_violation = "SELECT text FROM agent_channel_events WHERE id = $1"
        assert find_forbidden_tokens(real_violation) == ["text"]

    def test_discovery_finds_a_real_nonzero_set_of_sql_constants(self) -> None:
        """The other half of the canary: if this ever finds zero constants
        (a rename away from the `_SQL` naming convention, a refactor that
        moves the queries elsewhere), the scan below would silently pass by
        scanning nothing. Fail loudly instead."""
        constants = _discover_sql_constants()
        assert len(constants) >= 7, (
            f"expected at least 7 SQL constants in operator_console_service, found "
            f"{len(constants)}: {sorted(constants)}. If queries were intentionally "
            "consolidated or renamed, update this floor -- do not delete the check."
        )
        # Every constant actually contains a SELECT -- proves this found real
        # queries, not e.g. a stray docstring that happens to match '_SQL'.
        for name, sql in constants.items():
            assert "SELECT" in sql.upper(), f"{name} does not look like SQL: {sql[:80]!r}"

    def test_no_forbidden_content_column_appears_in_any_operator_console_query(self) -> None:
        constants = _discover_sql_constants()
        violations = {}
        for name, sql in constants.items():
            found = find_forbidden_tokens(sql)
            if found:
                violations[name] = found
        assert not violations, (
            "operator_console_service.py SQL references forbidden content "
            f"columns: {violations}. Operators see metadata and behaviour, "
            "never customer content (CLAUDE.md, non-negotiable)."
        )


if __name__ == "__main__":
    unittest.main()
