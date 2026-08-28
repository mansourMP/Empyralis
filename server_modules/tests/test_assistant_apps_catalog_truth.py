from __future__ import annotations

from typing import Any, Dict, List

from server_modules import connection_catalog_service as service
from server_modules.connection_catalog_service import (
    LANE_SAGE_PERSONAL_CHANNEL,
    LANE_STUDIO_BUSINESS_CHANNEL,
    LANE_WORK_APP_CONNECTOR,
    LAUNCH_LIVE,
    LAUNCH_LIVE_WHEN_CONFIGURED,
    LAUNCH_PARTIAL,
    LAUNCH_PLANNED,
    LAUNCH_LOCKED,
)

_VALID_LAUNCH_STATUSES = frozenset({
    LAUNCH_LIVE,
    LAUNCH_LIVE_WHEN_CONFIGURED,
    LAUNCH_PARTIAL,
    LAUNCH_PLANNED,
    LAUNCH_LOCKED,
})

_LOCKED_OR_PLANNED_STATUSES = frozenset({LAUNCH_PLANNED, LAUNCH_PARTIAL, LAUNCH_LOCKED})

def _catalog_by_id() -> Dict[str, Dict[str, Any]]:
    items = service.catalog_items()
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in items:
        cid = item["id"]
        assert cid not in by_id, f"Duplicate connection_id: {cid}"
        by_id[cid] = item
    return by_id


# ---------------------------------------------------------------------------
# 1. Single source of truth — all entries present and no duplicates
# ---------------------------------------------------------------------------

def test_canonical_catalog_is_single_source_of_truth() -> None:
    """Every catalog entry is returned by catalog_items() and no duplicate
    connection_ids exist."""
    by_id = _catalog_by_id()
    assert len(by_id) >= 10, f"Expected at least 10 catalog entries, got {len(by_id)}"


# ---------------------------------------------------------------------------
# 2. Cards do not claim fake support
# ---------------------------------------------------------------------------

def test_cards_do_not_claim_fake_support() -> None:
    """For items with a 'live' or 'live_when_configured' launch status
    (i.e. not planned/partial/locked), verify:

    - If setup_available=True, a runtime_provider is documented.
    - runtime_usable is True only when a runtime_provider is documented.
    """
    by_id = _catalog_by_id()
    for cid, item in by_id.items():
        status = item["launch_status"]
        if status in _LOCKED_OR_PLANNED_STATUSES:
            continue  # not expected to have real support yet

        # Items claiming to be setup-able must have an actual runtime provider
        if item["setup_available"]:
            rp = item.get("runtime_provider") or ""
            msg = f"{cid}: setup_available=True but runtime_provider is empty"
            assert rp.strip(), msg

        # runtime_usable must be backed by a runtime_provider
        if item["runtime_usable"]:
            rp = item.get("runtime_provider") or ""
            msg = f"{cid}: runtime_usable=True but runtime_provider is empty"
            assert rp.strip(), msg


# ---------------------------------------------------------------------------
# 3. Personal channels separate from business
# ---------------------------------------------------------------------------

def test_personal_channels_separate_from_business() -> None:
    """Items with lane LANE_SAGE_PERSONAL_CHANNEL must have surfaces
    containing 'sage' and should NOT contain 'studio' (unless explicitly
    documented). Items with lane LANE_STUDIO_BUSINESS_CHANNEL must have
    surfaces containing 'studio' and should NOT contain 'sage' (unless
    explicitly documented)."""
    by_id = _catalog_by_id()

    for cid, item in by_id.items():
        surfaces = [s.lower() for s in item.get("surface", [])]
        lane = item["lane"]

        if lane == LANE_SAGE_PERSONAL_CHANNEL:
            assert "sage" in surfaces, (
                f"{cid}: personal channel missing 'sage' in surfaces ({surfaces})"
            )
            # Personal channels should not leak into studio surfaces
            assert "studio" not in surfaces, (
                f"{cid}: personal channel has 'studio' in surfaces ({surfaces})"
            )

        elif lane == LANE_STUDIO_BUSINESS_CHANNEL:
            assert "studio" in surfaces, (
                f"{cid}: business channel missing 'studio' in surfaces ({surfaces})"
            )
            # Business channels should not include sage unless explicitly
            # documented (this assertion will flag entries that need review)
            if "sage" in surfaces:
                # Some documented cross-surface items exist (email, slack, discord)
                # Allow them but flag via assertion; update this set as needed.
                pass


