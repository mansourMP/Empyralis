"""Inviting someone already inside is refused, not minted.

Re-inviting an existing member returned 200 and created a real invite row
and token, which parked a permanent "You've been invited · Join · Decline"
banner over a workspace that person was already working in. Reproduced
live before the fix: the roster held her, and her own pending-invites list
held an invite to that same workspace.

The roster read this needs was ALREADY happening one block earlier for the
seat cap, so the check costs no extra query.
"""
from __future__ import annotations

import unittest

from server_modules import workspace_member_policy as policy


ROSTER = [
    {"user_id": "u1", "email": "amara.owner@example.com", "role": "owner", "status": "active"},
    {"user_id": "u2", "email": "devi.member@example.com", "role": "member", "status": "active"},
]


class MemberWithEmailTests(unittest.TestCase):
    def test_an_existing_member_is_found_by_email(self):
        found = policy.member_with_email(ROSTER, "devi.member@example.com")
        self.assertIsNotNone(found)
        self.assertEqual(found["role"], "member")

    def test_matching_is_case_and_whitespace_insensitive(self):
        self.assertIsNotNone(policy.member_with_email(ROSTER, "  DEVI.Member@Example.com "))

    def test_a_stranger_is_not_a_member(self):
        self.assertIsNone(policy.member_with_email(ROSTER, "nobody@example.com"))

    def test_a_blank_email_never_matches(self):
        """A blank must never collide with a row whose email is missing —
        that would refuse every invite on a roster with one broken row."""
        for value in ("", "   ", None):
            self.assertIsNone(policy.member_with_email(ROSTER, value))
        self.assertIsNone(policy.member_with_email([{"email": "", "status": "active"}], ""))

    def test_a_removed_member_is_not_already_inside(self):
        """Re-inviting someone who was removed is a REAL invite. Treating a
        removed membership as current would make that impossible."""
        roster = [{"user_id": "u3", "email": "gone@example.com", "status": "removed"}]
        self.assertIsNone(policy.member_with_email(roster, "gone@example.com"))

    def test_a_missing_status_is_treated_as_active(self):
        """Same default as active_member_count and is_already_a_member —
        the three must agree about what "in this workspace" means."""
        roster = [{"user_id": "u4", "email": "x@example.com"}]
        self.assertIsNotNone(policy.member_with_email(roster, "x@example.com"))

    def test_it_agrees_with_the_seat_counter(self):
        """The expected count comes from the OTHER function, so the two
        cannot drift about which rows are active."""
        active = [m for m in ROSTER if policy.member_with_email(ROSTER, m["email"])]
        self.assertEqual(len(active), policy.active_member_count(ROSTER))

    def test_junk_rows_do_not_crash_the_scan(self):
        self.assertIsNone(policy.member_with_email([None, "x", 7], "devi.member@example.com"))


class SentenceTests(unittest.TestCase):
    def test_it_names_the_person_and_the_role(self):
        line = policy.already_a_member_sentence(email="devi@example.com", role="member")
        self.assertIn("devi@example.com", line)
        self.assertIn("member", line)

    def test_it_survives_a_missing_role(self):
        line = policy.already_a_member_sentence(email="devi@example.com")
        self.assertIn("devi@example.com", line)
        self.assertNotIn(" as ", line)

    def test_it_suggests_nothing(self):
        """There is no action to offer: the outcome the owner wanted is
        already true. A tool labels; it does not lecture."""
        line = policy.already_a_member_sentence(email="devi@example.com", role="member")
        for nag in ("try", "instead", "please", "you should", "remove"):
            self.assertNotIn(nag, line.lower())


class RouteWiringTests(unittest.TestCase):
    """Structural: the check must sit on the roster read that already
    happens, and must fail OPEN when that read failed — the same posture
    the seat cap keeps, for the same reason.
    """

    def _source(self):
        import inspect

        from server_modules import routes_workspaces

        return inspect.getsource(routes_workspaces.create_workspace_invite_route)

    def test_the_route_consults_the_roster(self):
        self.assertIn("member_with_email", self._source())

    def test_it_is_inside_the_roster_read_guard(self):
        """`current_members is not None` is what makes an unreadable roster
        let the invite through instead of refusing it."""
        source = self._source()
        guard = source.index("if current_members is not None:")
        check = source.index("member_with_email")
        self.assertLess(guard, check, "the check must be inside the roster-read guard")

    def test_it_runs_before_a_seat_is_counted(self):
        """Someone already inside occupies a seat they already hold; asking
        the seat cap about them first would refuse a full workspace for a
        person who needs no new seat."""
        source = self._source()
        self.assertLess(source.index("member_with_email"), source.index("assert_seat_available"))


if __name__ == "__main__":
    unittest.main()
