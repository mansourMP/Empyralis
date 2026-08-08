"""A registered tool name must survive secret redaction unchanged.

THE BUG THIS LOCKS DOWN
-----------------------
`secret_redaction_service.redact_text` runs a high-entropy sweep over every
`[A-Za-z0-9._~+/=-]{20,}` run and rewrites anything that does not look like a
known-safe shape to `[redacted-secret]`. Its allowlist
(`_SAFE_IDENTIFIER_PATTERN`) permitted exactly ONE separator character between
alphanumeric runs, which cannot express this codebase's own tool-naming
convention: a DOUBLE underscore namespaces a connector from its action
(`project_task__update`, `fleet__schedule_recurring_task`,
`sage_service__update_profile`, plus every connector tool built by
`skills_service.tool_name_for_action`).

So any registered tool whose name contained `__` and was at least 20 characters
long was rewritten to `[redacted-secret]` — 17 of 72 at the time of the fix —
including inside `sage_agent_runtime_service._build_prompt_envelope`, whose
output IS the system prompt handed to the model on every Sage/specialist turn.
An agent cannot call a tool whose name it never sees, and nothing anywhere
raised: it presented as the model declining to assign a task, update a task,
label a task, schedule recurring work, configure another agent, or drive the
browser/computer.

WHY THIS TEST ENUMERATES THE LIVE REGISTRY
------------------------------------------
A hand-copied sample of tool names goes stale the moment someone registers a
new tool, and the failure mode is silent by construction — the whole point is
that a name can never again fall into this hole unnoticed. The names here come
from `skills_service.registered_direct_chat_tool_names_for_logging()`, the same
function `server.py:141` logs as `Registered tools: [...]` at boot, plus the
connector tool names `skills_service.tool_name_for_action` mints from the live
connector catalog.

THE OTHER HALF
--------------
Widening an allowlist inside a secret redactor is only safe if it cannot admit
a credential, so `EnumeratedSecretShapesStillRedactedTests` re-asserts every
secret family `_SENSITIVE_VALUE_PATTERNS` enumerates, and
`AllowlistDoesNotExcuseLongRandomRunsTests` pins the tightening that came with
the fix (segments are capped, so a delimited `<id>.<long-random-secret>` pair is
no longer excused — it was, before).
"""

from __future__ import annotations

import re
import unittest

from server_modules import secret_redaction_service, skills_service


def _live_registered_tool_names() -> list[str]:
    """The names `server.py` logs as `Registered tools: [...]` at boot."""
    return list(skills_service.registered_direct_chat_tool_names_for_logging())


def _live_connector_tool_names() -> list[str]:
    """Connector tool names minted from the live connector catalog.

    `build_connector_direct_chat_tools` composes these per workspace from
    whatever that workspace has connected, so they never appear in the boot log
    and there is no static action registry to enumerate — but they reach the
    model in exactly the same `connector__action` shape and were broken in
    exactly the same way. The axis that actually grows (and that carries the
    long, separator-rich half of the name) is the connector id, so every id in
    the live catalog is crossed with a handful of ordinary action ids.
    """
    from server_modules import connectors_core

    catalog = getattr(connectors_core, "CONNECTOR_CATALOG", None)
    if not isinstance(catalog, dict):
        return []
    actions = ("search", "send_email", "list_files", "create_record", "update_record")
    names: list[str] = []
    for connector_id in catalog:
        for action_id in actions:
            name = skills_service.tool_name_for_action(str(connector_id), action_id)
            if name:
                names.append(name)
    return names


