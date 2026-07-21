from server_modules import connection_catalog_service as service
from server_modules import personal_channels_repository as repo


def test_catalog_promotes_supported_work_app_connectors() -> None:
    payload = service.list_catalog_payload(surface="sage")
    by_id = {item["id"]: item for item in payload["items"]}

    for connection_id in ("dropbox", "s3", "smtp", "wechat_work", "instagram_business"):
        item = by_id[connection_id]
        assert item["lane"] == service.LANE_WORK_APP_CONNECTOR
        assert item["launch_status"] == service.LAUNCH_LIVE_WHEN_CONFIGURED
        assert item["setup_available"] is True
        assert item["runtime_usable"] is True
        assert item["launchable"] is True
        assert item["requires_gateway"] is False
        assert item["vault_provider"] == connection_id
        assert item["readiness_status"] == "implementation_ready"
        assert item["certification_required"] is True


def test_catalog_reclassifies_email_as_connector_not_channel_while_smtp_app_is_live() -> None:
    """reliability-audit-2-channels.md's Email finding: "email" used to
    carry lane=LANE_STUDIO_BUSINESS_CHANNEL with supports_inbound=True and
    runtime_usable/setup_available force-overridden to True despite
    launch_status=LAUNCH_PARTIAL and despite no inbound email listener
    existing anywhere in the codebase — the catalog was advertising a live
    messaging channel that doesn't exist. Per founder ruling, email is a
    connector/credential (send/fetch tool), not a channel: lane is now
    LANE_WORK_APP_CONNECTOR, supports_inbound is False, and
    runtime_usable/setup_available fall through to the LAUNCH_PARTIAL
    default (False) instead of being force-overridden."""
    payload = service.list_catalog_payload()
    by_id = {item["id"]: item for item in payload["items"]}

    assert by_id["email"]["lane"] == service.LANE_WORK_APP_CONNECTOR
    assert by_id["email"]["launch_status"] == service.LAUNCH_PARTIAL
    assert by_id["email"]["supports_inbound"] is False
    assert by_id["email"]["runtime_usable"] is False
    assert by_id["email"]["setup_available"] is False
    assert by_id["email"]["readiness_status"] == "planned"
    assert by_id["email"]["certification_required"] is True
    assert by_id["smtp"]["lane"] == service.LANE_WORK_APP_CONNECTOR
    assert by_id["smtp"]["launch_status"] == service.LAUNCH_LIVE_WHEN_CONFIGURED
    assert by_id["smtp"]["readiness_status"] == "implementation_ready"


def test_catalog_exposes_channel_certification_truth() -> None:
    payload = service.list_catalog_payload(surface="sage")
    by_id = {item["id"]: item for item in payload["items"]}

    assert by_id["telegram_personal"]["readiness_status"] == "launch_certified"
    assert by_id["telegram_personal"]["certification_required"] is False
    assert by_id["whatsapp_personal"]["readiness_status"] == "launch_certified"
    # Was "planned" — stale even before the channel_lane_contract_service.py
    # catalog fix (a separate file this test doesn't touch): this catalog's
    # own signal_personal _item() has carried launch_status="live_when_configured"
    # + runtime_usable=True + setup_available=True (identical to
    # imessage_personal below) since before this fix, so
    # _catalog_readiness already computed "implementation_ready" — this
    # assertion just never caught up. Now asserted against both real
    # local-bridge siblings together so they can't silently drift apart again.
    assert by_id["signal_personal"]["readiness_status"] == "implementation_ready"
    assert by_id["imessage_personal"]["readiness_status"] == "implementation_ready"
    # wechat_personal is the opposite fix: it used to carry the same
    # setup_available=True/runtime_usable=True as its local-bridge siblings
    # above despite zero gateway implementation backing it (no transport, no
    # pairing, no bridge — see docs/design/reliability-audit-2-channels.md and
    # the wechat_personal _item() in connection_catalog_service.py for the
    # full writeup). Personal WeChat automation has no supported API and is
    # not being pursued, so those flags were flipped to False, which now
    # correctly computes "planned" via _catalog_readiness's launchable-gate
    # fallthrough (connection_readiness_service.py:108-112) instead of
    # falsely claiming "implementation_ready".
    assert by_id["wechat_personal"]["readiness_status"] == "planned"
    assert by_id["wechat_personal"]["setup_available"] is False
    assert by_id["wechat_personal"]["runtime_usable"] is False
    assert by_id["signal_personal"]["requires_local_bridge"] is True
    assert by_id["signal_personal"]["certification_required"] is True
    assert by_id["signal_personal"]["certification_requirements"]
    assert by_id["apple_messages_business"]["readiness_status"] == "planned"
    assert by_id["apple_messages_business"]["requires_external_account"] is True
    assert by_id["sage_telegram_hosted"]["readiness_status"] == "live_when_configured"
    assert by_id["sage_telegram_hosted"]["requires_gateway"] is False


class TestPersonalChannelPillAgentScoping:
    """Item 3 pill fix: connection_catalog_service._personal_channel_state
    must read whichever agent asked, not "any session on this gateway."
    Uses a real temp SQLite DB (personal_channels_repository's own schema)
    rather than mocking — this is a data-isolation property, best proven
    against the real read path, not a mock of it."""

    def _use_temp_db(self, monkeypatch, tmp_path) -> None:
        db_path = str(tmp_path / "personal-channels-test.sqlite3")
        monkeypatch.setattr(repo, "PERSONAL_CHANNELS_DB_FILE", db_path)

    def test_two_agents_same_gateway_do_not_see_each_others_pill(self, monkeypatch, tmp_path) -> None:
        self._use_temp_db(monkeypatch, tmp_path)
        repo.upsert_telegram_state(
            gateway_id="gw-shared", tenant_id="t1", workspace_id="w1", user_id="u1",
            channel_key="telegram_personal", agent_id="agent_A",
            provider="telegram", status="connected", linked_username="alice",
        )

        state_for_owner = service._personal_channel_state("telegram_personal", "gw-shared", "agent_A")
        state_for_other_agent = service._personal_channel_state("telegram_personal", "gw-shared", "agent_B")
        state_unscoped_legacy_read = service._personal_channel_state("telegram_personal", "gw-shared", "")

        assert state_for_owner is not None
        assert state_for_owner["status"] == "connected"
        assert state_for_other_agent is None, "a different agent on the SAME gateway must not see agent_A's session"
        assert state_unscoped_legacy_read is None, "the old unscoped read must not surface a real agent's session either"

    def test_a_disconnected_agent_shows_disconnected_even_if_another_agent_on_the_box_is_connected(
        self, monkeypatch, tmp_path,
    ) -> None:
        self._use_temp_db(monkeypatch, tmp_path)
        repo.upsert_telegram_state(
            gateway_id="gw-shared", tenant_id="t1", workspace_id="w1", user_id="u1",
            channel_key="telegram_personal", agent_id="agent_connected",
            provider="telegram", status="connected", linked_username="connected_one",
        )
        repo.upsert_telegram_state(
            gateway_id="gw-shared", tenant_id="t1", workspace_id="w1", user_id="u1",
            channel_key="telegram_personal", agent_id="agent_idle",
            provider="telegram", status="code_required", login_hint="+1555",
        )

        connected_view = service._personal_channel_state("telegram_personal", "gw-shared", "agent_connected")
        idle_view = service._personal_channel_state("telegram_personal", "gw-shared", "agent_idle")

        assert connected_view["status"] == "connected"
        assert idle_view["status"] == "code_required", "agent_idle's own mid-pairing status must not be masked by agent_connected's"
