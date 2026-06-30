from pathlib import Path


def _install_allow_gate(monkeypatch, rust_client):
    calls = []

    def allow_decision(**request):
        calls.append(request)
        return {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": request.get("operation"),
        }

    def enforce(_command, decision):
        return decision

    monkeypatch.setattr(rust_client, "runtime_state_store_decision", allow_decision)
    monkeypatch.setattr(rust_client, "enforce_kernel_decision", enforce)
    return calls


def test_runtime_common_safe_write_json_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules import runtime_common

    calls = _install_allow_gate(monkeypatch, runtime_common.rust_runtime_kernel_client)
    target = tmp_path / "runtime-state.json"

    runtime_common._safe_write_json(target, {"enabled": True})

    assert target.exists()
    assert calls[0]["operation"] == "write_runtime_common_json"
    assert calls[0]["state_class"] == "runtime_common_json_files"
    assert calls[0]["payload"]["path"] == str(target)


def test_runtime_common_safe_write_json_wrong_rust_action_blocks_write(monkeypatch, tmp_path):
    from server_modules import runtime_common

    target = tmp_path / "runtime-state.json"

    monkeypatch.setattr(
        runtime_common.rust_runtime_kernel_client,
        "runtime_state_store_decision",
        lambda **request: {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_profile_api_file",
        },
    )
    monkeypatch.setattr(
        runtime_common.rust_runtime_kernel_client,
        "enforce_kernel_decision",
        lambda _command, decision: decision,
    )

    try:
        runtime_common._safe_write_json(target, {"enabled": True})
        assert False, "expected RuntimeCommonRustGateError"
    except runtime_common.RuntimeCommonRustGateError as exc:
        assert str(exc) == "unexpected_next_action"

    assert not target.exists()


def test_acp_manager_safe_write_json_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules import acp_manager

    calls = _install_allow_gate(monkeypatch, acp_manager.rust_runtime_kernel_client)
    target = tmp_path / "acp-state.json"

    acp_manager._safe_write_json(target, {"runs": []})

    assert target.exists()
    assert calls[0]["operation"] == "write_acp_manager_json"
    assert calls[0]["state_class"] == "acp_manager_state"
    assert calls[0]["payload"]["path"] == str(target)


def test_acp_manager_safe_write_json_wrong_rust_action_blocks_write(monkeypatch, tmp_path):
    from server_modules import acp_manager

    target = tmp_path / "acp-state.json"

    monkeypatch.setattr(
        acp_manager.rust_runtime_kernel_client,
        "runtime_state_store_decision",
        lambda **request: {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_profile_api_file",
        },
    )
    monkeypatch.setattr(
        acp_manager.rust_runtime_kernel_client,
        "enforce_kernel_decision",
        lambda _command, decision: decision,
    )

    try:
        acp_manager._safe_write_json(target, {"runs": []})
        assert False, "expected AcpManagerRustGateError"
    except acp_manager.AcpManagerRustGateError as exc:
        assert str(exc) == "unexpected_next_action"

    assert not target.exists()


def test_session_diagnostics_file_export_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules import session_diagnostics_service

    calls = _install_allow_gate(monkeypatch, session_diagnostics_service.rust_runtime_kernel_client)
    target = tmp_path / "diagnostics.json"

    session_diagnostics_service.export_diagnostics_to_file(
        {"diagnostics_version": 1, "workspace_id": "workspace-1", "sessions": []},
        target,
    )

    assert target.exists()
    assert calls[0]["operation"] == "write_session_diagnostics_file"
    assert calls[0]["workspace_id"] == "workspace-1"
    assert calls[0]["payload"]["output_path"] == str(target)


