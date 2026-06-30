"""Sage Doctor diagnostics service -- runs health checks across the Sage stack."""

from __future__ import annotations

from typing import Any, Dict, List


CHECK_SPECS: List[Dict[str, str]] = [
    {"id": "agent_computer_selection", "label": "Agent Computer Selection", "group": "Agent Computer"},
    {"id": "gateway_connection", "label": "Gateway Connection", "group": "Agent Computer"},
    {"id": "browser_runtime", "label": "Browser Runtime", "group": "Agent Computer"},
    {"id": "telegram_personal_state", "label": "Telegram Personal Channel", "group": "Channels"},
    {"id": "whatsapp_personal_state", "label": "WhatsApp Personal Channel", "group": "Channels"},
    {"id": "slack_credentials", "label": "Slack Credentials", "group": "Channels"},
    {"id": "discord_credentials", "label": "Discord Credentials", "group": "Channels"},
    {"id": "google_workspace_credentials", "label": "Google Workspace Credentials", "group": "Channels"},
    {"id": "mcp_registry", "label": "MCP Server Registry", "group": "Providers"},
    {"id": "model_provider_credentials", "label": "Model Provider Credentials", "group": "Providers"},
    {"id": "quota_store", "label": "Durable Quota Store", "group": "Infrastructure"},
    {"id": "outbox_replay_queue", "label": "Outbox Replay Queue", "group": "Infrastructure"},
    {"id": "approval_policy_state", "label": "Approval Policy State", "group": "Policy"},
]


def _result(check_id: str, label: str, status: str, detail: str, next_action: str, group: str) -> Dict[str, str]:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "detail": detail,
        "next_action": next_action,
        "group": group,
    }


def _pass_spec(spec: Dict[str, str], detail: str = "OK") -> Dict[str, str]:
    return _result(spec["id"], spec["label"], "pass", detail, "None", spec["group"])


def _fail_spec(spec: Dict[str, str], detail: str, next_action: str) -> Dict[str, str]:
    return _result(spec["id"], spec["label"], "fail", detail, next_action, spec["group"])


def _warn_spec(spec: Dict[str, str], detail: str, next_action: str = "") -> Dict[str, str]:
    return _result(spec["id"], spec["label"], "warn", detail, next_action or "Review configuration.", spec["group"])


def _skip_spec(spec: Dict[str, str], reason: str) -> Dict[str, str]:
    return _result(spec["id"], spec["label"], "skip", reason, "Complete prerequisite checks.", spec["group"])


def _spec_by_id(check_id: str) -> Dict[str, str]:
    for s in CHECK_SPECS:
        if s["id"] == check_id:
            return s
    return {"id": check_id, "label": check_id, "group": "Unknown"}


