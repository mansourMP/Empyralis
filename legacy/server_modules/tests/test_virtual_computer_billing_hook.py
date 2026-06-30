from server_modules import virtual_computer_billing_hook


def _item_by_type(payload: dict, item_type: str) -> dict:
    for item in payload.get("credit_line_items", []):
        if str(item.get("credit_item_type")) == item_type:
            return item
    raise AssertionError(f"Missing line item type: {item_type}")


def test_build_virtual_computer_billing_hook_payload_emits_required_interface_fields() -> None:
    payload = virtual_computer_billing_hook.build_virtual_computer_billing_hook_payload(
        runtime_type="virtual_browser",
        session_id="vcsess_123",
        duration_ms=180000,
        artifact_bytes=5 * 1024 * 1024,
        snapshot_bytes=2 * 1024 * 1024,
    )

    assert payload["runtime_type"] == "virtual_browser"
    assert payload["session_id"] == "vcsess_123"
    assert payload["duration_ms"] == 180000
    assert payload["artifact_bytes"] == 5 * 1024 * 1024
    assert payload["snapshot_bytes"] == 2 * 1024 * 1024
    assert isinstance(payload["credit_line_items"], list)

    runtime_item = _item_by_type(payload, "virtual_browser_minutes")
    artifact_item = _item_by_type(payload, "artifact_storage")
    snapshot_item = _item_by_type(payload, "snapshot_storage")

    assert runtime_item["quantity_unit"] == "minutes"
    assert runtime_item["quantity"] == 3.0
    assert artifact_item["quantity_unit"] == "mb"
    assert artifact_item["quantity"] == 5.0
    assert snapshot_item["quantity_unit"] == "mb"
    assert snapshot_item["quantity"] == 2.0


def test_build_virtual_computer_billing_hook_payload_normalizes_runtime_aliases() -> None:
    payload = virtual_computer_billing_hook.build_virtual_computer_billing_hook_payload(
        runtime_type="sandbox",
        session_id="vcsess_alias",
        duration_ms=60000,
        artifact_bytes=0,
        snapshot_bytes=0,
    )

    assert payload["runtime_type"] == "virtual_code_sandbox"
    runtime_item = _item_by_type(payload, "virtual_code_sandbox_minutes")
    assert runtime_item["quantity"] == 1.0


def test_build_virtual_computer_billing_hook_payload_keeps_shape_for_zero_usage() -> None:
    payload = virtual_computer_billing_hook.build_virtual_computer_billing_hook_payload(
        runtime_type="virtual_desktop",
        session_id="vcsess_zero",
        duration_ms=-1,
        artifact_bytes=-50,
        snapshot_bytes=None,
    )

    assert payload["runtime_type"] == "virtual_desktop"
    assert payload["session_id"] == "vcsess_zero"
    assert payload["duration_ms"] == 0
    assert payload["artifact_bytes"] == 0
    assert payload["snapshot_bytes"] == 0
    assert payload["credit_line_items"] == []