def test_session_diagnostics_file_export_wrong_rust_action_blocks_write(monkeypatch, tmp_path):
    from server_modules import session_diagnostics_service

    target = tmp_path / "diagnostics.json"

    monkeypatch.setattr(
        session_diagnostics_service.rust_runtime_kernel_client,
        "runtime_state_store_decision",
        lambda **request: {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_profile_api_file",
        },
    )
    monkeypatch.setattr(
        session_diagnostics_service.rust_runtime_kernel_client,
        "enforce_kernel_decision",
        lambda _command, decision: decision,
    )

    try:
        session_diagnostics_service.export_diagnostics_to_file(
            {"diagnostics_version": 1, "workspace_id": "workspace-1", "sessions": []},
            target,
        )
        assert False, "expected SessionDiagnosticsRustGateError"
    except session_diagnostics_service.SessionDiagnosticsRustGateError as exc:
        assert str(exc) == "unexpected_next_action"

    assert not target.exists()


def test_agent_memory_daily_log_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules import agent_memory
    from server_modules import rust_runtime_kernel_client

    calls = _install_allow_gate(monkeypatch, rust_runtime_kernel_client)
    monkeypatch.setattr(
        agent_memory,
        "_memory_logs_dir",
        lambda workspace_id, agent_install_id=None: tmp_path,
    )

    agent_memory._save_daily_log("workspace-1", "Remember this.", agent_install_id="agent-1")

    assert calls[0]["operation"] == "append_agent_memory_daily_log"
    assert calls[0]["workspace_id"] == "workspace-1"
    assert calls[0]["payload"]["agent_install_id"] == "agent-1"


def test_no_provider_summary_write_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules import no_provider_service
    from server_modules import rust_runtime_kernel_client

    calls = _install_allow_gate(monkeypatch, rust_runtime_kernel_client)
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "module.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    output_path = tmp_path / "summary.txt"

    result = no_provider_service.count_functions_and_write_summary(
        f"count function in .py files in {source_dir} write summary to {output_path}",
        compact_text=lambda value: str(value or "").lower(),
        resolve_local_path=lambda value: Path(value),
    )

    assert output_path.exists()
    assert "Counted 1 functions" in str(result)
    assert calls[0]["operation"] == "write_no_provider_summary"
    assert calls[0]["payload"]["source_dir"] == str(source_dir)
    assert calls[0]["payload"]["output_path"] == str(output_path)


def test_public_bot_drill_report_write_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules import public_bot_drill_support

    calls = _install_allow_gate(monkeypatch, public_bot_drill_support.rust_runtime_kernel_client)
    monkeypatch.setattr(public_bot_drill_support, "build_prompt14_report", lambda: {"ok": True})
    monkeypatch.setattr(public_bot_drill_support, "render_prompt14_markdown", lambda report: "# Drill\n")
    output_path = tmp_path / "drill.md"

    report = public_bot_drill_support.write_prompt14_report(output_path=output_path)

    assert report == {"ok": True}
    assert output_path.exists()
    assert output_path.with_suffix(".json").exists()
    assert calls[0]["operation"] == "write_public_bot_drill_report"
    assert calls[0]["payload"]["output_path"] == str(output_path)


def test_public_bot_drill_report_write_wrong_rust_action_blocks_write(monkeypatch, tmp_path):
    from server_modules import public_bot_drill_support

    monkeypatch.setattr(public_bot_drill_support, "build_prompt14_report", lambda: {"ok": True})
    monkeypatch.setattr(public_bot_drill_support, "render_prompt14_markdown", lambda report: "# Drill\n")
    monkeypatch.setattr(
        public_bot_drill_support.rust_runtime_kernel_client,
        "runtime_state_store_decision",
        lambda **request: {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_profile_api_file",
        },
    )
    monkeypatch.setattr(
        public_bot_drill_support.rust_runtime_kernel_client,
        "enforce_kernel_decision",
        lambda _command, decision: decision,
    )
    output_path = tmp_path / "drill.md"

    try:
        public_bot_drill_support.write_prompt14_report(output_path=output_path)
        assert False, "expected PublicBotDrillRustGateError"
    except public_bot_drill_support.PublicBotDrillRustGateError as exc:
        assert str(exc) == "unexpected_next_action"

    assert not output_path.exists()
    assert not output_path.with_suffix(".json").exists()


