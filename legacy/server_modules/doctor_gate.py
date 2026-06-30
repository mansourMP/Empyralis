from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

LOCAL_COMPANION_TARGET = "local_companion"
LOCAL_COMPANION_BLOCKING_FAIL_CHECKS = {
    "auth_policy",
    "runtime_contract",
    "runtime_validation",
}


def _safe_read_json(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return fallback
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw) if raw else {}
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return fallback


def _doctor_report_file() -> Path:
    from server_modules import runtime_config as config

    return config.ORION_DOCTOR_REPORT_FILE


def _normalize_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"pass", "ok"}:
        return "pass"
    if raw in {"warn", "warning"}:
        return "warn"
    return "fail"


def _default_unavailable_report(detail: str = "Hekor could not confirm runtime health right now.") -> Dict[str, Any]:
    return {
        "available": False,
        "generated_at": None,
        "overview": {
            "status": "warn",
            "title": "Doctor unavailable",
            "detail": detail,
        },
        "summary": {"pass": 0, "warn": 1, "fail": 0},
        "checks": [],
        "priority_fixes": [],
    }


def _coerce_report(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return _default_unavailable_report()

    overview_raw = payload.get("overview") if isinstance(payload.get("overview"), dict) else {}
    summary_raw = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    checks_raw = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    fixes_raw = payload.get("priority_fixes") if isinstance(payload.get("priority_fixes"), list) else []

    checks = [item for item in checks_raw if isinstance(item, dict)]
    priority_fixes = [item for item in fixes_raw if isinstance(item, dict)]

    if not overview_raw and not checks and not priority_fixes:
        return _default_unavailable_report()

    return {
        "available": bool(payload.get("available", True)),
        "generated_at": payload.get("generated_at"),
        "overview": {
            "status": _normalize_status(overview_raw.get("status") or "warn"),
            "title": str(overview_raw.get("title") or "").strip() or "Runtime needs attention",
            "detail": str(overview_raw.get("detail") or "").strip()
            or "Review Health before starting sensitive work.",
        },
        "summary": {
            "pass": int(summary_raw.get("pass") or 0),
            "warn": int(summary_raw.get("warn") or 0),
            "fail": int(summary_raw.get("fail") or 0),
        },
        "checks": checks,
        "priority_fixes": priority_fixes,
    }


def _first_check_by_status(report: Dict[str, Any], status: str) -> Optional[Dict[str, Any]]:
    normalized = _normalize_status(status)
    for item in report.get("checks") if isinstance(report.get("checks"), list) else []:
        if not isinstance(item, dict):
            continue
        if _normalize_status(item.get("status")) == normalized:
            return item
    return None


def _first_blocking_local_fail(report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for item in report.get("checks") if isinstance(report.get("checks"), list) else []:
        if not isinstance(item, dict):
            continue
        if _normalize_status(item.get("status")) != "fail":
            continue
        name = str(item.get("name") or "").strip().lower()
        if name in LOCAL_COMPANION_BLOCKING_FAIL_CHECKS:
            return item
    return None


def _resolve_runtime_provider(metadata: Dict[str, Any], provider: Optional[str] = None) -> str:
    candidates = [
        metadata.get("runtime_profile_provider"),
        metadata.get("active_profile_provider"),
        provider,
        metadata.get("provider"),
    ]
    for raw in candidates:
        token = str(raw or "").strip().lower()
        if token:
            return token
    return ""


def _uses_managed_openai(metadata: Dict[str, Any], runtime_provider: str, credential_id: Optional[str] = None) -> bool:
    if runtime_provider != "openai":
        return False

    connection_mode = str(metadata.get("connection_mode") or "").strip().lower()
    if connection_mode in {"byok", "bring_your_own_key", "own_key"}:
        return False
    if connection_mode in {"managed", "managed_openai", "platform", "platform_credits", "hekor_credits", "credits"}:
        return True

    return not str(credential_id or "").strip()


def evaluate_doctor_run_gate(
    report_payload: Any,
    *,
    execution_target: str,
    metadata: Optional[Dict[str, Any]] = None,
    provider: Optional[str] = None,
    credential_id: Optional[str] = None,
) -> Dict[str, Any]:
    metadata = metadata if isinstance(metadata, dict) else {}
    report = _coerce_report(report_payload)
    runtime_provider = _resolve_runtime_provider(metadata, provider)
    uses_managed_openai = _uses_managed_openai(metadata, runtime_provider, credential_id)
    route_label = execution_target.replace("_", " ").strip() or "automatic"

    if not report.get("available"):
        return {
            "status": "warn",
            "blocking": False,
            "title": report["overview"]["title"],
            "detail": report["overview"]["detail"],
            "recommendation": "Open Health and rerun checks if you want a fresh runtime report before starting work.",
            "report_generated_at": report.get("generated_at"),
            "report_summary": report.get("summary"),
        }

    openai_connectivity_warning = None
    for item in report.get("checks", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "").strip() == "openai_connectivity" and _normalize_status(item.get("status")) == "warn":
            openai_connectivity_warning = item
            break

    if openai_connectivity_warning and uses_managed_openai:
        return {
            "status": "fail",
            "blocking": True,
            "title": "OpenAI connection needs attention",
            "detail": str(openai_connectivity_warning.get("detail") or "").strip()
            or "OpenAI credentials are not ready for this run.",
            "recommendation": str(openai_connectivity_warning.get("recommendation") or "").strip()
            or "Open Health and add a direct OpenAI API key or access token before running.",
            "report_generated_at": report.get("generated_at"),
            "report_summary": report.get("summary"),
        }

    first_fail = _first_check_by_status(report, "fail")
    if first_fail:
        if str(execution_target or "").strip().lower() == LOCAL_COMPANION_TARGET:
            blocking_local_fail = _first_blocking_local_fail(report)
            if not blocking_local_fail:
                return {
                    "status": "warn",
                    "blocking": False,
                    "title": "Local runtime has warnings",
                    "detail": (
                        f"{str(first_fail.get('detail') or '').strip() or report['overview']['detail']} "
                        "This does not block local runs, but you should review Health."
                    ).strip(),
                    "recommendation": str(first_fail.get("recommendation") or "").strip()
                    or "Open Health to review the warning before you run.",
                    "report_generated_at": report.get("generated_at"),
                    "report_summary": report.get("summary"),
                }
            return {
                "status": "fail",
                "blocking": True,
                "title": "Local runtime needs attention",
                "detail": str(blocking_local_fail.get("detail") or "").strip() or report["overview"]["detail"],
                "recommendation": str(blocking_local_fail.get("recommendation") or "").strip()
                or "Open Health and fix the runtime before running locally.",
                "report_generated_at": report.get("generated_at"),
                "report_summary": report.get("summary"),
            }
        return {
            "status": "warn",
            "blocking": False,
            "title": "Runtime needs attention",
            "detail": (
                f"{str(first_fail.get('detail') or '').strip() or report['overview']['detail']} "
                f"Hekor can still try {route_label}, but you should review Health first."
            ).strip(),
            "recommendation": str(first_fail.get("recommendation") or "").strip()
            or "Open Health before starting high-trust work.",
            "report_generated_at": report.get("generated_at"),
            "report_summary": report.get("summary"),
        }

    first_warn = _first_check_by_status(report, "warn")
    if first_warn:
        return {
            "status": "warn",
            "blocking": False,
            "title": report["overview"]["title"] or "Runtime has warnings",
            "detail": str(first_warn.get("detail") or "").strip() or report["overview"]["detail"],
            "recommendation": str(first_warn.get("recommendation") or "").strip()
            or "Open Health to review the warning before you run.",
            "report_generated_at": report.get("generated_at"),
            "report_summary": report.get("summary"),
        }

    return {
        "status": "pass",
        "blocking": False,
        "title": report["overview"]["title"] or "Runtime healthy",
        "detail": report["overview"]["detail"] or f"Doctor checks passed for {route_label}.",
        "recommendation": "No action needed.",
        "report_generated_at": report.get("generated_at"),
        "report_summary": report.get("summary"),
    }


def latest_doctor_report() -> Dict[str, Any]:
    payload = _safe_read_json(_doctor_report_file(), {})
    return _coerce_report(payload)


def build_doctor_run_gate_from_snapshot(
    *,
    execution_target: str,
    metadata: Optional[Dict[str, Any]] = None,
    provider: Optional[str] = None,
    credential_id: Optional[str] = None,
) -> Dict[str, Any]:
    return evaluate_doctor_run_gate(
        latest_doctor_report(),
        execution_target=execution_target,
        metadata=metadata,
        provider=provider,
        credential_id=credential_id,
    )


async def build_doctor_run_gate_live(
    *,
    execution_target: str,
    metadata: Optional[Dict[str, Any]] = None,
    provider: Optional[str] = None,
    credential_id: Optional[str] = None,
) -> Dict[str, Any]:
    from server_modules.health_diagnostics import doctor

    report = await doctor()
    return evaluate_doctor_run_gate(
        report,
        execution_target=execution_target,
        metadata=metadata,
        provider=provider,
        credential_id=credential_id,
    )
