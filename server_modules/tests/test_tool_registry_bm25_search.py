"""
BM25 search correctness tests for tool_registry_service.search_tool_registry —
docs/design/audit-tool-reliability.md §3.1: the old scorer was plain binary
token/substring overlap, not real BM25 despite the module's own header
comment claiming otherwise, and it demonstrably failed on realistic
paraphrases. The audit ran 7 realistic user-intent probes through the real
function and got zero results on 4 of them ("let the team know on chat",
"schedule a meeting with the client", "ping the customer about the
invoice", "look something up online") plus a coincidental false positive
(computer__notify surfacing for "put this on my calendar" purely because
"put" is a substring of "computer").

This file re-runs the audit's own probes (the 5 the task calls out
explicitly, plus the previously-passing keyword queries as a regression
guard) against a registry built the SAME way production builds it —
tool_registry_service.build_registry_entries() over real ToolDescriptors
and the real build_direct_chat_tools() connector schemas — not a
hand-mocked corpus, so this also exercises the new tool descriptions
written alongside the BM25 replacement.

"look something up online" (the audit's 7th probe, expected to hit
web__search) is intentionally not re-tested here: web__search is
always-on (ALWAYS_ON_TOOL_NAMES) and therefore excluded from the
registry by build_registry_entries — it was never reachable through
query_tool_registry in the first place, so it isn't a meaningful probe
against this specific function.
"""

from __future__ import annotations

import unittest

from server_modules import tool_registry_service


def _tool_name(result: dict) -> str:
    func = result.get("function", {}) if isinstance(result, dict) else {}
    return str(func.get("name") or result.get("name") or "")


def _build_realistic_registry():
    tool_capabilities = [
        {
            "id": "smtp",
            "label": "SMTP Email",
            "runtime_usable": True,
            "write_actions": ["send_email", "fetch_emails"],
        },
        {
            "id": "google_workspace",
            "label": "Google Workspace",
            "runtime_usable": True,
            "write_actions": [
                "send_email",
                "draft_email",
                "create_calendar_event",
                "fetch_emails",
                "list_calendar_events",
            ],
        },
        {
            "id": "slack",
            "label": "Slack",
            "runtime_usable": True,
            "write_actions": ["send_message", "send_dm", "post_reply"],
        },
        {
            "id": "discord_bot",
            "label": "Discord Bot",
            "runtime_usable": True,
            "write_actions": ["send_message"],
        },
        {
            "id": "telegram_bot",
            "label": "Telegram Bot",
            "runtime_usable": True,
            "write_actions": ["send_message"],
        },
    ]
    return tool_registry_service.build_registry_entries(tool_capabilities, {})


class Bm25AuditProbeTests(unittest.TestCase):
    """The audit's own failing probes, re-run against the real search
    function post-fix. Each assertion matches the task's required outcome
    exactly (top-1/top-2/top-3 as specified)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = _build_realistic_registry()

    def _search(self, query: str, max_results: int = 5):
        return tool_registry_service.search_tool_registry(query, self.registry, max_results=max_results)

    def test_let_the_team_know_on_chat_surfaces_slack_send_top3(self) -> None:
        results = self._search("let the team know on chat")
        names = [_tool_name(r) for r in results[:3]]
        self.assertTrue(
            any(n in ("slack__send_message", "slack__send_dm") for n in names),
            f"expected a slack send tool in top 3, got {names}",
        )

    def test_notify_the_customer_by_mail_surfaces_email_tool_first(self) -> None:
        results = self._search("notify the customer by mail")
        self.assertTrue(results, "expected at least one result")
        self.assertIn("send_email", _tool_name(results[0]))

    def test_can_you_see_the_text_on_my_display_surfaces_ocr_top2(self) -> None:
        results = self._search("can you see the text on my display")
        names = [_tool_name(r) for r in results[:2]]
        self.assertIn("computer__ocr", names)

    def test_schedule_a_meeting_with_the_client_surfaces_calendar_tool_first(self) -> None:
        results = self._search("schedule a meeting with the client")
        self.assertTrue(results, "expected at least one result")
        self.assertIn("create_calendar_event", _tool_name(results[0]))

    def test_ping_the_customer_about_the_invoice_surfaces_messaging_top3(self) -> None:
        results = self._search("ping the customer about the invoice")
        names = [_tool_name(r) for r in results[:3]]
        self.assertTrue(
            any("send_email" in n or "send_message" in n or "send_dm" in n for n in names),
            f"expected an email/messaging tool in top 3, got {names}",
        )


class Bm25RegressionTests(unittest.TestCase):
    """Queries that already worked under the old scorer — must keep
    working under BM25 too."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = _build_realistic_registry()

    def _search(self, query: str, max_results: int = 5):
        return tool_registry_service.search_tool_registry(query, self.registry, max_results=max_results)

    def test_email_the_customer_still_works(self) -> None:
        results = self._search("email the customer")
        names = [_tool_name(r) for r in results]
        self.assertTrue(any("send_email" in n for n in names))

    def test_put_this_on_my_calendar_still_works_without_false_positive(self) -> None:
        results = self._search("put this on my calendar")
        names = [_tool_name(r) for r in results]
        self.assertTrue(any("create_calendar_event" in n for n in names))
        # The old scorer's coincidental false positive: "put" is a substring
        # of "computer" purely by chance, and the old substring-match rule
        # scored computer__notify for this query. BM25 doesn't do substring
        # matching at all, so this must never happen again.
        self.assertNotIn("computer__notify", names)

    def test_empty_query_returns_nothing(self) -> None:
        self.assertEqual(self._search(""), [])
        self.assertEqual(self._search("   "), [])

    def test_query_of_only_stopwords_returns_nothing(self) -> None:
        self.assertEqual(self._search("the a an of"), [])

    def test_no_matching_registry_returns_nothing(self) -> None:
        self.assertEqual(tool_registry_service.search_tool_registry("email", []), [])