def test_telegram_media_download_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules.connectors import telegram_media_service

    calls = _install_allow_gate(monkeypatch, telegram_media_service.rust_runtime_kernel_client)

    class FakeResponse:
        def __init__(self):
            self._chunks = [b"abc", b""]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _size):
            return self._chunks.pop(0)

    monkeypatch.setattr(
        telegram_media_service.urlrequest,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(),
    )
    service = telegram_media_service.TelegramMediaService(
        media_dir=tmp_path,
        media_enabled=True,
        media_max_items=1,
        media_max_bytes=1024,
        media_include_in_goal=True,
        telegram_api_request=lambda *args, **kwargs: {},
    )
    target = tmp_path / "media.jpg"

    size = service.download_file("bot-token", "photos/file.jpg", target, 1024)

    assert size == 3
    assert target.read_bytes() == b"abc"
    assert calls[0]["operation"] == "write_telegram_media_file"
    assert calls[0]["payload"]["dest_path"] == str(target)


def test_telegram_media_download_wrong_rust_action_blocks_write(monkeypatch, tmp_path):
    from server_modules.connectors import telegram_media_service

    class FakeResponse:
        def __init__(self):
            self._chunks = [b"abc", b""]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _size):
            return self._chunks.pop(0)

    monkeypatch.setattr(
        telegram_media_service.urlrequest,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(),
    )
    monkeypatch.setattr(
        telegram_media_service.rust_runtime_kernel_client,
        "runtime_state_store_decision",
        lambda **request: {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_profile_api_file",
        },
    )
    monkeypatch.setattr(
        telegram_media_service.rust_runtime_kernel_client,
        "enforce_kernel_decision",
        lambda _command, decision: decision,
    )
    service = telegram_media_service.TelegramMediaService(
        media_dir=tmp_path,
        media_enabled=True,
        media_max_items=1,
        media_max_bytes=1024,
        media_include_in_goal=True,
        telegram_api_request=lambda *args, **kwargs: {},
    )
    target = tmp_path / "media.jpg"

    try:
        service.download_file("bot-token", "photos/file.jpg", target, 1024)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert str(exc) == "unexpected_next_action"

    assert not target.exists()


def test_machine_capability_probe_write_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules import machine_capability_check
    from server_modules import rust_runtime_kernel_client

    calls = _install_allow_gate(monkeypatch, rust_runtime_kernel_client)
    monkeypatch.setattr(machine_capability_check, "_runtime_state_db_path", lambda: tmp_path / "state.db")

    result = machine_capability_check._filesystem_probe()

    assert result["status"] == "granted"
    assert calls[0]["operation"] == "write_machine_capability_probe_file"
    assert calls[0]["payload"]["probe_file"].endswith("write-test.tmp")


def test_machine_capability_probe_wrong_rust_action_blocks_probe_write(monkeypatch, tmp_path):
    from server_modules import machine_capability_check
    from server_modules import rust_runtime_kernel_client

    monkeypatch.setattr(machine_capability_check, "_runtime_state_db_path", lambda: tmp_path / "state.db")
    monkeypatch.setattr(
        rust_runtime_kernel_client,
        "runtime_state_store_decision",
        lambda **request: {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_profile_api_file",
        },
    )
    monkeypatch.setattr(
        rust_runtime_kernel_client,
        "enforce_kernel_decision",
        lambda _command, decision: decision,
    )

    result = machine_capability_check._filesystem_probe()

    assert result["status"] == "denied"
    assert "unexpected_next_action" in str(result["detail"])


def test_dropbox_download_file_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules.connectors import dropbox_connector

    calls = _install_allow_gate(monkeypatch, dropbox_connector.rust_runtime_kernel_client)

    class FakeClient:
        def files_download(self, path):
            return {"name": "file.txt"}, type("Response", (), {"content": b"dropbox-body"})()

    target = tmp_path / "downloads" / "file.txt"

    result = dropbox_connector.download_file(
        {"access_token": "token"},
        "/remote/file.txt",
        str(target),
        client=FakeClient(),
    )

    assert target.read_bytes() == b"dropbox-body"
    assert result["local_path"] == str(target)
    assert calls[0]["operation"] == "write_dropbox_download_file"
    assert calls[0]["payload"]["dropbox_path"] == "/remote/file.txt"


