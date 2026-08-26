import logging
import os
import secrets
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from server_modules.auth import (
    auth_cookie_refresh_token,
    auth_provider_options,
    browser_auth_session_channel,
    clear_auth_cookies,
    enterprise_status_for_user,
    ensure_public_registration_enabled,
    get_authenticated_user_profile,
    get_authenticated_user_record,
    list_authenticated_user_devices,
    get_current_user,
    limit_email_verification_requests,
    limit_login_requests,
    limit_public_requests,
    limit_refresh_requests,
    login_mobile_beta_user,
    login_external_user,
    login_user,
    load_tenant_enterprise_settings,
    logout_authenticated_session,
    provision_user_account,
    register_user,
    refresh_authenticated_session,
    RefreshTokenSupersededError,
    require_admin_access,
    revoke_authenticated_user_device,
    set_auth_cookies,
    upsert_tenant_enterprise_settings,
    update_authenticated_user_profile,
    validate_csrf,
    verify_external_identity_token,
)
from server_modules.account_shell_service import build_account_shell_payload
from server_modules.channel_pairing_service import (
    create_authenticated_channel_pairing_intent,
    list_authenticated_channel_links,
    revoke_authenticated_channel_link,
)
from server_modules.channel_user_acquisition_service import CHANNEL_ATTRIBUTION_QUERY_PARAM
from server_modules.native_auth_service import exchange_native_auth_code, mint_native_auth_handoff
from server_modules.profile_api import register_profile_routes
from server_modules import control_plane_repository, pilot_invite_service
from server_modules import email_provider_service, email_verification_service
from server_modules.schemas import AuthLoginRequest, AuthRegisterRequest, AuthVerifyEmailRequest


router = APIRouter()
LOGGER = logging.getLogger(__name__)

# Shown to the end user when the email provider can't send right now, no
# matter the underlying cause (missing API key, provider outage, rejected
# request). The real cause -- which may name an env var or a vendor -- goes
# to the server log only; see MAN-293 (a leaked "EMAIL_PROVIDER_API_KEY is
# not configured..." 503 body was reproduced on production).
EMAIL_SEND_UNAVAILABLE_MESSAGE = (
    "We couldn't send a verification email right now. Please try again in a few minutes."
)


def _require_tenant_id(value: Optional[str]) -> str:
    token = str(value or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="tenant_id is required.")
    return token


class AuthMePatchRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    avatar_url: Optional[str] = Field(default=None, max_length=2_048)


class AuthRefreshRequest(BaseModel):
    refresh_token: Optional[str] = Field(default=None, max_length=512)
    channel: Optional[str] = Field(default=None, max_length=80)
    device_id: Optional[str] = Field(default=None, max_length=180)
    device_name: Optional[str] = Field(default=None, max_length=120)
    device_platform: Optional[str] = Field(default=None, max_length=120)
    workspace_id: Optional[str] = Field(default=None, max_length=120)
    session_ttl_seconds: Optional[int] = None


class AuthProviderLoginRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    identity_token: str = Field(min_length=1, max_length=16_384)
    email: Optional[str] = Field(default=None, max_length=320)
    name: Optional[str] = Field(default=None, max_length=120)
    avatar_url: Optional[str] = Field(default=None, max_length=2_048)
    channel: Optional[str] = Field(default=None, max_length=80)
    device_id: Optional[str] = Field(default=None, max_length=180)
    device_name: Optional[str] = Field(default=None, max_length=120)
    device_platform: Optional[str] = Field(default=None, max_length=120)
    workspace_id: Optional[str] = Field(default=None, max_length=120)
    session_ttl_seconds: Optional[int] = None
    acquisition_token: Optional[str] = Field(default=None, max_length=512)


class MobileBetaBootstrapRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=180)
    device_name: Optional[str] = Field(default=None, max_length=120)
    device_platform: Optional[str] = Field(default=None, max_length=120)
    workspace_id: Optional[str] = Field(default=None, max_length=120)
    session_ttl_seconds: Optional[int] = None


