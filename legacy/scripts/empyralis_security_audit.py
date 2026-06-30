#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class AuditResult:
    check: str
    status: str
    detail: str
    recommendation: str = ""


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _env_any(names: Iterable[str], default: str = "") -> tuple[str, str]:
    for name in names:
        value = _env(name)
        if value:
            return name, value
    return "", default


def _is_production() -> bool:
    token = _env_any(("APP_ENV", "NODE_ENV", "ORION_ENV", "EMPYRALIS_ENV"), "development")[1].lower()
    return token in {"prod", "production", "render"}


def _bool_env(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _split_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]


def _audit_vault() -> AuditResult:
    key = _env("CREDENTIAL_VAULT_KEY")
    key_file = Path(_env("CREDENTIAL_VAULT_KEY_FILE")).expanduser() if _env("CREDENTIAL_VAULT_KEY_FILE") else None
    vault_file = _env("CREDENTIAL_VAULT_FILE")
    has_key_file = bool(key_file and key_file.exists() and key_file.is_file() and key_file.stat().st_size >= 32)
    if len(key) >= 32 or has_key_file:
        return AuditResult(
            "credentials_vault_encryption",
            "PASS",
            "Credential vault key material is configured.",
        )
    status = "FAIL" if _is_production() else "WARN"
    return AuditResult(
        "credentials_vault_encryption",
        status,
        f"No strong CREDENTIAL_VAULT_KEY or CREDENTIAL_VAULT_KEY_FILE detected; vault={vault_file or 'default'}.",
        "Configure a 32+ byte vault key outside source control before public launch.",
    )


def _audit_gateway_auth() -> AuditResult:
    bind_name, bind = _env_any(
        (
            "EMPYRALIS_GATEWAY_BIND",
            "ORION_GATEWAY_BIND",
            "GATEWAY_BIND",
            "GATEWAY_HOST",
        ),
        "loopback",
    )
    auth_name, auth_mode = _env_any(
        (
            "EMPYRALIS_GATEWAY_AUTH_MODE",
            "ORION_GATEWAY_AUTH_MODE",
            "GATEWAY_AUTH_MODE",
        ),
        "token",
    )
    normalized_bind = bind.lower()
    normalized_auth = auth_mode.lower()
    loopback = normalized_bind in {"", "loopback", "localhost", "127.0.0.1", "::1"}
    no_auth = normalized_auth in {"none", "off", "disabled", "0", "false"}
    if no_auth and not loopback:
        return AuditResult(
            "gateway_auth_boundary",
            "FAIL",
            f"{bind_name or 'gateway bind'}={bind!r} with {auth_name or 'gateway auth'}={auth_mode!r}.",
            "Network-reachable gateway endpoints must require token/password auth.",
        )
    if no_auth:
        return AuditResult(
            "gateway_auth_boundary",
            "WARN",
            f"Gateway auth is disabled but bind appears loopback-only: {bind!r}.",
            "Keep this local-dev only; production should still use gateway token auth.",
        )
    return AuditResult(
        "gateway_auth_boundary",
        "PASS",
        f"Gateway auth mode is {auth_mode!r}; bind is {bind!r}.",
    )


def _audit_dm_policies() -> AuditResult:
    policy_names = (
        "TELEGRAM_DM_POLICY",
        "WHATSAPP_DM_POLICY",
        "SIGNAL_DM_POLICY",
        "ORION_TELEGRAM_DM_POLICY",
        "ORION_WHATSAPP_DM_POLICY",
    )
    configured = {name: _env(name).lower() for name in policy_names if _env(name)}
    open_policies = [name for name, value in configured.items() if value == "open"]
    bad_policies = [name for name, value in configured.items() if value not in {"pairing", "allowlist", "disabled", "open"}]
    if open_policies:
        return AuditResult(
            "channel_dm_policy",
            "FAIL",
            f"Open DM policy detected: {', '.join(open_policies)}.",
            "Use pairing or allowlist for personal-agent channels.",
        )
    if bad_policies:
        return AuditResult(
            "channel_dm_policy",
            "WARN",
            f"Unknown DM policy values: {', '.join(bad_policies)}.",
            "Use only pairing, allowlist, disabled, or explicitly audited open mode.",
        )
    if configured:
        return AuditResult(
            "channel_dm_policy",
            "PASS",
            "Configured DM policies avoid open mode.",
        )
    return AuditResult(
        "channel_dm_policy",
        "WARN",
        "No channel DM policy environment variables found.",
        "Verify persisted channel policy uses pairing/allowlist before launch.",
    )