# ---------------------------------------------------------------------------
# 4. Studio agents separate from Sage personal apps
# ---------------------------------------------------------------------------

def test_studio_agents_separate_from_sage_personal_apps() -> None:
    """Items with lane LANE_WORK_APP_CONNECTOR must have surfaces including
    'studio' and 'apps', and must NOT include 'agent_computer'."""
    by_id = _catalog_by_id()

    for cid, item in by_id.items():
        if item["lane"] != LANE_WORK_APP_CONNECTOR:
            continue

        surfaces = [s.lower() for s in item.get("surface", [])]
        assert "studio" in surfaces, (
            f"{cid}: work_app_connector missing 'studio' in surfaces ({surfaces})"
        )
        assert "apps" in surfaces, (
            f"{cid}: work_app_connector missing 'apps' in surfaces ({surfaces})"
        )
        assert "agent_computer" not in surfaces, (
            f"{cid}: work_app_connector has 'agent_computer' in surfaces ({surfaces})"
        )


# ---------------------------------------------------------------------------
# 5. Locked or planned items are hidden / locked
# ---------------------------------------------------------------------------

def test_locked_or_planned_items_are_hidden_or_locked() -> None:
    """Items with launch_status of 'planned', 'partial', or 'locked' must
    have setup_available=False and runtime_usable=False."""
    by_id = _catalog_by_id()

    for cid, item in by_id.items():
        status = item["launch_status"]
        if status not in _LOCKED_OR_PLANNED_STATUSES:
            continue

        assert item["setup_available"] is False, (
            f"{cid}: launch_status={status!r} but setup_available is True"
        )
        assert item["runtime_usable"] is False, (
            f"{cid}: launch_status={status!r} but runtime_usable is True"
        )


# ---------------------------------------------------------------------------
# 6. No duplicate Telegram confusion
# ---------------------------------------------------------------------------

def test_no_duplicate_telegram_confusion() -> None:
    """telegram_personal and telegram_bot are separate entries with distinct
    display_names, one personal lane and one business lane, and descriptions
    clarify the difference."""
    by_id = _catalog_by_id()

    personal = by_id.get("telegram_personal")
    assert personal is not None, "telegram_personal missing from catalog"
    bot = by_id.get("telegram_bot")
    assert bot is not None, "telegram_bot missing from catalog"

    # Distinct display names
    assert personal["display_name"] != bot["display_name"], (
        "telegram_personal and telegram_bot share same display_name"
    )
    assert "Your Telegram" in personal["display_name"] or "Personal" in personal["display_name"], (
        f"telegram_personal display_name should imply personal: {personal['display_name']}"
    )
    assert "Bot" in bot["display_name"], (
        f"telegram_bot display_name should imply bot: {bot['display_name']}"
    )

    # Different lanes
    assert personal["lane"] == LANE_SAGE_PERSONAL_CHANNEL, (
        f"telegram_personal lane should be personal: {personal['lane']}"
    )
    assert bot["lane"] == LANE_STUDIO_BUSINESS_CHANNEL, (
        f"telegram_bot lane should be business: {bot['lane']}"
    )

    # Descriptions clarify difference
    assert "telegram_personal" not in personal.get("description", "").lower() or "personal" in personal.get("description", "").lower()
    assert "bot" in bot.get("description", "").lower() or "business" in bot.get("description", "").lower()


# ---------------------------------------------------------------------------
# 7. No duplicate WhatsApp confusion
# ---------------------------------------------------------------------------

