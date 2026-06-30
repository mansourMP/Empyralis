from server_modules import runtime_config as config
from server_modules import shared as shared
from server_modules import runtime_common as common
from server_modules.schemas import GenericObjectBody
from server_modules import runs_core as runs_core
globals().update({key: value for key, value in vars(config).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(shared).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(common).items() if not key.startswith("__")})
globals().update({key: value for key, value in vars(runs_core).items() if not key.startswith("__")})
def _sanitize_skill_card(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    skill_id = str(item.get("id") or "").strip().lower()
    title = str(item.get("title") or "").strip()
    intent = str(item.get("intent") or "").strip()
    if not skill_id or not title or not intent:
        return None
    tools_raw = item.get("tools")
    tools: List[str] = []
    if isinstance(tools_raw, list):
        for entry in tools_raw:
            token = str(entry or "").strip().lower()
            if token and token not in tools:
                tools.append(token[:120])
    runtime_tools_raw = item.get("runtime_tools")
    runtime_tools: List[str] = []
    if isinstance(runtime_tools_raw, list):
        for entry in runtime_tools_raw:
            token = normalize_action_id(entry)
            if token and token in TOOL_CONTRACTS and token not in runtime_tools:
                runtime_tools.append(token)
    guardrail = str(item.get("guardrail") or "").strip()
    preferred_target = ""
    raw_target = str(item.get("preferred_target") or "").strip().lower()
    if raw_target in VALID_EXECUTION_TARGETS:
        preferred_target = raw_target
    preferred_trust_mode = ""
    raw_trust_mode = str(item.get("preferred_trust_mode") or "").strip().lower()
    if raw_trust_mode:
        normalized_trust_mode = normalize_trust_mode(raw_trust_mode)
        if normalized_trust_mode in VALID_TRUST_MODES:
            preferred_trust_mode = normalized_trust_mode
    policy_mode = "warn"
    raw_policy_mode = str(item.get("policy_mode") or item.get("skill_policy_mode") or "").strip().lower()
    if raw_policy_mode in {"off", "warn", "enforce"}:
        policy_mode = raw_policy_mode
    version = str(item.get("version") or "").strip()
    author = str(item.get("author") or "").strip()
    category = str(item.get("category") or "").strip()
    card = {
        "id": skill_id[:80],
        "title": title[:120],
        "intent": intent[:1000],
        "tools": tools[:30],
        "guardrail": guardrail[:1000],
        "policy_mode": policy_mode,
    }
    if runtime_tools:
        card["runtime_tools"] = runtime_tools[:30]
    if preferred_target:
        card["preferred_target"] = preferred_target
    if preferred_trust_mode:
        card["preferred_trust_mode"] = preferred_trust_mode
    if version:
        card["version"] = version[:40]
    if author:
        card["author"] = author[:80]
    if category:
        card["category"] = category[:80]
    return card


def _sanitize_public_installed_skill(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    skill_id = str(item.get("id") or "").strip().lower()
    name = str(item.get("name") or "").strip()
    if not skill_id or not name:
        return None
    return {
        "id": skill_id[:120],
        "name": name[:160],
        "description": str(item.get("description") or "").strip()[:500],
        "enabled": bool(item.get("enabled")),
        "has_query_handler": bool(item.get("has_query_handler")),
        "has_snapshot_worker": bool(item.get("has_snapshot_worker")),
    }


def _sanitize_public_solution_ui_preset(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}

    def _sanitize_nav(raw_items: Any) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        if not isinstance(raw_items, list):
            return out
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            label = str(raw.get("label") or "").strip()
            href = str(raw.get("href") or "").strip()
            icon = str(raw.get("icon") or "").strip()
            if not label or not href:
                continue
            out.append({"label": label[:120], "href": href[:240], "icon": icon[:80]})
        return out[:20]

    sanitized: Dict[str, Any] = {}
    nav_items = _sanitize_nav(item.get("nav_items"))
    admin_bottom_items = _sanitize_nav(item.get("admin_bottom_items"))
    if nav_items:
        sanitized["nav_items"] = nav_items
    if admin_bottom_items:
        sanitized["admin_bottom_items"] = admin_bottom_items
    return sanitized


def _sanitize_public_installed_solution(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    solution_id = str(item.get("id") or "").strip().lower()
    name = str(item.get("name") or "").strip()
    if not solution_id or not name:
        return None
    return {
        "id": solution_id[:120],
        "name": name[:160],
        "description": str(item.get("description") or "").strip()[:500],
        "enabled": bool(item.get("enabled")),
        "route_base": str(item.get("route_base") or "").strip()[:240] or None,
        "primary_route": str(item.get("primary_route") or "").strip()[:240] or None,
        "required_skills": _sanitize_skill_id_list(item.get("required_skills")),
        "ui_preset": _sanitize_public_solution_ui_preset(item.get("ui_preset")),
        "status": item.get("status") if isinstance(item.get("status"), dict) else {},
    }


def _sanitize_skill_id_list(raw_items: Any) -> List[str]:
    if not isinstance(raw_items, list):
        return []
    out: List[str] = []
    for raw in raw_items:
        token = str(raw or "").strip().lower()
        if not token or token in out:
            continue
        out.append(token[:80])
    return out[:50]


def _compose_skill_prompt_append(skills: List[Dict[str, Any]]) -> str:
    if not skills:
        return ""
    lines = ["Active skill directives (follow unless user overrides explicitly):"]
    for skill in skills:
        title = str(skill.get("title") or "").strip()
        intent = str(skill.get("intent") or "").strip()
        guardrail = str(skill.get("guardrail") or "").strip()
        tools = skill.get("tools") if isinstance(skill.get("tools"), list) else []
        tools_text = ", ".join(str(item).strip() for item in tools if str(item).strip()) or "none"
        runtime_tools = skill.get("runtime_tools") if isinstance(skill.get("runtime_tools"), list) else []
        runtime_tools_text = ", ".join(str(item).strip() for item in runtime_tools if str(item).strip()) or "none"
        preferred_target = str(skill.get("preferred_target") or "").strip()
        preferred_trust_mode = str(skill.get("preferred_trust_mode") or "").strip()
        policy_mode = str(skill.get("policy_mode") or "").strip()
        extra_parts: List[str] = []
        if preferred_target:
            extra_parts.append(f"Preferred target: {preferred_target}.")
        if preferred_trust_mode:
            extra_parts.append(f"Preferred trust mode: {preferred_trust_mode}.")
        if policy_mode:
            extra_parts.append(f"Policy mode: {policy_mode}.")
        extra_suffix = f" {' '.join(extra_parts)}" if extra_parts else ""
        lines.append(f"- {title}: {intent} Guardrail: {guardrail or 'none'}. Tools: {tools_text}. Runtime tools: {runtime_tools_text}.{extra_suffix}")
    return "\n".join(lines).strip()


def _build_skill_contract_from_metadata(
    metadata: Dict[str, Any],
    predicted_tool_ids: List[str],
    trust_mode: str,
    target: str,
) -> Dict[str, Any]:
    bundle = metadata.get("skill_bundle") if isinstance(metadata.get("skill_bundle"), dict) else {}
    skills = bundle.get("skills") if isinstance(bundle.get("skills"), list) else []
    skill_ids = _sanitize_skill_id_list(bundle.get("skill_ids"))
    declared_runtime_tools: List[str] = []
    seen_runtime_tools: Set[str] = set()
    preferred_targets: List[str] = []
    preferred_trust_modes: List[str] = []
    skill_policy_modes: List[str] = []

    for raw in skills:
        if not isinstance(raw, dict):
            continue
        for entry in raw.get("runtime_tools") if isinstance(raw.get("runtime_tools"), list) else []:
            tool_id = normalize_action_id(entry)
            if tool_id and tool_id in TOOL_CONTRACTS and tool_id not in seen_runtime_tools:
                seen_runtime_tools.add(tool_id)
                declared_runtime_tools.append(tool_id)
        target_hint = str(raw.get("preferred_target") or "").strip().lower()
        if target_hint in VALID_EXECUTION_TARGETS and target_hint not in preferred_targets:
            preferred_targets.append(target_hint)
        trust_hint = str(raw.get("preferred_trust_mode") or "").strip().lower()
        if trust_hint:
            normalized_trust = normalize_trust_mode(trust_hint)
            if normalized_trust in VALID_TRUST_MODES and normalized_trust not in preferred_trust_modes:
                preferred_trust_modes.append(normalized_trust)
        policy_hint = str(raw.get("policy_mode") or raw.get("skill_policy_mode") or "").strip().lower()
        if policy_hint in {"off", "warn", "enforce"} and policy_hint not in skill_policy_modes:
            skill_policy_modes.append(policy_hint)

    undeclared_tools = [
        tool_id for tool_id in predicted_tool_ids if declared_runtime_tools and tool_id not in seen_runtime_tools
    ]
    mode = str(metadata.get("skill_policy_mode") or "").strip().lower() or "warn"
    if mode not in {"off", "warn", "enforce"}:
        if "enforce" in skill_policy_modes:
            mode = "enforce"
        elif "warn" in skill_policy_modes:
            mode = "warn"
        elif "off" in skill_policy_modes:
            mode = "off"
        else:
            mode = "warn"

    return {
        "scope": str(metadata.get("skill_scope") or "").strip() or None,
        "skill_ids": skill_ids,
        "declared_runtime_tools": declared_runtime_tools,
        "preferred_targets": preferred_targets,
        "preferred_trust_modes": preferred_trust_modes,
        "skill_policy_modes": skill_policy_modes,
        "undeclared_tools": undeclared_tools,
        "policy_mode": mode,
        "target_conflict": bool(preferred_targets and target not in preferred_targets),
        "trust_conflict": bool(preferred_trust_modes and trust_mode not in preferred_trust_modes),
    }

def _runtime_skill_catalog_map(custom_skills: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    merged.extend(RUNTIME_BUILTIN_SKILLS)
    merged.extend(custom_skills)
    out: Dict[str, Dict[str, Any]] = {}
    for raw in merged:
        card = _sanitize_skill_card(raw)
        if not card:
            continue
        out[card["id"]] = card
    return out


def _runtime_skill_bundle_for_scope(scope: str) -> Dict[str, Any]:
    scope_key = str(scope or "").strip().lower()
    if scope_key not in {"assistant_defaults", "automation_defaults"}:
        scope_key = "assistant_defaults"
    with RUNTIME_SKILLS_LOCK:
        state = dict(RUNTIME_SKILLS_STATE)
        bindings = dict(state.get("bindings") or {})
        custom_skills = list(state.get("custom_skills") or [])
    selected_ids = _sanitize_skill_id_list(bindings.get(scope_key))
    if not selected_ids:
        return {"scope": scope_key, "skill_ids": [], "skills": [], "prompt_append": ""}
    skill_map = _runtime_skill_catalog_map(custom_skills)
    active: List[Dict[str, Any]] = []
    for skill_id in selected_ids:
        skill = skill_map.get(skill_id)
        if skill:
            active.append(skill)
    prompt_append = _compose_skill_prompt_append(active)
    return {
        "scope": scope_key,
        "skill_ids": [str(item.get("id") or "") for item in active],
        "skills": active,
        "prompt_append": prompt_append,
    }


def _inject_runtime_skill_defaults(context: Dict[str, Any]) -> None:
    if not isinstance(context, dict):
        return
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    if not metadata.get("skill_bundle") and not metadata.get("skill_prompt_append"):
        scope_key = "assistant_defaults"
        if str(metadata.get("source") or "").strip().lower() in {"weekly_scheduler", "scheduled"}:
            scope_key = "automation_defaults"
        if str(metadata.get("outcome_pack") or "").strip():
            scope_key = "automation_defaults"
        bundle = _runtime_skill_bundle_for_scope(scope_key)
        if bundle.get("skills"):
            metadata["skill_scope"] = bundle.get("scope")
            metadata["skill_bundle"] = {
                "skill_ids": bundle.get("skill_ids"),
                "skills": bundle.get("skills"),
            }
            metadata["skill_prompt_append"] = bundle.get("prompt_append")
    installed_prompt = build_active_skill_prompt_append()
    if installed_prompt:
        metadata["installed_skill_ids"] = active_installed_skill_ids()
        metadata["skill_prompt_append"] = merge_skill_prompt_append(
            str(metadata.get("skill_prompt_append") or "").strip(),
            installed_prompt,
        )
    context["metadata"] = metadata


def _persist_runtime_skills_state() -> None:
    with RUNTIME_SKILLS_LOCK:
        payload = {
            "version": 1,
            "custom_skills": list(RUNTIME_SKILLS_STATE.get("custom_skills") or []),
            "bindings": dict(RUNTIME_SKILLS_STATE.get("bindings") or {}),
            "updated_at": RUNTIME_SKILLS_STATE.get("updated_at"),
        }
    _safe_write_json(ORION_RUNTIME_SKILLS_FILE, payload)


def _load_runtime_skills_state() -> None:
    payload = _safe_read_json(
        ORION_RUNTIME_SKILLS_FILE,
        {
            "version": 1,
            "custom_skills": [],
            "bindings": {"assistant_defaults": [], "automation_defaults": []},
            "updated_at": None,
        },
    )
    custom_raw = payload.get("custom_skills") if isinstance(payload.get("custom_skills"), list) else []
    custom_skills: List[Dict[str, Any]] = []
    for item in custom_raw:
        card = _sanitize_skill_card(item)
        if card:
            custom_skills.append(card)
    bindings_raw = payload.get("bindings") if isinstance(payload.get("bindings"), dict) else {}
    bindings = {
        "assistant_defaults": _sanitize_skill_id_list(bindings_raw.get("assistant_defaults")),
        "automation_defaults": _sanitize_skill_id_list(bindings_raw.get("automation_defaults")),
    }
    with RUNTIME_SKILLS_LOCK:
        RUNTIME_SKILLS_STATE["version"] = 1
        RUNTIME_SKILLS_STATE["custom_skills"] = custom_skills
        RUNTIME_SKILLS_STATE["bindings"] = bindings
        RUNTIME_SKILLS_STATE["updated_at"] = str(payload.get("updated_at") or "").strip() or None


def _runtime_skills_snapshot() -> Dict[str, Any]:
    with RUNTIME_SKILLS_LOCK:
        custom = list(RUNTIME_SKILLS_STATE.get("custom_skills") or [])
        bindings = dict(RUNTIME_SKILLS_STATE.get("bindings") or {})
        updated_at = RUNTIME_SKILLS_STATE.get("updated_at")
    return {
        "version": 1,
        "custom_skills": custom,
        "bindings": {
            "assistant_defaults": _sanitize_skill_id_list(bindings.get("assistant_defaults")),
            "automation_defaults": _sanitize_skill_id_list(bindings.get("automation_defaults")),
        },
        "registry": {
            "builtin_count": len(_runtime_skill_catalog_map([])),
            "custom_count": len(custom),
            "installed_count": len(_runtime_skill_catalog_map(custom)),
            "assistant_bundle_count": len(_runtime_skill_bundle_for_scope("assistant_defaults").get("skill_ids") or []),
            "automation_bundle_count": len(_runtime_skill_bundle_for_scope("automation_defaults").get("skill_ids") or []),
        },
        "updated_at": updated_at,
    }


def _persist_doctor_snapshot(report: Dict[str, Any]) -> None:
    generated_at = str(report.get("generated_at") or _utc_now_iso())
    snapshot = {
        "generated_at": generated_at,
        "ok": bool(report.get("ok")),
        "overview": report.get("overview") if isinstance(report.get("overview"), dict) else {},
        "summary": report.get("summary") if isinstance(report.get("summary"), dict) else {"pass": 0, "warn": 0, "fail": 0},
        "checks": report.get("checks") if isinstance(report.get("checks"), list) else [],
        "priority_fixes": report.get("priority_fixes") if isinstance(report.get("priority_fixes"), list) else [],
    }
    _safe_write_json(ORION_DOCTOR_REPORT_FILE, snapshot)

    payload = _safe_read_json(
        ORION_DOCTOR_HISTORY_FILE,
        {"version": 1, "updated_at": None, "items": []},
    )
    items_raw = payload.get("items") if isinstance(payload.get("items"), list) else []
    items: List[Dict[str, Any]] = [item for item in items_raw if isinstance(item, dict)]
    items = [item for item in items if str(item.get("generated_at") or "") != generated_at]
    items.insert(0, snapshot)
    payload["version"] = 1
    payload["updated_at"] = _utc_now_iso()
    payload["items"] = items[: max(10, int(ORION_DOCTOR_HISTORY_LIMIT or 120))]
    _safe_write_json(ORION_DOCTOR_HISTORY_FILE, payload)


async def doctor_history(limit: int = 10):
    safe_limit = max(1, min(limit, 30))
    payload = _safe_read_json(
        ORION_DOCTOR_HISTORY_FILE,
        {"version": 1, "updated_at": None, "items": []},
    )
    items_raw = payload.get("items") if isinstance(payload.get("items"), list) else []
    items: List[Dict[str, Any]] = []
    for item in items_raw[:safe_limit]:
        if not isinstance(item, dict):
            continue
        overview = item.get("overview") if isinstance(item.get("overview"), dict) else {}
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        items.append(
            {
                "generated_at": item.get("generated_at"),
                "ok": bool(item.get("ok")),
                "overview": {
                    "status": str(overview.get("status") or "warn"),
                    "title": str(overview.get("title") or "").strip(),
                    "detail": str(overview.get("detail") or "").strip(),
                },
                "summary": {
                    "pass": int(summary.get("pass") or 0),
                    "warn": int(summary.get("warn") or 0),
                    "fail": int(summary.get("fail") or 0),
                },
            }
        )
    return {
        "items": items,
        "count": len(items),
        "updated_at": payload.get("updated_at"),
    }

def _env_truthy(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_operator_risk_levels(raw_value: Optional[str], fallback: List[str]) -> List[str]:
    allowed = {"low", "medium", "high"}
    if raw_value is None:
        base = list(fallback)
    else:
        base = [part.strip().lower() for part in str(raw_value).split(",") if part.strip()]
        if not base:
            base = list(fallback)
    seen: Set[str] = set()
    normalized: List[str] = []
    for level in base:
        if level not in allowed or level in seen:
            continue
        normalized.append(level)
        seen.add(level)
    return normalized


def _build_cognitive_operator_policy() -> Dict[str, Any]:
    enabled = _env_truthy(os.getenv("ORION_COGNITIVE_OPERATOR_ENABLED"), default=True)
    trust_mode_default = (str(os.getenv("ORION_COGNITIVE_OPERATOR_TRUST_MODE") or "guarded").strip().lower() or "guarded")
    if trust_mode_default not in {"auto", "guarded", "strict"}:
        trust_mode_default = "guarded"

    timeout_seconds = 60
    timeout_raw = str(os.getenv("ORION_COGNITIVE_OPERATOR_TIMEOUT_SECONDS") or "60").strip()
    if timeout_raw.isdigit():
        timeout_seconds = max(1, int(timeout_raw))

    max_output_chars = 1600
    max_output_raw = str(os.getenv("ORION_COGNITIVE_OPERATOR_MAX_OUTPUT_CHARS") or "1600").strip()
    if max_output_raw.isdigit():
        max_output_chars = max(200, int(max_output_raw))

    root = str(os.getenv("ORION_COGNITIVE_OPERATOR_ROOT") or "").strip() or str(Path.cwd())
    allow_shell_fallback = _env_truthy(os.getenv("ORION_COGNITIVE_OPERATOR_ALLOW_SHELL_FALLBACK"), default=False)
    allow_prefixes_raw = (
        str(os.getenv("ORION_LOCAL_COMPANION_COMMAND_ALLOW_PREFIXES") or "").strip()
        or str(os.getenv("ORION_COGNITIVE_OPERATOR_ALLOW_PREFIXES") or "").strip()
    )
    if allow_prefixes_raw:
        allow_prefixes = [part.strip() for part in allow_prefixes_raw.split(",") if part.strip()]
    else:
        allow_prefixes = list(DEFAULT_LOCAL_COMPANION_ALLOW_PREFIXES)
    strict_approval_all = _env_truthy(os.getenv("ORION_COGNITIVE_OPERATOR_STRICT_APPROVAL_ALL"), default=False)
    strict_default = ["low", "medium", "high"] if strict_approval_all else ["medium", "high"]
    approval_levels_by_trust = {
        "auto": _normalize_operator_risk_levels(
            os.getenv("ORION_COGNITIVE_OPERATOR_APPROVAL_LEVELS_AUTO"),
            [],
        ),
        "guarded": _normalize_operator_risk_levels(
            os.getenv("ORION_COGNITIVE_OPERATOR_APPROVAL_LEVELS_GUARDED"),
            ["medium", "high"],
        ),
        "strict": _normalize_operator_risk_levels(
            os.getenv("ORION_COGNITIVE_OPERATOR_APPROVAL_LEVELS_STRICT"),
            strict_default,
        ),
    }
    return {
        "enabled": enabled,
        "trust_mode_default": trust_mode_default,
        "approval_levels_by_trust_mode": approval_levels_by_trust,
        "strict_approval_all": strict_approval_all,
        "allow_prefixes": allow_prefixes,
        "capabilities": supported_capability_catalog(Path(__file__).resolve().parent),
        "root": root,
        "timeout_seconds": timeout_seconds,
        "max_output_chars": max_output_chars,
        "allow_shell_fallback": allow_shell_fallback,
        "command_allowlist_enforced": True,
    }

async def doctor():
    from server_modules.health_core import health

    health_data = await health()
    report = build_doctor_report(
        health_data,
        {
            "ORION_AUTH_REQUIRED": ORION_AUTH_REQUIRED,
            "ORION_DEV_INSECURE_NO_AUTH": ORION_DEV_INSECURE_NO_AUTH,
            "ORION_API_KEY": ORION_API_KEY,
            "FRONTEND_ORIGINS": FRONTEND_ORIGINS,
            "CONTROL_PLANE_ORIGINS": CONTROL_PLANE_ORIGINS,
            "CONTROL_PLANE_RATE_LIMIT_PER_MINUTE": CONTROL_PLANE_RATE_LIMIT_PER_MINUTE,
            "CONTROL_PLANE_RATE_LIMIT_BURST": CONTROL_PLANE_RATE_LIMIT_BURST,
            "ORION_RUN_TIMEOUT_SECONDS": ORION_RUN_TIMEOUT_SECONDS,
            "ORION_MAX_RETRIES": ORION_MAX_RETRIES,
            "ORION_SCHEDULER_POLL_SECONDS": ORION_SCHEDULER_POLL_SECONDS,
            "ORION_SCHEDULER_ENABLED": ORION_SCHEDULER_ENABLED,
            "ORION_LOCAL_COMPANION_ENABLED": ORION_LOCAL_COMPANION_ENABLED,
            "EMPYRALIS_STATE_HOME": str(EMPYRALIS_STATE_HOME),
            "VAULT_FILE": str(VAULT_FILE),
            "ORION_VAULT_CIPHER_PREFIX": ORION_VAULT_CIPHER_PREFIX,
            "ORION_VAULT_KDF_ITERATIONS": ORION_VAULT_KDF_ITERATIONS,
            "ORION_VAULT_LEGACY_OPENSSL_DECRYPT": ORION_VAULT_LEGACY_OPENSSL_DECRYPT,
            "ORION_VAULT_LEGACY_OPENSSL_ENCRYPT_FALLBACK": ORION_VAULT_LEGACY_OPENSSL_ENCRYPT_FALLBACK,
            "ORION_SETUP_SESSIONS_FILE": str(ORION_SETUP_SESSIONS_FILE),
            "ORION_PROVIDER_PROFILES_FILE": str(ORION_PROVIDER_PROFILES_FILE),
            "ORION_IDEMPOTENCY_FILE": str(ORION_IDEMPOTENCY_FILE),
            "ORION_RUNTIME_STATE_DB": str(ORION_RUNTIME_STATE_DB),
            "ORION_MEMORY_ENABLED": ORION_MEMORY_ENABLED,
            "ORION_MEMORY_DB_PATH": ORION_MEMORY_DB_PATH,
            "ORION_MEMORY_LANCEDB_URI": ORION_MEMORY_LANCEDB_URI,
        },
    )
    _persist_doctor_snapshot(report)
    return report

async def get_runtime_skills_state():
    installed: List[Dict[str, Any]] = []
    for item in list_installed_skills():
        sanitized = _sanitize_public_installed_skill(item)
        if sanitized:
            installed.append(sanitized)
    state = _runtime_skills_snapshot()
    return {
        "ok": True,
        "state": state,
        "builtins": RUNTIME_BUILTIN_SKILLS,
        "installed": installed,
    }

async def get_runtime_solutions_state():
    installed: List[Dict[str, Any]] = []
    active: List[Dict[str, Any]] = []
    for item in list_installed_solutions():
        sanitized = _sanitize_public_installed_solution(item)
        if sanitized:
            installed.append(sanitized)
    for item in active_installed_solutions():
        sanitized = _sanitize_public_installed_solution(item)
        if sanitized:
            active.append(sanitized)
    return {
        "ok": True,
        "installed": installed,
        "active": active,
        "mcp_endpoint": EMPYRALIST_MCP_ENDPOINT,
        "mcp_tools": EMPYRALIST_MCP_TOOLS,
    }

async def put_runtime_skills_state(body: RuntimeSkillsStateUpsertRequest):
    body.validate_fields()
    custom_skills: Optional[List[Dict[str, Any]]] = None
    if body.custom_skills is not None:
        custom_skills = []
        for item in body.custom_skills:
            card = _sanitize_skill_card(item)
            if card:
                custom_skills.append(card)
    bindings_patch: Optional[Dict[str, List[str]]] = None
    if body.bindings is not None:
        bindings_patch = {
            "assistant_defaults": _sanitize_skill_id_list(body.bindings.get("assistant_defaults")),
            "automation_defaults": _sanitize_skill_id_list(body.bindings.get("automation_defaults")),
        }
    with RUNTIME_SKILLS_LOCK:
        if custom_skills is not None:
            RUNTIME_SKILLS_STATE["custom_skills"] = custom_skills
        current_bindings = dict(RUNTIME_SKILLS_STATE.get("bindings") or {})
        if bindings_patch is not None:
            current_bindings.update(bindings_patch)
        current_bindings["assistant_defaults"] = _sanitize_skill_id_list(current_bindings.get("assistant_defaults"))
        current_bindings["automation_defaults"] = _sanitize_skill_id_list(current_bindings.get("automation_defaults"))
        RUNTIME_SKILLS_STATE["bindings"] = current_bindings
        RUNTIME_SKILLS_STATE["updated_at"] = _utc_now_iso()
    _persist_runtime_skills_state()
    state = _runtime_skills_snapshot()
    return {
        "ok": True,
        "state": state,
        "assistant_bundle": _runtime_skill_bundle_for_scope("assistant_defaults"),
        "automation_bundle": _runtime_skill_bundle_for_scope("automation_defaults"),
    }

async def dispatch_installed_solution_api(
    solution_id: str,
    subpath: str,
    request: Request,
    body: Optional[GenericObjectBody] = None,
):
    from server_modules.health_core import _ensure_installed_solution

    _ensure_installed_solution(solution_id)
    payload = body.as_dict() if body is not None else {}
    try:
        payload = call_installed_solution_hook(
            solution_id,
            "handle_api_request",
            request.method,
            subpath,
            payload,
            dict(request.query_params),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(payload, Response):
        return payload
    if isinstance(payload, (dict, list)):
        return payload
    return {"ok": True}
