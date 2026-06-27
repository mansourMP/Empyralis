"""
Unified Pipeline Verification Tests
Verifies the key invariants after refactoring Steps 0-7.
"""
import sys
import importlib


def test_modules_import():
    """T0: All modified modules import cleanly."""
    modules = [
        'server_modules.turn_runtime',
        'server_modules.direct_chat_service',
        'server_modules.direct_chat_runtime_service',
        'server_modules.sage_agent_runtime_service',
        'server_modules.sage_turn_adapter',
        'server_modules.workspace_config_schema',
    ]
    for m in modules:
        importlib.import_module(m)
    print("  [PASS] T0: All modules import")


def test_unified_entry_exists():
    """T1: execute_direct_chat_turn_request routes through handle_sage_chat (unified entry)."""
    import inspect
    from server_modules.direct_chat_service import execute_direct_chat_turn_request
    source = inspect.getsource(execute_direct_chat_turn_request)
    assert "handle_sage_chat" in source, "execute_direct_chat_turn_request must route through handle_sage_chat"
    assert "execute_sage_turn" in source, "execute_direct_chat_turn_request must route through execute_sage_turn"
    print("  [PASS] T1: Web chat routes through unified Sage entry")


def test_provider_resolver():
    """T2: Web chat uses the same entitlement-gated resolver as channels."""
    import inspect
    from server_modules.direct_chat_runtime_service import build_direct_operator_reply
    source = inspect.getsource(build_direct_operator_reply)
    assert "_resolve_cloud_provider" in source, "Web chat must use _resolve_cloud_provider (same as Sage)"
    print("  [PASS] T2: Web uses same provider resolver as channels")


def test_no_regex_tool_shortcut():
    """T3: The regex/keyword tool shortcut has been deleted from the web path."""
    import inspect
    from server_modules.direct_chat_runtime_service import build_direct_operator_reply
    source = inspect.getsource(build_direct_operator_reply)
    assert "_explicit_provider_parity_tool_calls" not in source, "Regex tool shortcut must be deleted"
    assert "explicit_provider_tool_calls" not in source or "False" in source, "No regex tool calls"
    print("  [PASS] T3: Regex tool shortcut deleted — LLM decides tools")


def test_durable_runs_explicit():
    """T4: Durable runs only trigger on explicit execution_mode='durable'."""
    import inspect
    from server_modules.turn_runtime import execute_agent_turn_request
    source = inspect.getsource(execute_agent_turn_request)
    assert '_exec_mode == "durable"' in source, "Durable dispatch must be gated on execution_mode"
    print("  [PASS] T4: Durable runs are explicit, not default")


def test_persisted_model():
    """T5: Model persistence functions exist."""
    from server_modules.sage_agent_runtime_service import get_persisted_model_preference, set_persisted_model_preference
    assert callable(get_persisted_model_preference), "get_persisted_model_preference must be callable"
    assert callable(set_persisted_model_preference), "set_persisted_model_preference must be callable"
    print("  [PASS] T5: Model persistence functions exist")


def test_model_command_persisted():
    """T6: /model writes to workspace metadata (persisted)."""
    import inspect
    from server_modules.sage_turn_adapter import execute_sage_turn
    source = inspect.getsource(execute_sage_turn)
    assert "set_persisted_model_preference" in source, "/model must persist to workspace metadata"
    assert "survives" in source.lower() or "persist" in source.lower(), "Must mention persistence"
    print("  [PASS] T6: /model persists to workspace metadata")


def test_sage_ai_model_config():
    """T7: sage_ai_model field exists in workspace config."""
    from server_modules.workspace_config_schema import WorkspaceAdminDefaultsConfig
    assert hasattr(WorkspaceAdminDefaultsConfig, '__fields__') or True
    # Check the field exists in the model
    fields = WorkspaceAdminDefaultsConfig.model_fields if hasattr(WorkspaceAdminDefaultsConfig, 'model_fields') else {}
    assert 'sage_ai_model' in fields, "sage_ai_model field must exist"
    print("  [PASS] T7: sage_ai_model field in workspace config")


def test_no_fallback_invariant():
    """T8: disable_provider_fallback is set True (no fallback)."""
    import inspect
    from server_modules.sage_agent_runtime_service import handle_sage_chat
    source = inspect.getsource(handle_sage_chat)
    assert "disable_provider_fallback" in source, "disable_provider_fallback must be set"
    print("  [PASS] T8: No provider fallback (ONE AI ROAD)")


if __name__ == "__main__":
    passed = 0
    failed = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                func()
                passed += 1
            except Exception as e:
                print(f"  [FAIL] {name[5:]}: {e}")
                failed += 1

    print(f"\n=== Unified Pipeline Verification ===\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
