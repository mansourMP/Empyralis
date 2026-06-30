from __future__ import annotations

from typing import Dict, List

AutomationIntent = str

INTENT_KEYWORDS: Dict[str, List[str]] = {
    "EMAIL_SUMMARY": ["summarize", "emails", "inbox", "daily summary"],
    "LEAD_FOLLOWUP": ["follow up", "leads", "contacts", "outreach"],
    "ALERT_ME": ["alert", "notify", "tell me when", "let me know"],
}


def classify_automation_intent(text: str) -> AutomationIntent:
    normalized = f" {str(text or '').strip().lower()} "
    for intent, keywords in INTENT_KEYWORDS.items():
        if any((f" {keyword} " in normalized) or (keyword in normalized) for keyword in keywords):
            return intent
    return "UNKNOWN"
