from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence
from urllib.parse import urlparse


CLOUD_ENVIRONMENTS = {"prod", "production", "staging"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


class CloudCutoverConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class CloudCutoverConfigReport:
    environment: str
    frontend_origins: tuple[str, ...]
    public_frontend_origin: str
    public_api_url: str
    gateway_api_url: str
    websocket_url: str


def _clean(value: object) -> str:
    return str(value or "").strip()


def deployment_environment(env: Mapping[str, str | None]) -> str:
    return (
        _clean(env.get("EMPYRALIS_DEPLOY_ENV"))
        or _clean(env.get("ORION_ENV"))
        or _clean(env.get("ENV"))
        or _clean(env.get("NODE_ENV"))
        or "development"
    ).lower()


def is_cloud_environment(env: Mapping[str, str | None]) -> bool:
    return deployment_environment(env) in CLOUD_ENVIRONMENTS


def split_csv(value: object) -> tuple[str, ...]:
    return tuple(token.strip() for token in _clean(value).split(",") if token.strip())


def is_loopback_url(value: object) -> bool:
    parsed = urlparse(_clean(value))
    return parsed.hostname in LOOPBACK_HOSTS


def _validate_url(
    *,
    value: str,
    label: str,
    allowed_schemes: Sequence[str],
    require_https: bool,
) -> str:
    token = _clean(value)
    if not token:
        raise CloudCutoverConfigError(f"{label} is required for staging/production.")
    parsed = urlparse(token)
    if not parsed.scheme or not parsed.netloc:
        allowed = ", ".join(allowed_schemes)
        raise CloudCutoverConfigError(f"{label} must be an absolute URL with scheme: {allowed}.")
    if parsed.hostname in LOOPBACK_HOSTS:
        raise CloudCutoverConfigError(f"{label} cannot point at localhost in staging/production.")
    if parsed.scheme not in allowed_schemes:
        allowed = ", ".join(allowed_schemes)
        raise CloudCutoverConfigError(f"{label} must be an absolute URL with scheme: {allowed}.")
    if require_https and parsed.scheme not in {"https", "wss"}:
        raise CloudCutoverConfigError(f"{label} must use HTTPS/WSS in staging/production.")
    return token.rstrip("/")


def resolve_public_frontend_origin(
    env: Mapping[str, str | None],
    *,
    allow_dev_fallback: bool = True,
) -> str:
    candidates = (
        _clean(env.get("EMPYRALIS_PUBLIC_FRONTEND_ORIGIN")),
        _clean(env.get("FRONTEND_PUBLIC_ORIGIN")),
        _clean(env.get("FRONTEND_ORIGINS")).split(",", 1)[0].strip(),
    )
    for candidate in candidates:
        if candidate:
            if is_cloud_environment(env):
                return _validate_url(
                    value=candidate,
                    label="public frontend origin",
                    allowed_schemes=("https",),
                    require_https=True,
                )
            return candidate.rstrip("/")
    if allow_dev_fallback and not is_cloud_environment(env):
        return "http://127.0.0.1:3000"
    raise CloudCutoverConfigError("public frontend origin is required for staging/production.")


def resolve_privacy_policy_url(env: Mapping[str, str | None]) -> str:
    return f"{resolve_public_frontend_origin(env).rstrip('/')}/privacy"


def assert_cloud_cutover_config(env: Mapping[str, str | None]) -> CloudCutoverConfigReport:
    environment = deployment_environment(env)
    frontend_origins = split_csv(env.get("FRONTEND_ORIGINS"))
    public_frontend_origin = resolve_public_frontend_origin(env, allow_dev_fallback=not is_cloud_environment(env))
    public_api_url = (
        _clean(env.get("EMPYRALIS_PUBLIC_API_URL"))
        or _clean(env.get("ORION_PUBLIC_API_URL"))
        or _clean(env.get("EMPYRALIS_API_URL"))
        or _clean(env.get("ORION_API_URL"))
    )
    gateway_api_url = _clean(env.get("EMPYRALIS_GATEWAY_API_URL"))
    websocket_url = _clean(env.get("EMPYRALIS_GATEWAY_WS_URL")) or _clean(env.get("NEXT_PUBLIC_WS_URL"))

    if is_cloud_environment(env):
        if not frontend_origins:
            raise CloudCutoverConfigError("FRONTEND_ORIGINS is required for staging/production.")
        for origin in frontend_origins:
            _validate_url(
                value=origin,
                label="FRONTEND_ORIGINS",
                allowed_schemes=("https",),
                require_https=True,
            )
        public_api_url = _validate_url(
            value=public_api_url,
            label="public API URL",
            allowed_schemes=("https",),
            require_https=True,
        )
        if gateway_api_url:
            gateway_api_url = _validate_url(
                value=gateway_api_url,
                label="gateway API URL",
                allowed_schemes=("https",),
                require_https=True,
            )
        if websocket_url:
            websocket_url = _validate_url(
                value=websocket_url,
                label="gateway websocket URL",
                allowed_schemes=("wss",),
                require_https=True,
            )

    return CloudCutoverConfigReport(
        environment=environment,
        frontend_origins=frontend_origins,
        public_frontend_origin=public_frontend_origin,
        public_api_url=public_api_url.rstrip("/"),
        gateway_api_url=gateway_api_url.rstrip("/"),
        websocket_url=websocket_url.rstrip("/"),
    )