class SageDoctorService:
    """Runs diagnostics across the Sage stack.

    Each check is a static method named ``_check_<id>``.  All heavy imports
    are done inside the method body to avoid circular-import problems.
    """

    @staticmethod
    def check_all(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, Any]:
        """Run the full suite of checks and return structured results."""
        checks: List[Dict[str, str]] = []

        for spec in CHECK_SPECS:
            handler_name = f"_check_{spec['id']}"
            handler = getattr(SageDoctorService, handler_name, None)
            if handler is None:
                checks.append(_skip_spec(spec, "No handler registered for this check."))
                continue
            try:
                checks.append(handler(workspace_id, tenant_id, user_id))
            except Exception as exc:
                checks.append(
                    _result(
                        spec["id"], spec["label"], "fail",
                        f"Check raised an exception: {exc}",
                        "Investigate and fix the error.",
                        spec["group"],
                    )
                )

        total = len(checks)
        passed = sum(1 for c in checks if c["status"] == "pass")
        failed = sum(1 for c in checks if c["status"] == "fail")
        warned = sum(1 for c in checks if c["status"] == "warn")
        skipped = sum(1 for c in checks if c["status"] == "skip")

        return {
            "checks": checks,
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "warned": warned,
                "skipped": skipped,
                "healthy": failed == 0,
            },
        }

    # ------------------------------------------------------------------
    #   Individual checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_agent_computer_selection(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, str]:
        import server_modules.sage_agent_computer_selection_service as sel

        spec = _spec_by_id("agent_computer_selection")
        selection = sel.get_selection(workspace_id=workspace_id, user_id=user_id)
        if selection is None:
            return _fail_spec(
                spec,
                "Agent Computer not selected.",
                "Go to Agent Computer settings and select a device.",
            )
        gw_id = str(selection.get("selected_gateway_id") or "")
        return _pass_spec(spec, detail=f"Agent Computer selected: {gw_id}")

    @staticmethod
    def _check_gateway_connection(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, str]:
        import server_modules.gateway_registry_service as gw_registry

        spec = _spec_by_id("gateway_connection")
        result = gw_registry.list_workspace_gateways(workspace_id=workspace_id)
        items = result.get("items") or []
        if not items:
            return _warn_spec(
                spec,
                "No gateways registered.",
                "Install or connect a gateway device.",
            )

        for gw in items:
            if gw.get("connection_status") == "online":
                return _pass_spec(
                    spec,
                    detail=f"Gateway online: {gw.get('gateway_id', 'unknown')}",
                )

        return _fail_spec(
            spec,
            "Gateway is paired but offline.",
            "Check gateway power and network.",
        )

    @staticmethod
    def _check_browser_runtime(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, str]:
        import server_modules.gateway_registry_service as gw_registry

        spec = _spec_by_id("browser_runtime")
        result = gw_registry.list_workspace_gateways(workspace_id=workspace_id)
        items = result.get("items") or []
        if not items:
            return _skip_spec(spec, "No gateways registered; cannot check browser capability.")

        has_browser = False
        for gw in items:
            caps = gw.get("capabilities") or []
            if "browser_automation" in caps:
                has_browser = True
                break

        if has_browser:
            return _pass_spec(spec, detail="Browser runtime available on a gateway.")
        return _warn_spec(
            spec,
            "No gateway with browser_automation capability found.",
            "Ensure at least one gateway supports browser automation.",
        )

    @staticmethod
    def _check_telegram_personal_state(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, str]:
        import server_modules.personal_channels_repository as pcr
        import server_modules.personal_channels_service as pcs

        spec = _spec_by_id("telegram_personal_state")
        gateway_id = SageDoctorService._resolve_gateway_id(workspace_id, user_id)
        if not gateway_id:
            return _skip_spec(spec, "No Agent Computer selected; cannot check personal channels.")

        state = pcr.get_telegram_state(
            gateway_id,
            channel_key=pcs.TELEGRAM_PERSONAL_CHANNEL_KEY,
        )
        if state is None or str(state.get("status") or "").strip().lower() != "connected":
            return _fail_spec(
                spec,
                "Telegram personal channel not configured.",
                "Set up Telegram personal channel via the Agent Computer interface.",
            )
        return _pass_spec(spec, detail="Telegram personal channel connected.")

    @staticmethod
    def _check_whatsapp_personal_state(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, str]:
        import server_modules.personal_channels_repository as pcr
        import server_modules.personal_channels_service as pcs

        spec = _spec_by_id("whatsapp_personal_state")
        gateway_id = SageDoctorService._resolve_gateway_id(workspace_id, user_id)
        if not gateway_id:
            return _skip_spec(spec, "No Agent Computer selected; cannot check personal channels.")

        state = pcr.get_whatsapp_state(
            gateway_id,
            channel_key=pcs.WHATSAPP_PERSONAL_CHANNEL_KEY,
        )
        if state is None or str(state.get("status") or "").strip().lower() != "connected":
            return _fail_spec(
                spec,
                "WhatsApp personal channel not configured.",
                "Set up WhatsApp personal channel via the Agent Computer interface.",
            )
        return _pass_spec(spec, detail="WhatsApp personal channel connected.")

    @staticmethod
    def _check_slack_credentials(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, str]:
        spec = _spec_by_id("slack_credentials")
        connectors = SageDoctorService._list_vault_connectors(workspace_id)
        has_slack = any(
            c.get("connector") == "slack"
            for c in connectors
        )
        if has_slack:
            return _pass_spec(spec, detail="Slack credentials found in vault.")
        return _fail_spec(
            spec,
            "Slack credentials not found.",
            "Connect Slack via OAuth in the Integrations settings.",
        )

    @staticmethod
    def _check_discord_credentials(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, str]:
        spec = _spec_by_id("discord_credentials")
        connectors = SageDoctorService._list_vault_connectors(workspace_id)
        has_discord = any(
            c.get("connector") == "discord_bot"
            for c in connectors
        )
        if has_discord:
            return _pass_spec(spec, detail="Discord credentials found in vault.")
        return _fail_spec(
            spec,
            "Discord credentials not found.",
            "Connect Discord via OAuth in the Integrations settings.",
        )

    @staticmethod
    def _check_google_workspace_credentials(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, str]:
        spec = _spec_by_id("google_workspace_credentials")
        connectors = SageDoctorService._list_vault_connectors(workspace_id)
        has_gws = any(
            c.get("connector") == "google_workspace"
            for c in connectors
        )
        if has_gws:
            return _pass_spec(spec, detail="Google Workspace credentials found in vault.")
        return _fail_spec(
            spec,
            "Google Workspace credentials not found.",
            "Connect Google Workspace via OAuth in the Integrations settings.",
        )

    @staticmethod
    def _check_mcp_registry(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, str]:
        import server_modules.mcp_registry_service as mcp

        spec = _spec_by_id("mcp_registry")
        servers = mcp.list_workspace_mcp_servers(workspace_id)
        if not servers:
            return _warn_spec(
                spec,
                "No MCP servers registered.",
                "Register an MCP server in the workspace settings.",
            )
        return _pass_spec(
            spec,
            detail=f"{len(servers)} MCP server(s) registered.",
        )

    @staticmethod
    def _check_model_provider_credentials(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, str]:
        import server_modules.sage_agent_runtime_service as sage_runtime

        spec = _spec_by_id("model_provider_credentials")
        try:
            provider, _credentials = sage_runtime._resolve_cloud_provider(workspace_id)
            return _pass_spec(spec, detail=f"Sage model provider available: {provider}.")
        except Exception as exc:
            return _fail_spec(
                spec,
                f"No usable Sage model provider is configured: {exc}",
                "Configure at least one usable Sage model provider in Provider Settings.",
            )

    @staticmethod
    def _check_quota_store(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, str]:
        spec = _spec_by_id("quota_store")
        try:
            import server_modules.durable_quota_store as dqs

            _ = dqs  # module itself is the check (import succeeded)
            return _pass_spec(spec, detail="Durable Quota Store module is accessible.")
        except Exception as exc:
            return _warn_spec(
                spec,
                f"Durable Quota Store not accessible: {exc}",
                "Investigate quota-store initialisation.",
            )

    @staticmethod
    def _check_outbox_replay_queue(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, str]:
        import server_modules.outbox_service as outbox

        spec = _spec_by_id("outbox_replay_queue")
        try:
            status = outbox.get_outbox_delivery_status()
            undelivered = int(status.get("undelivered_count") or 0)
            poisoned = int(status.get("poisoned_count") or 0)
            if undelivered > 50 or poisoned > 5:
                return _warn_spec(
                    spec,
                    f"Outbox has {undelivered} undelivered and {poisoned} poisoned events.",
                    "Check outbox delivery health and clear stuck events.",
                )
            return _pass_spec(
                spec,
                detail=f"Outbox healthy ({undelivered} pending, {poisoned} poisoned).",
            )
        except Exception as exc:
            return _warn_spec(
                spec,
                f"Outbox status unavailable: {exc}",
                "Investigate outbox service health.",
            )

    @staticmethod
    def _check_approval_policy_state(workspace_id: str, tenant_id: str, user_id: str = "") -> Dict[str, str]:
        import server_modules.policy_service as policy

        spec = _spec_by_id("approval_policy_state")
        try:
            decision = policy.action_policy_from_app_permissions()
            if not isinstance(decision, dict):
                return _fail_spec(
                    spec,
                    "Approval policy returned an unexpected result.",
                    "Review policy configuration.",
                )
            return _pass_spec(spec, detail="Approval policy state is healthy.")
        except Exception as exc:
            return _fail_spec(
                spec,
                f"Approval policy check failed: {exc}",
                "Investigate policy service health.",
            )

    # ------------------------------------------------------------------
    #   Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_gateway_id(workspace_id: str, user_id: str = "") -> str:
        """Return the selected gateway_id for this workspace, or empty string."""
        import server_modules.sage_agent_computer_selection_service as sel

        selection = sel.get_selection(workspace_id=workspace_id, user_id=user_id)
        if selection is not None:
            return str(selection.get("selected_gateway_id") or "")
        return ""

    @staticmethod
    def _list_vault_connectors(workspace_id: str) -> List[Dict[str, Any]]:
        """List vault connector entries for the given workspace."""
        import server_modules.runtime_common as rt_common

        try:
            return list(rt_common.list_vault_connectors(workspace_id=workspace_id) or [])
        except Exception:
            return []