class TokenizerAndSynonymUnitTests(unittest.TestCase):
    """Lower-level checks on the tokenizer/stopword-unification/synonym
    layer, independent of the full search pipeline."""

    def test_tokenize_strips_commas_semicolons_colons_not_just_underscores(self) -> None:
        """audit §3.8: the old regex only stripped `_.-`, so a description
        ending '...titles, URLs, and snippets' produced un-matchable
        keywords 'titles,' / 'urls,' with the comma still attached."""
        tokens = tool_registry_service._tokenize("titles, urls, and snippets")
        self.assertNotIn("titles,", tokens)
        self.assertNotIn("urls,", tokens)
        self.assertIn("titles", tokens)
        self.assertIn("urls", tokens)
        self.assertIn("snippets", tokens)

    def test_stopwords_are_identical_on_index_and_query_side(self) -> None:
        """audit §3.5: the two stopword lists had drifted — search_tool_registry's
        inline copy was missing several words _extract_keywords already
        stripped (via/use/can/will/when/are/was/not/no/yes), so a token
        like "can" would silently behave differently depending on which
        side of the pipeline it hit. There is exactly one list now
        (_STOPWORDS), used by _tokenize for both indexing and querying."""
        for word in ("via", "use", "can", "will", "when", "are", "was", "not", "no", "yes"):
            self.assertNotIn(word, tool_registry_service._tokenize(f"a tool you {word} use here"))

    def test_synonym_expansion_maps_chat_vocabulary_to_tool_vocabulary(self) -> None:
        expanded = tool_registry_service._expand_query_tokens(["mail", "chat", "schedule"])
        self.assertIn("mail", expanded)  # original token preserved
        self.assertIn("email", expanded)
        self.assertIn("message", expanded)
        self.assertIn("slack", expanded)
        self.assertIn("calendar", expanded)

    def test_meaningful_short_tokens_survive_the_length_filter(self) -> None:
        tokens = tool_registry_service._tokenize("read text with ocr from a pdf via api")
        self.assertIn("ocr", tokens)
        self.assertIn("pdf", tokens)
        self.assertIn("api", tokens)


class Bm25FieldWeightingUnitTests(unittest.TestCase):
    """BM25 mechanics in isolation: name/label tokens must outweigh
    description tokens (a query naming the tool/action directly should beat
    a tool that merely mentions the word once in a longer description)."""

    def test_name_field_match_outranks_description_only_match(self) -> None:
        name_match = tool_registry_service.RegistryEntry(
            tool_name="alpha__search",
            description="Does something else entirely, filler text here",
            connector_id="alpha",
            name_field_tokens=tool_registry_service._tokenize("alpha search Alpha Search"),
            desc_field_tokens=tool_registry_service._tokenize("Does something else entirely, filler text here"),
            tool_definition={"type": "function", "function": {"name": "alpha__search", "description": "x"}},
        )
        desc_match = tool_registry_service.RegistryEntry(
            tool_name="beta__unrelated",
            description="A tool that happens to mention search once in passing",
            connector_id="beta",
            name_field_tokens=tool_registry_service._tokenize("beta unrelated Beta Unrelated"),
            desc_field_tokens=tool_registry_service._tokenize(
                "A tool that happens to mention search once in passing"
            ),
            tool_definition={"type": "function", "function": {"name": "beta__unrelated", "description": "x"}},
        )
        results = tool_registry_service.search_tool_registry("search", [desc_match, name_match])
        names = [_tool_name(r) for r in results]
        self.assertEqual(names[0], "alpha__search")

    def test_bm25_rank_returns_nothing_for_terms_absent_from_corpus(self) -> None:
        entry = tool_registry_service.RegistryEntry(
            tool_name="alpha__search",
            description="Does something",
            connector_id="alpha",
            name_field_tokens=["alpha", "search"],
            desc_field_tokens=["does", "something"],
            tool_definition={"type": "function", "function": {"name": "alpha__search", "description": "x"}},
        )
        scored = tool_registry_service._bm25_rank(["zzzznomatch"], [entry])
        self.assertEqual(scored, [])


if __name__ == "__main__":
    unittest.main()