def test_dropbox_download_file_wrong_rust_action_blocks_write(monkeypatch, tmp_path):
    from server_modules.connectors import dropbox_connector

    class FakeClient:
        def files_download(self, path):
            return {"name": "file.txt"}, type("Response", (), {"content": b"dropbox-body"})()

    monkeypatch.setattr(
        dropbox_connector.rust_runtime_kernel_client,
        "runtime_state_store_decision",
        lambda **request: {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_profile_api_file",
        },
    )
    monkeypatch.setattr(
        dropbox_connector.rust_runtime_kernel_client,
        "enforce_kernel_decision",
        lambda _command, decision: decision,
    )
    target = tmp_path / "downloads" / "file.txt"

    try:
        dropbox_connector.download_file(
            {"access_token": "token"},
            "/remote/file.txt",
            str(target),
            client=FakeClient(),
        )
        assert False, "expected DropboxConnectorRustGateError"
    except dropbox_connector.DropboxConnectorRustGateError as exc:
        assert str(exc) == "unexpected_next_action"

    assert not target.exists()


def test_generated_image_write_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules import tools_image_gen

    calls = _install_allow_gate(monkeypatch, tools_image_gen.rust_runtime_kernel_client)
    target = tmp_path / "image.png"

    result = tools_image_gen._write_binary(target, b"png-bytes")

    assert result == str(target)
    assert target.read_bytes() == b"png-bytes"
    assert calls[0]["operation"] == "write_generated_image_file"
    assert calls[0]["payload"]["path"] == str(target)


def test_generated_image_write_wrong_rust_action_blocks_write(monkeypatch, tmp_path):
    from server_modules import tools_image_gen

    monkeypatch.setattr(
        tools_image_gen.rust_runtime_kernel_client,
        "runtime_state_store_decision",
        lambda **request: {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_profile_api_file",
        },
    )
    monkeypatch.setattr(
        tools_image_gen.rust_runtime_kernel_client,
        "enforce_kernel_decision",
        lambda _command, decision: decision,
    )
    target = tmp_path / "image.png"

    try:
        tools_image_gen._write_binary(target, b"png-bytes")
        assert False, "expected ImageGenerationRustGateError"
    except tools_image_gen.ImageGenerationRustGateError as exc:
        assert str(exc) == "unexpected_next_action"

    assert not target.exists()


def test_telegram_poll_lock_write_wrong_rust_action_blocks_write(monkeypatch, tmp_path):
    from server_modules.connectors import autopilot_connector_config

    monkeypatch.setattr(autopilot_connector_config, "_TELEGRAM_POLL_LOCK_DIR", tmp_path)
    monkeypatch.setattr(
        autopilot_connector_config.rust_runtime_kernel_client,
        "runtime_state_store_decision",
        lambda **request: {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_profile_api_file",
        },
    )
    monkeypatch.setattr(
        autopilot_connector_config.rust_runtime_kernel_client,
        "enforce_kernel_decision",
        lambda _command, decision: decision,
    )

    try:
        with autopilot_connector_config._telegram_get_updates_process_lock("bot-token") as acquired:
            assert acquired is True
        assert False, "expected AutopilotConnectorConfigRustGateError"
    except autopilot_connector_config.AutopilotConnectorConfigRustGateError as exc:
        assert str(exc) == "unexpected_next_action"