class RegisteredToolNamesSurviveRedactionTests(unittest.TestCase):
    def test_registry_is_not_empty(self) -> None:
        # Guards the test itself: an empty registry would make every
        # assertion below vacuously true, which is how this class of bug
        # survives a suite that is looking right at it.
        names = _live_registered_tool_names()
        self.assertGreater(len(names), 20, "live tool registry came back suspiciously small")

    def test_every_registered_tool_name_survives_redact_text(self) -> None:
        names = _live_registered_tool_names()
        mangled = {
            name: secret_redaction_service.redact_text(name)
            for name in names
            if secret_redaction_service.redact_text(name) != name
        }
        self.assertEqual(
            mangled,
            {},
            "redact_text rewrote registered tool names — an agent cannot call a "
            "tool whose name it never sees in its system prompt",
        )

    def test_every_connector_tool_name_survives_redact_text(self) -> None:
        names = _live_connector_tool_names()
        self.assertGreater(len(names), 0, "live connector catalog produced no tool names")
        mangled = {
            name: secret_redaction_service.redact_text(name)
            for name in names
            if secret_redaction_service.redact_text(name) != name
        }
        self.assertEqual(mangled, {}, "redact_text rewrote connector tool names")

    def test_tool_names_survive_inside_prompt_prose(self) -> None:
        # The live failure was not a bare name in isolation: it was a name
        # embedded in the capability manifest / instruction text that
        # _build_prompt_envelope redacts before the model ever sees it.
        for name in _live_registered_tool_names():
            sentence = f"- `{name}` — call this to do the work. Use {name} directly, not a built-in."
            self.assertEqual(
                secret_redaction_service.redact_text(sentence),
                sentence,
                f"{name} did not survive redaction inside prompt prose",
            )

    def test_tool_names_survive_sanitize_value(self) -> None:
        # The structured path (args_preview, trace payloads, tool catalogs
        # rendered into JSON) goes through sanitize_value, not redact_text.
        names = _live_registered_tool_names()
        payload = {"tools": [{"name": name, "description": f"Run {name}"} for name in names]}
        sanitized = secret_redaction_service.sanitize_value(payload)
        self.assertEqual(
            [item["name"] for item in sanitized["tools"]],
            names,
            "sanitize_value rewrote registered tool names",
        )

    def test_double_underscore_convention_is_actually_exercised(self) -> None:
        # If the naming convention ever changes, this test's premise dies and
        # the assertions above stop covering the reported bug. Fail loudly
        # rather than keep passing for the wrong reason.
        long_double_underscore = [
            name
            for name in _live_registered_tool_names()
            if "__" in name and len(name) >= 20
        ]
        self.assertGreater(
            len(long_double_underscore),
            0,
            "no registered tool name is both >=20 chars and contains '__' — "
            "the shape this test exists to protect is no longer present",
        )


