"""The phone sweep must never eat technical output a customer is reading.

Observed live 2026-08-16, in the founder's own production chat: `uname -a`
rendered as "Linux cc2ee0a795f4 [redacted-phone]-generic" — the kernel
version 6.8.0-124 matched the phone pattern because its boundaries treated
`-`/`.` as phone separators rather than token glue. A phone number is never
embedded inside a hyphenated identifier, and dot-only compact tokens
(versions, IPs, decimals) are overwhelmingly technical on a surface whose
job is showing command output.

Real phone shapes must keep matching — weakening the detector to fix a
boundary problem is the trade this codebase's redaction entry forbids.
"""

from server_modules.secret_redaction_service import redact_text


def _phone_hit(text: str) -> bool:
    return "[redacted-phone]" in redact_text(text)


# ── technical output stays intact ────────────────────────────────────────


def test_kernel_version_survives():
    out = redact_text("Linux cc2ee0a795f4 6.8.0-124-generic #124-Ubuntu SMP x86_64")
    assert out == "Linux cc2ee0a795f4 6.8.0-124-generic #124-Ubuntu SMP x86_64"


def test_semver_build_string_survives():
    assert not _phone_hit("v2.4.3-build.2026.08.15")


def test_ip_address_survives():
    assert not _phone_hit("connect to 192.168.100.200 then retry")


def test_decimal_number_survives():
    assert not _phone_hit("pi is 3.14159265 exactly enough")


def test_digit_run_inside_hyphenated_identifier_survives():
    # The marker itself may hit OTHER rules (high-entropy); the claim here is
    # only that the PHONE rule does not fire on an embedded digit run.
    assert not _phone_hit("marker RELIABILITY-2-1786807636 done")


# ── real phones keep matching ────────────────────────────────────────────


def test_international_phone_still_redacts():
    assert _phone_hit("call me at +1 415 555 0134 ok")


def test_spaced_phone_still_redacts():
    assert _phone_hit("my number is 8 861 747 837 thanks")


def test_compact_plus_phone_still_redacts():
    assert _phone_hit("dial +33612345678 please")


def test_parenthesised_phone_still_redacts():
    assert _phone_hit("ring (415) 555-0134 now")


# ── ISO dates stay intact (third instance of this same class of bug) ─────
#
# Found 2026-08-19 in a REAL agent's MEMORY.md on production, not in a test.
# A launch audit the agent had written for itself read:
#
#     "[redacted-phone] — Pre-launch environment audit
#      (platform launches ~[redacted-phone])"
#
# Those were dates. `2026-08-19` is digits-and-hyphens of phone-ish length,
# so the sweep matched it. Memory is redacted BEFORE it reaches disk (see
# test_memory_write_redaction_live_path.py), making this a PERMANENT edit
# rather than a display filter — the agent's own record of when things
# happened was destroyed.
#
# It survived the earlier hardening passes above because a full ISO
# timestamp (`2026-08-19T09:13:06Z`) never matched: the trailing `T` trips
# the sweep's own `(?![\w.-])` boundary. Only BARE dates were eaten, and
# nothing asserted on those.


def test_bare_iso_date_survives():
    assert redact_text("2026-08-19") == "2026-08-19"


def test_iso_dates_in_prose_survive():
    text = "2026-08-19 — Pre-launch environment audit (platform launches ~2026-08-31)"
    assert redact_text(text) == text


def test_two_adjacent_iso_dates_survive():
    # The sweep's character class includes spaces, so these arrive as ONE
    # match — a single-date check would miss it and redact both.
    assert redact_text("2026-08-19 2026-08-20") == "2026-08-19 2026-08-20"


def test_iso_timestamps_still_survive():
    text = "opened 2026-08-19T09:13:06Z and closed 2026-08-19T09:13:39Z"
    assert redact_text(text) == text


# ── the exemption must not widen into phone coverage ─────────────────────
#
# It is shaped exactly `\d{4}-\d{2}-\d{2}`. If any of these stop
# redacting, it has been loosened too far.


def test_hyphenated_phone_still_redacted():
    assert _phone_hit("call me at +1-415-555-0142")
    assert _phone_hit("phone 415-555-0142")


def test_compact_and_spaced_phones_still_redacted():
    assert _phone_hit("reach me on 4155550142")
    assert _phone_hit("+44 20 7946 0958")