class AuthNativeHandoffRequest(BaseModel):
    """The already-authenticated web session (cookie or bearer, via
    get_current_user) asks for a single-use code to hand off to a native
    app. See native_auth_service.py's own header comment for the full
    design; `native` selects a HARDCODED server-side redirect target, never
    a caller-supplied URL."""

    native: str = Field(min_length=1, max_length=32)
    code_challenge: str = Field(min_length=1, max_length=256)
    code_challenge_method: Optional[str] = Field(default="S256", max_length=16)
    state: Optional[str] = Field(default=None, max_length=512)


class AuthNativeExchangeRequest(BaseModel):
    """No user identity of any kind is accepted here on purpose -- the code
    alone (bound server-side to a user_id at mint time) decides who this
    session is for. device_* mirror AuthLoginRequest's own fields, since
    they are the calling device's own metadata, not something carried over
    from the web session that minted the code."""

    code: str = Field(min_length=1, max_length=512)
    code_verifier: str = Field(min_length=1, max_length=256)
    device_id: Optional[str] = Field(default=None, max_length=180)
    device_name: Optional[str] = Field(default=None, max_length=120)
    device_platform: Optional[str] = Field(default=None, max_length=120)
    workspace_id: Optional[str] = Field(default=None, max_length=120)
    session_ttl_seconds: Optional[int] = None


class ChannelPairingIntentCreateRequest(BaseModel):
    provider: str
    workspace_id: Optional[str] = None
    scopes: Optional[list[str]] = None
    ttl_seconds: Optional[int] = None
    allow_relink: Optional[bool] = None
    metadata: Optional[dict[str, Any]] = None


class ChannelLinkRevokeRequest(BaseModel):
    confirm: bool = False
    reason: Optional[str] = None


class EnterpriseSsoConfigPatchRequest(BaseModel):
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    issuer_url: Optional[str] = None
    metadata_url: Optional[str] = None
    client_id: Optional[str] = None
    audience: Optional[str] = None
    domains: Optional[list[str]] = None
    scopes: Optional[list[str]] = None


class EnterpriseMfaConfigPatchRequest(BaseModel):
    required: Optional[bool] = None
    methods: Optional[list[str]] = None
    grace_period_hours: Optional[int] = None


class EnterpriseScimConfigPatchRequest(BaseModel):
    enabled: Optional[bool] = None
    base_url: Optional[str] = None
    provisioning_mode: Optional[str] = None
    last_token_rotation_at: Optional[int] = None


class EnterpriseConfigPatchRequest(BaseModel):
    tenant_id: Optional[str] = None
    sso: Optional[EnterpriseSsoConfigPatchRequest] = None
    mfa: Optional[EnterpriseMfaConfigPatchRequest] = None
    scim: Optional[EnterpriseScimConfigPatchRequest] = None


class AdminProvisionUserRequest(BaseModel):
    email: str
    name: Optional[str] = None
    tenant_id: Optional[str] = None
    workspace_roles: Optional[dict[str, str]] = None
    provisioning_source: Optional[str] = None
    external_id: Optional[str] = None
    auth_provider: Optional[str] = None
    sso_subject: Optional[str] = None


def _sanitize_browser_auth_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean_payload = dict(payload or {})
    clean_payload.pop("token", None)
    clean_payload.pop("session_recovery", None)
    return clean_payload


def _resolved_acquisition_token(request: Optional[Request], token: Optional[str]) -> Optional[str]:
    explicit = str(token or "").strip()
    if explicit:
        return explicit
    if request is None:
        return None
    query_value = str(request.query_params.get(CHANNEL_ATTRIBUTION_QUERY_PARAM) or "").strip()
    if query_value:
        return query_value
    header_value = str(request.headers.get("x-channel-attribution") or "").strip()
    return header_value or None


@router.post("/auth/login", dependencies=[Depends(limit_login_requests)])
async def login(body: AuthLoginRequest, request: Request, response: Response):
    payload = login_user(
        body.email,
        body.password,
        acquisition_token=_resolved_acquisition_token(request, body.acquisition_token),
        channel=body.channel,
        device_id=body.device_id,
        device_name=body.device_name,
        device_platform=body.device_platform,
        workspace_id=body.workspace_id,
        session_ttl_seconds=body.session_ttl_seconds,
    )
    if browser_auth_session_channel(body.channel):
        set_auth_cookies(response, payload, request=request, channel=body.channel)
        return _sanitize_browser_auth_payload(payload)
    return payload