class EnumeratedSecretShapesStillRedactedTests(unittest.TestCase):
    """Every credential family `_SENSITIVE_VALUE_PATTERNS` names, re-asserted.

    Widening the identifier allowlist is only defensible if it cannot rescue a
    real credential. Each sample below must still be rewritten.
    """

    SAMPLES = {
        "openai_sk": "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789",
        "stripe_live": "sk_live_abcdefghijklmnop1234",
        "stripe_restricted": "rk_test_abcdefghijklmnop1234",
        "github_pat": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "github_server": "ghs_abcdefghijklmnopqrstuvwxyz0123456789",
        "slack_bot": "xoxb-123456789012-1234567890123-abcdefghijklmnopqrstuvwx",
        "slack_user": "xoxp-abcdefgh-123456789012-abcdefghijkl",
        "aws_akia": "AKIAIOSFODNN7EXAMPLE",
        "aws_asia": "ASIAIOSFODNN7EXAMPLE",
        "google_api": "AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q",
        "gateway_pairing": "gpair_abcdefghijklmnopqrstuvwx",
        "gateway_token": "ggt_abcdefghijklmnopqrstuvwx",
        "jwt": (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            ".dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        ),
        "private_key": (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA1234567890abcdef\n"
            "-----END RSA PRIVATE KEY-----"
        ),
        "bearer": "Bearer abcdefghijklmnopqrstuvwxyz012345",
        "telegram_bot": "123456789:AAHkmZlHqL9nOpQrStUvWxYz0123456789abcdef",
        "discord_mfa": "mfa.abcdefghijklmnopqrstuvwxyz",
        "authorization_header": "authorization: Basic YWJjOmRlZg==",
        "api_key_assignment": "api_key=abcdefghijklmnopqrst",
        "cookie_header": "set-cookie: session=abcdefghijklmnopqrst",
    }

    def test_every_enumerated_secret_shape_is_still_redacted(self) -> None:
        survivors = {
            label: sample
            for label, sample in self.SAMPLES.items()
            if secret_redaction_service.redact_text(sample) == sample
        }
        self.assertEqual(survivors, {}, "a known credential shape passed through redaction untouched")

    def test_secret_shapes_are_still_redacted_next_to_a_tool_name(self) -> None:
        # The realistic prompt/log line: an agent naming a tool and pasting a
        # token in the same breath. The tool name must live, the token must not.
        for label, sample in self.SAMPLES.items():
            line = f"project_task__update failed with credential {sample} attached"
            redacted = secret_redaction_service.redact_text(line)
            self.assertIn("project_task__update", redacted, f"{label}: tool name was eaten")
            self.assertNotIn(sample, redacted, f"{label}: credential survived")


class AllowlistDoesNotExcuseLongRandomRunsTests(unittest.TestCase):
    """The fix is net-tighter, not merely wider.

    The old single-separator allowlist excused ANY length of lowercase run, so a
    delimited `<id>.<long-random-secret>` credential pair — a real shape, e.g.
    Airtable-style personal access tokens with their 64-hex half — was waved
    through whenever it happened to be all lowercase. Segments are now capped at
    24 characters (the longest segment in the live registry is 13), so those are
    caught.

    Stated plainly, because an allowlist that overclaims is worse than none: a
    SHORT all-lowercase random run joined by a single separator is still
    excused. That is unchanged from before this fix, it is the price of not
    redacting ordinary identifiers out of system prompts and memory files, and
    every prefixed credential family is matched by `_SENSITIVE_VALUE_PATTERNS`
    before the allowlist is consulted at all.
    """

    def test_long_random_segments_are_not_allowlisted(self) -> None:
        for sample in (
            "patabcdefghijklmn.0123456789abcdef0123456789abcdef0123456789abcdef01234567",
            "account7.a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
            "session.9zqm1xv7ku3pd0shq4wbn2mfy8ejr6tc",
        ):
            self.assertNotEqual(
                secret_redaction_service.redact_text(sample),
                sample,
                f"long random run {sample!r} was excused by the identifier allowlist",
            )

    def test_separator_runs_longer_than_the_convention_are_not_allowlisted(self) -> None:
        # `__` is the convention. Nothing beyond it needs excusing, and a token
        # padded with separator runs is not an identifier this codebase mints.
        self.assertNotEqual(
            secret_redaction_service.redact_text("abcd---efgh---ijkl---mnop"),
            "abcd---efgh---ijkl---mnop",
        )

    def test_uppercase_tokens_are_not_allowlisted_as_identifiers(self) -> None:
        # The allowlist is lowercase-only by construction; base64/base64url
        # material almost always carries uppercase.
        self.assertIsNone(
            secret_redaction_service._SAFE_IDENTIFIER_PATTERN.match("Abcdefghij__Klmnopqrst"),
        )

    def test_allowlist_shape_matches_the_registry_it_exists_for(self) -> None:
        pattern = secret_redaction_service._SAFE_IDENTIFIER_PATTERN
        for name in _live_registered_tool_names():
            if len(name) < 20:
                continue
            self.assertIsNotNone(
                pattern.match(name),
                f"{name} is a live tool name the identifier allowlist does not recognise",
            )

    def test_registry_segments_stay_inside_the_allowlist_cap(self) -> None:
        # Documents the headroom the cap was chosen against: if a future tool
        # name carries a segment longer than the cap, this fails here rather
        # than silently redacting the tool out of a system prompt.
        longest = max(
            (
                len(segment)
                for name in _live_registered_tool_names()
                for segment in re.split(r"[._-]+", name)
            ),
            default=0,
        )
        self.assertLessEqual(longest, 24, "a registered tool name segment now exceeds the allowlist cap")


if __name__ == "__main__":
    unittest.main()