def test_no_provider_summary_wrong_rust_action_blocks_write(monkeypatch, tmp_path):
    from server_modules import no_provider_service
    from server_modules import rust_runtime_kernel_client

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "module.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    output_path = tmp_path / "summary.txt"

    monkeypatch.setattr(
        rust_runtime_kernel_client,
        "runtime_state_store_decision",
        lambda **request: {
            "ok": True,
            "decision": "allow",
            "operation": request.get("operation"),
            "next_action": "write_profile_api_file",
        },
    )
    monkeypatch.setattr(
        rust_runtime_kernel_client,
        "enforce_kernel_decision",
        lambda _command, decision: decision,
    )

    try:
        no_provider_service.count_functions_and_write_summary(
            f"count function in .py files in {source_dir} write summary to {output_path}",
            compact_text=lambda value: str(value or "").lower(),
            resolve_local_path=lambda value: Path(value),
        )
        assert False, "expected NoProviderRustGateError"
    except no_provider_service.NoProviderRustGateError as exc:
        assert str(exc) == "unexpected_next_action"

    assert not output_path.exists()


def test_telegram_poll_lock_write_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules.connectors import autopilot_connector_config

    calls = _install_allow_gate(monkeypatch, autopilot_connector_config.rust_runtime_kernel_client)
    monkeypatch.setattr(autopilot_connector_config, "_TELEGRAM_POLL_LOCK_DIR", tmp_path)

    with autopilot_connector_config._telegram_get_updates_process_lock("bot-token") as acquired:
        assert acquired is True

    assert calls[0]["operation"] == "write_telegram_poll_lock_file"
    assert calls[0]["payload"]["lock_path"].endswith(".lock")


def test_skills_registry_json_write_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules import rust_runtime_kernel_client
    from server_modules import skills_registry

    calls = _install_allow_gate(monkeypatch, rust_runtime_kernel_client)
    target = tmp_path / "skill.json"

    skills_registry._write_json(target, {"name": "example"})

    assert target.exists()
    assert calls[0]["operation"] == "write_skills_registry_file"
    assert calls[0]["payload"]["path"] == str(target)
    assert calls[0]["payload"]["kind"] == "json"


def test_deployed_agent_knowledge_file_write_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules import deployed_agent_service

    calls = _install_allow_gate(monkeypatch, deployed_agent_service.rust_runtime_kernel_client)
    target = tmp_path / "knowledge.md"

    deployed_agent_service._enforce_deployed_agent_knowledge_file_write(
        deployed_agent_id="agent-1",
        workspace_id="workspace-1",
        destination=target,
        safe_filename="knowledge.md",
        content_hash="abcdef1234567890",
        content_bytes=42,
    )

    assert calls[0]["operation"] == "write_deployed_agent_knowledge_file"
    assert calls[0]["workspace_id"] == "workspace-1"
    assert calls[0]["payload"]["deployed_agent_id"] == "agent-1"


def test_outcome_pack_file_write_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules import outcome_packs

    calls = _install_allow_gate(monkeypatch, outcome_packs.rust_runtime_kernel_client)
    target = tmp_path / "outcome.csv"

    outcome_packs._enforce_outcome_pack_file_write(
        operation="write_outcome_pack_spreadsheet_file",
        pack_id=outcome_packs.SPREADSHEET_OPS_PACK_ID,
        file_path=target,
        action="spreadsheet_create",
        storage="local",
        payload_bytes=128,
    )

    assert calls[0]["operation"] == "write_outcome_pack_spreadsheet_file"
    assert calls[0]["state_class"] == "outcome_pack_files"
    assert calls[0]["payload"]["pack_id"] == outcome_packs.SPREADSHEET_OPS_PACK_ID
    assert calls[0]["payload"]["file_path"] == str(target)


def test_artifact_content_file_write_calls_rust_gate(monkeypatch, tmp_path):
    from server_modules import artifact_service

    calls = _install_allow_gate(monkeypatch, artifact_service.rust_runtime_kernel_client)
    target = tmp_path / "artifact.bin"

    artifact_service._enforce_artifact_content_file_decision(
        artifact_id="artifact-1",
        run_id="run-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        object_key="objects/artifact.bin",
        target=target,
        content_bytes=256,
    )

    assert calls[0]["operation"] == "write_artifact_content_file"
    assert calls[0]["state_class"] == "artifact_content_files"
    assert calls[0]["run_id"] == "run-1"
    assert calls[0]["payload"]["artifact_id"] == "artifact-1"