@router.post("/auth/provider-login", dependencies=[Depends(limit_login_requests)])
async def provider_login(body: AuthProviderLoginRequest, request: Request, response: Response):
    verified = verify_external_identity_token(
        body.provider,
        id_token=body.identity_token,
        fallback_email=body.email,
        fallback_name=body.name,
        fallback_avatar_url=body.avatar_url,
    )
    payload = login_external_user(
        provider=str(verified.get("provider") or body.provider or "").strip(),
        subject=str(verified.get("subject") or "").strip(),
        email=str(verified.get("email") or "").strip() or None,
        name=str(verified.get("name") or "").strip() or None,
        avatar_url=str(verified.get("avatar_url") or "").strip() or None,
        acquisition_token=_resolved_acquisition_token(request, body.acquisition_token),
        channel=body.channel or "mobile",
        device_id=body.device_id,
        device_name=body.device_name,
        device_platform=body.device_platform,
        workspace_id=body.workspace_id,
        session_ttl_seconds=body.session_ttl_seconds,
    )
    if browser_auth_session_channel(body.channel):
        set_auth_cookies(response, payload, request=request, channel=body.channel)
        return _sanitize_browser_auth_payload(payload)
    return payload


@router.post("/auth/mobile-beta-bootstrap", dependencies=[Depends(limit_login_requests)])
async def mobile_beta_bootstrap(body: MobileBetaBootstrapRequest):
    return login_mobile_beta_user(
        device_id=body.device_id,
        device_name=body.device_name,
        device_platform=body.device_platform,
        workspace_id=body.workspace_id,
        session_ttl_seconds=body.session_ttl_seconds,
    )


@router.post("/auth/native/handoff")
async def native_handoff(
    body: AuthNativeHandoffRequest,
    current_user: dict = Depends(get_current_user),
):
    # Authenticated by the caller's own already-live web session (cookie or
    # bearer) -- this never logs anyone in, it only mints a short-lived
    # ticket naming whoever is already signed in here. No `limit_login_
    # requests` dependency: get_current_user already applies this session's
    # own per-user rate limit, and unlike /auth/login there is no
    # unauthenticated brute-force surface to add a second IP-keyed limiter
    # for.
    return mint_native_auth_handoff(
        current_user,
        native=body.native,
        code_challenge=body.code_challenge,
        code_challenge_method=body.code_challenge_method,
        state=body.state,
    )


@router.post("/auth/native/exchange", dependencies=[Depends(limit_login_requests)])
async def native_exchange(body: AuthNativeExchangeRequest):
    # Deliberately unauthenticated -- the code + code_verifier pair IS the
    # credential, exactly like /auth/login's email + password pair is. Rate
    # limited the same way for the same reason: no session exists yet to
    # rate-limit by.
    return exchange_native_auth_code(
        code=body.code,
        code_verifier=body.code_verifier,
        device_id=body.device_id,
        device_name=body.device_name,
        device_platform=body.device_platform,
        workspace_id=body.workspace_id,
        session_ttl_seconds=body.session_ttl_seconds,
    )


async def _validate_and_apply_pilot_invite(code: Optional[str]) -> Optional[str]:
    """Validate pilot invite code when invite-only mode is active. Returns plan_id or None."""
    if not pilot_invite_service.pilot_signup_requires_invite():
        return None
    clean_code = str(code or "").strip()
    if not clean_code:
        raise HTTPException(status_code=403, detail="A valid pilot invite code is required to register.")
    result = await pilot_invite_service.claim_pilot_invite_code(clean_code)
    return str(result.get("plan_id") or "pilot").strip()