def _audit_multi_user_risk() -> AuditResult:
    auth_required = _env("ORION_AUTH_REQUIRED", "1")
    multi_user = _bool_env("ORION_MULTI_USER", default=_is_production())
    if auth_required == "0" and (_is_production() or multi_user):
        return AuditResult(
            "multi_user_auth_boundary",
            "FAIL",
            "ORION_AUTH_REQUIRED=0 in a production or multi-user context.",
            "Never treat same-origin/local mode as real authentication.",
        )
    if auth_required == "0":
        return AuditResult(
            "multi_user_auth_boundary",
            "WARN",
            "ORION_AUTH_REQUIRED=0 is enabled.",
            "Keep this explicit local-dev mode only.",
        )
    return AuditResult(
        "multi_user_auth_boundary",
        "PASS",
        "Authentication gate is enabled.",
    )


def _audit_provider_keys() -> AuditResult:
    key_names = (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
    )
    present = {name: _env(name) for name in key_names if _env(name)}
    short = [name for name, value in present.items() if len(value) < 20]
    if short:
        return AuditResult(
            "provider_key_shape",
            "FAIL",
            f"Provider key(s) look too short: {', '.join(short)}.",
            "Rotate/reconfigure malformed provider keys.",
        )
    if present:
        return AuditResult(
            "provider_key_shape",
            "PASS",
            f"Provider key(s) configured: {', '.join(sorted(present))}.",
        )
    return AuditResult(
        "provider_key_shape",
        "WARN",
        "No provider keys found in the current process environment.",
        "Expected if using hosted secret storage; otherwise configure provider credentials.",
    )


def _audit_cors() -> AuditResult:
    _, origins_raw = _env_any(("ORION_CORS_ORIGINS", "CORS_ORIGINS", "ALLOWED_ORIGINS"), "")
    origins = _split_list(origins_raw)
    if "*" in origins:
        return AuditResult(
            "cors_origins",
            "FAIL" if _is_production() else "WARN",
            "Wildcard CORS origin is configured.",
            "Use explicit production origins only.",
        )
    if origins:
        return AuditResult(
            "cors_origins",
            "PASS",
            f"CORS origins are explicit: {', '.join(origins[:5])}.",
        )
    return AuditResult(
        "cors_origins",
        "WARN",
        "No explicit CORS origin config found in the current process environment.",
        "Verify runtime_config.py defaults match production domains.",
    )


def _audit_rate_limits() -> AuditResult:
    names = (
        "ORION_SERVICE_RATE_LIMIT_PER_MINUTE",
        "ORION_AUTH_LOGIN_RATE_LIMIT_PER_MINUTE",
        "ORION_AUTHENTICATED_API_RATE_LIMIT_PER_MINUTE",
        "CONTROL_PLANE_RATE_LIMIT_PER_MINUTE",
    )
    values: dict[str, int] = {}
    for name in names:
        raw = _env(name)
        if not raw:
            continue
        try:
            values[name] = int(raw)
        except ValueError:
            return AuditResult(
                "rate_limiting",
                "FAIL",
                f"{name} is not an integer: {raw!r}.",
                "Set rate limits to positive integers.",
            )
    disabled = [name for name, value in values.items() if value <= 0]
    if disabled:
        return AuditResult(
            "rate_limiting",
            "FAIL",
            f"Disabled/non-positive rate limits: {', '.join(disabled)}.",
            "Set positive request limits before launch.",
        )
    if values:
        return AuditResult(
            "rate_limiting",
            "PASS",
            "Rate limit env values are positive.",
        )
    return AuditResult(
        "rate_limiting",
        "WARN",
        "No rate limit environment overrides found.",
        "Confirm code defaults are acceptable for launch traffic.",
    )


CHECKS: tuple[Callable[[], AuditResult], ...] = (
    _audit_vault,
    _audit_gateway_auth,
    _audit_dm_policies,
    _audit_multi_user_risk,
    _audit_provider_keys,
    _audit_cors,
    _audit_rate_limits,
)


def run_audit() -> list[AuditResult]:
    results: list[AuditResult] = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:
            results.append(
                AuditResult(
                    check.__name__.removeprefix("_audit_"),
                    "FAIL",
                    f"Audit check crashed: {exc}",
                    "Fix the audit check before treating this lane as certified.",
                )
            )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Empyralis launch security configuration checks.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()
    results = run_audit()
    if args.json:
        print(json.dumps([asdict(item) for item in results], indent=2))
    else:
        for item in results:
            suffix = f" — {item.recommendation}" if item.recommendation else ""
            print(f"{item.status} {item.check}: {item.detail}{suffix}")
    return 1 if any(item.status == "FAIL" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
