import unittest

from server_modules import healthguide_safety_service


class HealthGuideSafetyServiceTests(unittest.TestCase):
    def test_apply_health_safety_to_reply_adds_disclaimer_and_no_citation_disclosure(self) -> None:
        payload = healthguide_safety_service.apply_health_safety_to_reply(
            reply="Stay hydrated and monitor your symptoms.",
            user_message="I have a mild fever and a sore throat.",
            assistant_name="HealthGuide",
            response_payload={},
        )

        self.assertIn("not a doctor", payload["reply"])
        self.assertIn("without verified citations", payload["reply"])
        self.assertFalse(payload["red_flag_triggered"])
        self.assertEqual(payload["citation_mode"], "disclosed_unverified")

    def test_apply_health_safety_to_reply_renders_citations_when_present(self) -> None:
        payload = healthguide_safety_service.apply_health_safety_to_reply(
            reply="This is general guidance.",
            user_message="What are common flu symptoms?",
            assistant_name="HealthGuide",
            response_payload={
                "citation_refs": [
                    {"title": "CDC Flu Symptoms", "url": "https://www.cdc.gov/flu/symptoms/index.html"},
                ]
            },
        )

        self.assertIn("Sources:", payload["reply"])
        self.assertIn("CDC Flu Symptoms", payload["reply"])
        self.assertNotIn("without verified citations", payload["reply"])
        self.assertEqual(payload["citation_mode"], "verified")

    def test_apply_health_safety_to_reply_escalates_red_flag_symptoms(self) -> None:
        payload = healthguide_safety_service.apply_health_safety_to_reply(
            reply="You may be okay to rest at home.",
            user_message="I have chest pain and trouble breathing.",
            assistant_name="HealthGuide",
            response_payload={},
        )

        self.assertTrue(payload["red_flag_triggered"])
        self.assertIn("urgent medical attention", payload["reply"])
        self.assertIn("local emergency services", payload["reply"])
        self.assertIn("not a doctor", payload["reply"])
        self.assertIn("chest_pain", payload["red_flag_matches"])

    def test_apply_health_safety_to_turn_request_adds_policy_business_plan(self) -> None:
        class _Request:
            def __init__(self) -> None:
                self.context_hints = {
                    "metadata": {
                        "health_safety_enabled": True,
                        "deployed_agent_name": "HealthGuide",
                    }
                }

        request = _Request()
        resolved = healthguide_safety_service.apply_health_safety_to_turn_request(request)

        self.assertTrue(resolved.context_hints["metadata"]["health_safety_enabled"])
        self.assertEqual(
            resolved.context_hints["metadata"]["health_safety_policy_version"],
            healthguide_safety_service.HEALTHGUIDE_SAFETY_POLICY_VERSION,
        )
        self.assertIn(
            healthguide_safety_service.HEALTHGUIDE_SAFETY_MARKER,
            resolved.context_hints["business_plan"],
        )


if __name__ == "__main__":
    unittest.main()