def _validate_platform_invite_code(code: Optional[str]) -> None:
    """EMPYRALIS_INVITE_CODE gate: set -> signup requires this exact code; unset ->
    open signup (no-op). A single static shared secret, deliberately simpler than
    the DB-backed pilot_invite system above — this is a blunt "not open yet" gate,
    not a per-use/per-plan invite mechanism. 403, not 404: auth-client.ts's
    authFailureMessage() hardcodes a generic override for 404 that would swallow
    the honest "invite-only" copy; 403 passes the detail text through."""
    required_code = str(os.environ.get("EMPYRALIS_INVITE_CODE") or "").strip()
    if not required_code:
        return
    # Constant-time: this is a shared secret gating signup, the same
    # category of comparison every other secret check in auth.py uses
    # secrets.compare_digest for. A plain `!=` leaks match/mismatch timing.
    #
    # ENCODED TO BYTES, and that is not stylistic. compare_digest's str form
    # accepts ASCII ONLY and raises TypeError on anything else — and `code`
    # here is raw user input from the signup form. Comparing it as a str
    # turns "someone typed an invite code containing an accent, a Cyrillic
    # letter or an emoji" into an unhandled 500 instead of the clean 403
    # this function exists to return. The bytes form has no such limit.
    if not secrets.compare_digest(
        str(code or "").strip().encode("utf-8"),
        required_code.encode("utf-8"),
    ):
        raise HTTPException(status_code=403, detail="Empyralis is invite-only right now.")


@router.post("/auth/register", dependencies=[Depends(limit_public_requests), Depends(ensure_public_registration_enabled)])
async def register(body: AuthRegisterRequest, request: Request, response: Response):
    _validate_platform_invite_code(body.invite_code)
    pilot_plan_id = await _validate_and_apply_pilot_invite(body.pilot_invite_code)
    payload = register_user(
        body.email,
        body.password,
        name=body.name,
        acquisition_token=_resolved_acquisition_token(request, body.acquisition_token),
        channel=body.channel,
        device_id=body.device_id,
        device_name=body.device_name,
        device_platform=body.device_platform,
        workspace_id=body.workspace_id,
        session_ttl_seconds=body.session_ttl_seconds,
    )
    if pilot_plan_id:
        workspace_access = payload.get("workspace_access") or {}
        for ws_id in list(workspace_access.keys()):
            try:
                await control_plane_repository.update_workspace_billing_plan(
                    workspace_id=ws_id,
                    plan_id=pilot_plan_id,
                )
            except Exception:
                pass
    if browser_auth_session_channel(body.channel):
        set_auth_cookies(response, payload, request=request, channel=body.channel)
        return _sanitize_browser_auth_payload(payload)
    return payload


async def signup(
    body: AuthRegisterRequest,
    request: Optional[Request] = None,
    response: Optional[Response] = None,
):
    if request is None and response is None:
        return await register(body)
    if request is None:
        request = Request(
            {
                "type": "http",
                "headers": [],
                "method": "POST",
                "path": "/auth/signup",
                "query_string": b"",
            }
        )
    if response is None:
        response = Response()
    return await register(body, request, response)


@router.post("/auth/signup", dependencies=[Depends(limit_public_requests), Depends(ensure_public_registration_enabled)])
async def signup_route(body: AuthRegisterRequest, request: Request, response: Response):
    return await signup(body, request, response)


@router.get("/auth/providers")
async def auth_providers():
    return auth_provider_options()


@router.get("/auth/me")
async def auth_me(current_user=Depends(get_current_user)):
    return get_authenticated_user_profile(current_user)


@router.get("/auth/account-shell")
async def auth_account_shell(current_user=Depends(get_current_user)):
    return await build_account_shell_payload(current_user)


@router.get("/auth/status")
async def auth_status(current_user=Depends(get_current_user)):
    profile = get_authenticated_user_profile(current_user)
    user = profile.get("user") if isinstance(profile, dict) else None
    return {"authenticated": True, "user": user}


def _authenticated_user_id_and_email(current_user: dict) -> tuple[str, str]:
    user = get_authenticated_user_record(current_user)
    user_id = str(user.get("id") or "").strip()
    email = str(user.get("email") or "").strip().lower()
    if not user_id or not email:
        raise HTTPException(status_code=404, detail="User not found.")
    return user_id, email


@router.get("/auth/verify-email/status")
async def auth_verify_email_status(current_user=Depends(get_current_user)):
    user_id, _email = _authenticated_user_id_and_email(current_user)
    status = await email_verification_service.verification_status(user_id)
    return {"ok": True, "status": status, "email_verified": status in {"verified", "none"}}