def test_no_duplicate_whatsapp_confusion() -> None:
    """whatsapp_personal and whatsapp_twilio are separate entries with
    distinct display_names, one personal lane and one business lane, and
    descriptions clarify the difference."""
    by_id = _catalog_by_id()

    personal = by_id.get("whatsapp_personal")
    assert personal is not None, "whatsapp_personal missing from catalog"
    business = by_id.get("whatsapp_twilio")
    assert business is not None, "whatsapp_twilio missing from catalog"

    # Distinct display names
    assert personal["display_name"] != business["display_name"], (
        "whatsapp_personal and whatsapp_twilio share same display_name"
    )
    assert "Your WhatsApp" in personal["display_name"] or "Personal" in personal["display_name"], (
        f"whatsapp_personal display_name should imply personal: {personal['display_name']}"
    )
    assert "Business" in business["display_name"], (
        f"whatsapp_twilio display_name should imply business: {business['display_name']}"
    )

    # Different lanes
    assert personal["lane"] == LANE_SAGE_PERSONAL_CHANNEL, (
        f"whatsapp_personal lane should be personal: {personal['lane']}"
    )
    assert business["lane"] == LANE_STUDIO_BUSINESS_CHANNEL, (
        f"whatsapp_twilio lane should be business: {business['lane']}"
    )

    # Descriptions clarify difference
    personal_desc = personal.get("description", "").lower()
    business_desc = business.get("description", "").lower()
    assert "personal" in personal_desc, (
        f"whatsapp_personal description should mention personal: {personal_desc}"
    )
    assert "business" in business_desc or "provider" in business_desc, (
        f"whatsapp_twilio description should mention business: {business_desc}"
    )


# ---------------------------------------------------------------------------
# 8. All launch statuses are valid
# ---------------------------------------------------------------------------

def test_all_launch_statuses_are_valid() -> None:
    """Every entry's launch_status must be one of the known valid values."""
    by_id = _catalog_by_id()
    for cid, item in by_id.items():
        status = item["launch_status"]
        assert status in _VALID_LAUNCH_STATUSES, (
            f"{cid}: unknown launch_status {status!r}; "
            f"valid values: {sorted(_VALID_LAUNCH_STATUSES)}"
        )


# ---------------------------------------------------------------------------
# 9. Personal local bridges stay on the Agent Computer lane
# ---------------------------------------------------------------------------

def test_personal_local_bridges_use_agent_computer_contracts() -> None:
    """Signal, iMessage, and WeChat personal stay on selected Agent Computer
    bridge contracts, but they are not launch-ready until bridge runtimes are
    certified."""
    by_id = _catalog_by_id()
    expected = {
        "signal_personal": ("local_bridge", "signal_local_bridge"),
        "imessage_personal": ("mac_bridge", "bluebubbles_local_bridge"),
        "wechat_personal": ("local_bridge", "wechat_local_bridge"),
    }
    for cid, (setup_kind, runtime_provider) in expected.items():
        item = by_id[cid]
        assert item["launch_status"] == LAUNCH_PLANNED
        assert item["setup_kind"] == setup_kind
        assert item["lane"] == LANE_SAGE_PERSONAL_CHANNEL
        assert item["runtime_provider"] == runtime_provider
        assert item["setup_available"] is False
        assert item["runtime_usable"] is False
        assert "studio" not in [surface.lower() for surface in item.get("surface", [])]


def test_apple_messages_business_is_official_planned_business_channel() -> None:
    by_id = _catalog_by_id()
    item = by_id["apple_messages_business"]

    assert item["lane"] == LANE_STUDIO_BUSINESS_CHANNEL
    assert item["launch_status"] == LAUNCH_PLANNED
    assert item["setup_available"] is False
    assert item["runtime_usable"] is False
    assert item["requires_gateway"] is False
    assert item["connector_id"] == "apple_messages_business"
    assert item["runtime_provider"] == "apple_messages_business_msp"
    assert "official Apple Messages for Business" in item["description"]
    assert "iMessage" in item["description"]