@router.post("/auth/verify-email", dependencies=[Depends(limit_email_verification_requests)])
async def auth_verify_email(
    body: AuthVerifyEmailRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    validate_csrf(request)
    user_id, _email = _authenticated_user_id_and_email(current_user)
    await email_verification_service.verify_code(user_id=user_id, code=body.code)
    return {"ok": True, "email_verified": True}


@router.post("/auth/verify-email/resend", dependencies=[Depends(limit_email_verification_requests)])
async def auth_resend_verify_email(request: Request, current_user=Depends(get_current_user)):
    validate_csrf(request)
    user_id, email = _authenticated_user_id_and_email(current_user)
    try:
        await email_verification_service.resend_verification(user_id=user_id, email=email)
    except email_provider_service.EmailProviderUnavailable as exc:
        # str(exc) names the missing env var and the vendor (Resend) -- that
        # belongs in the operator's log, never in a response an end user
        # can read (MAN-293).
        LOGGER.error(
            "verify_email_resend_provider_unavailable: user_id=%s: %s",
            user_id,
            exc,
        )
        raise HTTPException(status_code=503, detail=EMAIL_SEND_UNAVAILABLE_MESSAGE) from exc
    except email_provider_service.EmailSendFailed as exc:
        LOGGER.error(
            "verify_email_resend_send_failed: user_id=%s: %s",
            user_id,
            exc,
        )
        raise HTTPException(status_code=502, detail=EMAIL_SEND_UNAVAILABLE_MESSAGE) from exc
    return {"ok": True, "sent": True}


@router.post("/auth/refresh", dependencies=[Depends(limit_refresh_requests)])
async def refresh_session(body: AuthRefreshRequest, request: Request, response: Response):
    requested_channel = body.channel
    browser_session_request = False
    if not requested_channel and auth_cookie_refresh_token(request):
        requested_channel = "web"
        browser_session_request = True
    elif requested_channel is not None:
        browser_session_request = browser_auth_session_channel(requested_channel)
    if browser_session_request:
        # A stale/expired session cookie must not block re-establishing a
        # session — treat a dead credential as absent on this re-auth path.
        validate_csrf(request, allow_expired_session=True)
    refresh_token = str(
        body.refresh_token
        or (auth_cookie_refresh_token(request) if browser_session_request else "")
        or ""
    ).strip()
    if not refresh_token:
        raise HTTPException(status_code=400, detail="refresh_token is required.")
    try:
        payload = refresh_authenticated_session(
            refresh_token,
            device_id=body.device_id,
            device_name=body.device_name,
            device_platform=body.device_platform,
            workspace_id=body.workspace_id,
            session_ttl_seconds=body.session_ttl_seconds,
        )
    except RefreshTokenSupersededError as exc:
        # This exact request lost a race against a concurrent, legitimate
        # refresh on the same session — do NOT clear cookies: the browser's
        # current ones may already be the fresh pair the winner just set.
        # Fail only this one request; the caller's next request (or the
        # winner's own response, already in flight) carries the good
        # session forward.
        response.status_code = 401
        return {"detail": str(exc)}
    except HTTPException as exc:
        if browser_session_request and exc.status_code in {401, 403}:
            clear_auth_cookies(response, request=request)
            response.status_code = exc.status_code
            return {"detail": exc.detail}
        raise
    response_channel = requested_channel or str(
        ((payload.get("auth_session") or {}).get("channel") if isinstance(payload, dict) else "") or ""
    ).strip()
    if browser_session_request or (response_channel and browser_auth_session_channel(response_channel)):
        set_auth_cookies(response, payload, request=request, channel=response_channel)
        return _sanitize_browser_auth_payload(payload)
    return payload


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    # Logout is the guaranteed escape hatch from a stuck session — it must
    # never itself 403 on the CSRF check it exists to help a user recover
    # from. No validate_csrf call here at all: even a live, valid session
    # cookie must not block logout, since a forged cross-site logout only
    # logs the victim out (not a meaningful attack), and a stale one with a
    # missing/mismatched CSRF cookie is exactly the stuck state this route
    # must always be able to clear.
    result: dict[str, Any] = {"ok": True, "session_revoked": False}
    try:
        current_user = get_current_user(
            request,
            authorization=request.headers.get("authorization"),
            x_api_key=request.headers.get("x-api-key"),
        )
        result = logout_authenticated_session(current_user)
        if isinstance(result, dict):
            result.setdefault("session_revoked", True)
    except HTTPException:
        # Logout must be able to clear broken/stale browser sessions. If session
        # resolution fails, still remove cookies and let the user sign in again.
        result = {"ok": True, "session_revoked": False}
    clear_auth_cookies(response, request=request)
    return result


@router.get("/auth/devices")
async def auth_devices(current_user=Depends(get_current_user)):
    return list_authenticated_user_devices(current_user)


@router.delete("/auth/devices/{device_id}")
async def auth_revoke_device(device_id: str, request: Request, current_user=Depends(get_current_user)):
    validate_csrf(request)
    return revoke_authenticated_user_device(current_user, device_id)


@router.post("/auth/channel-pairing/intents")
async def create_channel_pairing_intent(
    body: ChannelPairingIntentCreateRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    validate_csrf(request)
    return create_authenticated_channel_pairing_intent(
        current_user,
        provider=body.provider,
        workspace_id=body.workspace_id,
        scopes=body.scopes,
        ttl_seconds=body.ttl_seconds,
        allow_relink=bool(body.allow_relink),
        metadata=body.metadata,
    )


@router.get("/auth/channel-pairing/links")
async def auth_channel_links(
    provider: Optional[str] = None,
    workspace_id: Optional[str] = None,
    include_revoked: bool = False,
    current_user=Depends(get_current_user),
):
    return list_authenticated_channel_links(
        current_user,
        provider=provider,
        workspace_id=workspace_id,
        include_revoked=include_revoked,
    )


@router.post("/auth/channel-pairing/links/{link_id}/revoke")
async def auth_revoke_channel_link(
    link_id: str,
    body: ChannelLinkRevokeRequest,
    request: Request,
    current_user=Depends(get_current_user),
):
    validate_csrf(request)
    return revoke_authenticated_channel_link(
        current_user,
        link_id=link_id,
        confirm=body.confirm,
        reason=body.reason,
    )


@router.get("/auth/enterprise/status")
async def auth_enterprise_status(current_user=Depends(get_current_user)):
    return enterprise_status_for_user(current_user)


@router.patch("/auth/me")
async def patch_auth_me(body: AuthMePatchRequest, request: Request, current_user=Depends(get_current_user)):
    validate_csrf(request)
    return update_authenticated_user_profile(current_user, name=body.name, avatar_url=body.avatar_url)


@router.get("/auth/admin/enterprise-config")
async def get_enterprise_config(
    tenant_id: Optional[str] = None,
    current_user=Depends(require_admin_access),
):
    resolved_tenant_id = _require_tenant_id(tenant_id)
    return {
        "ok": True,
        "tenant_id": resolved_tenant_id,
        "config": load_tenant_enterprise_settings(resolved_tenant_id),
    }


@router.patch("/auth/admin/enterprise-config")
async def patch_enterprise_config(
    body: EnterpriseConfigPatchRequest,
    request: Request,
    current_user=Depends(require_admin_access),
):
    validate_csrf(request)
    resolved_tenant_id = _require_tenant_id(body.tenant_id)
    config = upsert_tenant_enterprise_settings(
        resolved_tenant_id,
        sso=body.sso.model_dump(exclude_none=True) if body.sso is not None else None,
        mfa=body.mfa.model_dump(exclude_none=True) if body.mfa is not None else None,
        scim=body.scim.model_dump(exclude_none=True) if body.scim is not None else None,
    )
    return {"ok": True, "tenant_id": resolved_tenant_id, "config": config}


@router.post("/auth/admin/provision/users")
async def admin_provision_user(
    body: AdminProvisionUserRequest,
    request: Request,
    current_user=Depends(require_admin_access),
):
    validate_csrf(request)
    return provision_user_account(
        email=body.email,
        name=body.name,
        tenant_id=body.tenant_id,
        workspace_roles=body.workspace_roles,
        provisioning_source=body.provisioning_source or "admin_api",
        external_id=body.external_id,
        auth_provider=body.auth_provider,
        sso_subject=body.sso_subject,
    )


register_profile_routes(router)
