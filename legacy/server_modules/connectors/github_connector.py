from __future__ import annotations

import base64
import calendar
import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


GithubHttpRequest = Callable[..., Dict[str, Any]]

GITHUB_API_BASE = "https://api.github.com"
_TOKEN_REFRESH_SKEW_SECONDS = 60


from server_modules.channel_sdk import _http_json_request


def _env_first(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return ""


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _coerce_epoch(raw: Any) -> Optional[int]:
    if raw in {None, ""}:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _github_headers(token: str) -> Dict[str, str]:
    normalized = str(token or "").strip()
    if not normalized:
        raise RuntimeError("GitHub token is required.")
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {normalized}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Empyralist-GitHub-Connector",
    }


def _ensure_ok(response: Dict[str, Any], *, fallback_error: str) -> Any:
    status = int(response.get("status") or 0)
    if 200 <= status < 300:
        return response.get("json")
    body = response.get("json") if isinstance(response.get("json"), dict) else {}
    detail = str(body.get("message") or response.get("text") or "").strip()
    raise RuntimeError(detail or fallback_error)


def _personal_access_token(credentials: Dict[str, Any]) -> str:
    return str(
        credentials.get("personal_access_token")
        or credentials.get("pat")
        or credentials.get("token")
        or credentials.get("access_token")
        or ""
    ).strip()


def _app_credentials(credentials: Dict[str, Any]) -> Dict[str, str]:
    app_id = str(credentials.get("app_id") or "").strip()
    installation_id = str(credentials.get("installation_id") or "").strip()
    private_key_pem = str(
        credentials.get("private_key_pem")
        or credentials.get("private_key")
        or ""
    ).strip()
    if not app_id or not installation_id or not private_key_pem:
        raise RuntimeError("GitHub App auth requires app_id, installation_id, and private_key_pem.")
    return {
        "app_id": app_id,
        "installation_id": installation_id,
        "private_key_pem": private_key_pem,
    }


def _app_token_expired(credentials: Dict[str, Any]) -> bool:
    expires_at = _coerce_epoch(credentials.get("installation_token_expires_at_epoch"))
    if expires_at is None:
        return True
    return expires_at <= int(time.time()) + _TOKEN_REFRESH_SKEW_SECONDS


def _openssl_sign_rs256(private_key_pem: str, message: bytes) -> bytes:
    key_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".pem") as handle:
            handle.write(private_key_pem)
            handle.flush()
            key_path = handle.name
        proc = subprocess.run(
            ["openssl", "dgst", "-binary", "-sha256", "-sign", key_path],
            input=message,
            capture_output=True,
            check=False,
        )
    finally:
        if key_path:
            try:
                os.remove(key_path)
            except Exception:
                pass
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "GitHub App JWT signing failed.")
    return bytes(proc.stdout)


def build_app_jwt(app_id: str, private_key_pem: str, *, now: Optional[int] = None) -> str:
    issued_at = int(now or time.time()) - 60
    payload = {
        "iat": issued_at,
        "exp": issued_at + 600,
        "iss": str(app_id or "").strip(),
    }
    header_segment = _base64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload_segment = _base64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = _openssl_sign_rs256(private_key_pem, signing_input)
    return f"{header_segment}.{payload_segment}.{_base64url(signature)}"


def _app_headers(jwt_token: str) -> Dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {jwt_token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Empyralist-GitHub-Connector",
    }


def _installation_access_token(
    credentials: Dict[str, Any],
    *,
    http_json_request: Optional[GithubHttpRequest] = None,
) -> str:
    cached = str(credentials.get("installation_access_token") or "").strip()
    if cached and not _app_token_expired(credentials):
        return cached
    app = _app_credentials(credentials)
    request_fn = http_json_request or _http_json_request
    jwt_token = build_app_jwt(app["app_id"], app["private_key_pem"])
    response = request_fn(
        f"{GITHUB_API_BASE}/app/installations/{urlparse.quote(app['installation_id'])}/access_tokens",
        method="POST",
        headers=_app_headers(jwt_token),
        payload={},
    )
    body = _ensure_ok(response, fallback_error="GitHub installation access token request failed.")
    if not isinstance(body, dict):
        raise RuntimeError("GitHub installation access token response was invalid.")
    token = str(body.get("token") or "").strip()
    if not token:
        raise RuntimeError("GitHub installation access token was missing.")
    expires_at = str(body.get("expires_at") or "").strip()
    epoch = _coerce_epoch(credentials.get("installation_token_expires_at_epoch"))
    if expires_at:
        try:
            epoch = int(calendar.timegm(time.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ")))
        except Exception:
            epoch = None
    credentials["installation_access_token"] = token
    if epoch:
        credentials["installation_token_expires_at_epoch"] = epoch
    return token


def _resolve_token(
    credentials: Dict[str, Any],
    *,
    http_json_request: Optional[GithubHttpRequest] = None,
) -> Dict[str, str]:
    pat = _personal_access_token(credentials)
    if pat:
        return {"auth_mode": "pat", "token": pat}
    return {
        "auth_mode": "app",
        "token": _installation_access_token(credentials, http_json_request=http_json_request),
    }


def _request_json(
    path: str,
    credentials: Dict[str, Any],
    *,
    method: str = "GET",
    payload: Optional[Any] = None,
    http_json_request: Optional[GithubHttpRequest] = None,
) -> Any:
    auth = _resolve_token(credentials, http_json_request=http_json_request)
    request_fn = http_json_request or _http_json_request
    response = request_fn(
        f"{GITHUB_API_BASE}{path}",
        method=method,
        headers=_github_headers(auth["token"]),
        payload=payload,
    )
    return _ensure_ok(response, fallback_error=f"GitHub request failed: {path}")


def validate_github_credentials(
    credentials: Dict[str, Any],
    *,
    http_json_request: Optional[GithubHttpRequest] = None,
) -> Dict[str, Any]:
    request_fn = http_json_request or _http_json_request
    pat = _personal_access_token(credentials)
    normalized = dict(credentials)
    if pat:
        profile = _request_json("/user", normalized, http_json_request=request_fn)
        if not isinstance(profile, dict):
            raise RuntimeError("GitHub PAT profile response was invalid.")
        login = str(profile.get("login") or "").strip()
        normalized["auth_mode"] = "pat"
        if login:
            normalized["username"] = login
        return {
            "ok": True,
            "status": 200,
            "message": "GitHub connector is valid.",
            "auth_mode": "pat",
            "username": login or None,
            "profile": {
                "login": login or None,
                "id": profile.get("id"),
                "name": profile.get("name"),
                "type": profile.get("type"),
            },
            "credentials": normalized,
        }

    app = _app_credentials(normalized)
    jwt_token = build_app_jwt(app["app_id"], app["private_key_pem"])
    installation = request_fn(
        f"{GITHUB_API_BASE}/app/installations/{urlparse.quote(app['installation_id'])}",
        headers=_app_headers(jwt_token),
    )
    installation_json = _ensure_ok(installation, fallback_error="GitHub App installation lookup failed.")
    if not isinstance(installation_json, dict):
        raise RuntimeError("GitHub App installation response was invalid.")
    token = _installation_access_token(normalized, http_json_request=request_fn)
    repos = request_fn(
        f"{GITHUB_API_BASE}/installation/repositories?per_page=1",
        headers=_github_headers(token),
    )
    _ensure_ok(repos, fallback_error="GitHub App installation token is invalid.")
    account = installation_json.get("account") if isinstance(installation_json.get("account"), dict) else {}
    login = str(account.get("login") or installation_json.get("account_login") or "").strip()
    normalized["auth_mode"] = "app"
    if login:
        normalized["username"] = login
    normalized.pop("installation_access_token", None)
    normalized.pop("installation_token_expires_at_epoch", None)
    return {
        "ok": True,
        "status": 200,
        "message": "GitHub connector is valid.",
        "auth_mode": "app",
        "username": login or None,
        "profile": {
            "login": login or None,
            "id": account.get("id"),
            "name": account.get("name") or account.get("login"),
            "type": account.get("type"),
        },
        "credentials": normalized,
    }


def list_repos(
    credentials: Dict[str, Any],
    *,
    org: Optional[str] = None,
    limit: int = 50,
    http_json_request: Optional[GithubHttpRequest] = None,
) -> List[Dict[str, Any]]:
    auth = _resolve_token(credentials, http_json_request=http_json_request)
    safe_limit = max(1, min(int(limit or 50), 100))
    if org:
        path = f"/orgs/{urlparse.quote(str(org).strip())}/repos?per_page={safe_limit}"
    elif auth["auth_mode"] == "app":
        path = f"/installation/repositories?per_page={safe_limit}"
    else:
        path = f"/user/repos?per_page={safe_limit}"
    body = _request_json(path, credentials, http_json_request=http_json_request)
    items = body.get("repositories") if isinstance(body, dict) and isinstance(body.get("repositories"), list) else body
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in items[:safe_limit]:
        if not isinstance(item, dict):
            continue
        owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
        out.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "full_name": item.get("full_name"),
                "private": bool(item.get("private")),
                "default_branch": item.get("default_branch"),
                "html_url": item.get("html_url"),
                "owner": str(owner.get("login") or "").strip() or None,
            }
        )
    return out


def get_repo(
    credentials: Dict[str, Any],
    owner: str,
    repo: str,
    *,
    http_json_request: Optional[GithubHttpRequest] = None,
) -> Dict[str, Any]:
    body = _request_json(
        f"/repos/{urlparse.quote(owner)}/{urlparse.quote(repo)}",
        credentials,
        http_json_request=http_json_request,
    )
    return body if isinstance(body, dict) else {}


def list_issues(
    credentials: Dict[str, Any],
    owner: str,
    repo: str,
    *,
    state: str = "open",
    limit: int = 20,
    http_json_request: Optional[GithubHttpRequest] = None,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 20), 100))
    body = _request_json(
        f"/repos/{urlparse.quote(owner)}/{urlparse.quote(repo)}/issues?state={urlparse.quote(str(state or 'open'))}&per_page={safe_limit}",
        credentials,
        http_json_request=http_json_request,
    )
    if not isinstance(body, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in body:
        if not isinstance(item, dict) or item.get("pull_request"):
            continue
        out.append(item)
        if len(out) >= safe_limit:
            break
    return out


def create_issue(
    credentials: Dict[str, Any],
    owner: str,
    repo: str,
    title: str,
    body: str,
    *,
    labels: Optional[List[str]] = None,
    http_json_request: Optional[GithubHttpRequest] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"title": str(title or "").strip(), "body": str(body or "")}
    if labels:
        payload["labels"] = [str(item).strip() for item in labels if str(item).strip()]
    result = _request_json(
        f"/repos/{urlparse.quote(owner)}/{urlparse.quote(repo)}/issues",
        credentials,
        method="POST",
        payload=payload,
        http_json_request=http_json_request,
    )
    return result if isinstance(result, dict) else {}


def comment_on_issue(
    credentials: Dict[str, Any],
    owner: str,
    repo: str,
    issue_number: int,
    body: str,
    *,
    http_json_request: Optional[GithubHttpRequest] = None,
) -> Dict[str, Any]:
    result = _request_json(
        f"/repos/{urlparse.quote(owner)}/{urlparse.quote(repo)}/issues/{int(issue_number)}/comments",
        credentials,
        method="POST",
        payload={"body": str(body or "")},
        http_json_request=http_json_request,
    )
    return result if isinstance(result, dict) else {}


def list_pull_requests(
    credentials: Dict[str, Any],
    owner: str,
    repo: str,
    *,
    state: str = "open",
    limit: int = 20,
    http_json_request: Optional[GithubHttpRequest] = None,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 20), 100))
    body = _request_json(
        f"/repos/{urlparse.quote(owner)}/{urlparse.quote(repo)}/pulls?state={urlparse.quote(str(state or 'open'))}&per_page={safe_limit}",
        credentials,
        http_json_request=http_json_request,
    )
    return [item for item in body[:safe_limit] if isinstance(item, dict)] if isinstance(body, list) else []


def create_pull_request(
    credentials: Dict[str, Any],
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str,
    *,
    http_json_request: Optional[GithubHttpRequest] = None,
) -> Dict[str, Any]:
    result = _request_json(
        f"/repos/{urlparse.quote(owner)}/{urlparse.quote(repo)}/pulls",
        credentials,
        method="POST",
        payload={
            "title": str(title or "").strip(),
            "body": str(body or ""),
            "head": str(head or "").strip(),
            "base": str(base or "").strip(),
        },
        http_json_request=http_json_request,
    )
    return result if isinstance(result, dict) else {}


def get_file_content(
    credentials: Dict[str, Any],
    owner: str,
    repo: str,
    path: str,
    *,
    ref: str = "main",
    http_json_request: Optional[GithubHttpRequest] = None,
) -> Dict[str, Any]:
    body = _request_json(
        f"/repos/{urlparse.quote(owner)}/{urlparse.quote(repo)}/contents/{urlparse.quote(path)}?ref={urlparse.quote(str(ref or 'main'))}",
        credentials,
        http_json_request=http_json_request,
    )
    if not isinstance(body, dict):
        return {}
    encoded = str(body.get("content") or "").strip().replace("\n", "")
    decoded = ""
    if encoded and str(body.get("encoding") or "").strip().lower() == "base64":
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
        except Exception:
            decoded = ""
    return {
        "path": body.get("path"),
        "sha": body.get("sha"),
        "size": body.get("size"),
        "encoding": body.get("encoding"),
        "content": decoded,
        "download_url": body.get("download_url"),
        "html_url": body.get("html_url"),
    }


def create_or_update_file(
    credentials: Dict[str, Any],
    owner: str,
    repo: str,
    path: str,
    message: str,
    content: str,
    *,
    sha: Optional[str] = None,
    branch: Optional[str] = None,
    http_json_request: Optional[GithubHttpRequest] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "message": str(message or "").strip(),
        "content": base64.b64encode(str(content or "").encode("utf-8")).decode("ascii"),
    }
    if sha:
        payload["sha"] = str(sha).strip()
    if branch:
        payload["branch"] = str(branch).strip()
    result = _request_json(
        f"/repos/{urlparse.quote(owner)}/{urlparse.quote(repo)}/contents/{urlparse.quote(path)}",
        credentials,
        method="PUT",
        payload=payload,
        http_json_request=http_json_request,
    )
    return result if isinstance(result, dict) else {}


def list_commits(
    credentials: Dict[str, Any],
    owner: str,
    repo: str,
    *,
    limit: int = 10,
    http_json_request: Optional[GithubHttpRequest] = None,
) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 10), 100))
    body = _request_json(
        f"/repos/{urlparse.quote(owner)}/{urlparse.quote(repo)}/commits?per_page={safe_limit}",
        credentials,
        http_json_request=http_json_request,
    )
    return [item for item in body[:safe_limit] if isinstance(item, dict)] if isinstance(body, list) else []


def request_signature_header(headers: Dict[str, Any]) -> str:
    raw_headers = headers if isinstance(headers, dict) else dict(headers or {})
    return str(raw_headers.get("X-Hub-Signature-256") or raw_headers.get("x-hub-signature-256") or "").strip()


def verify_request_signature(headers: Dict[str, Any], raw_body: bytes, secret: str) -> bool:
    normalized_secret = str(secret or "").strip()
    signature = request_signature_header(headers)
    if not normalized_secret:
        return False
    if not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(normalized_secret.encode("utf-8"), raw_body or b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def parse_inbound_event(payload: Dict[str, Any], *, event_type: str = "", delivery_id: str = "") -> Dict[str, Any]:
    event = str(event_type or payload.get("hook") or "").strip().lower()
    repository = payload.get("repository") if isinstance(payload.get("repository"), dict) else {}
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    action = str(payload.get("action") or "").strip().lower()
    parsed: Dict[str, Any] = {
        "kind": "event",
        "event_type": event or "unknown",
        "delivery_id": str(delivery_id or "").strip() or None,
        "repository": str(repository.get("full_name") or "").strip() or None,
        "owner": (
            str((repository.get("owner") or {}).get("login") or "").strip()
            if isinstance(repository.get("owner"), dict)
            else None
        ),
        "repo": str(repository.get("name") or "").strip() or None,
        "sender": str(sender.get("login") or "").strip() or None,
        "action": action or None,
    }
    if event == "push":
        commits = payload.get("commits") if isinstance(payload.get("commits"), list) else []
        parsed.update(
            {
                "ref": str(payload.get("ref") or "").strip() or None,
                "before": str(payload.get("before") or "").strip() or None,
                "after": str(payload.get("after") or "").strip() or None,
                "commit_count": len(commits),
                "head_commit": (
                    str((payload.get("head_commit") or {}).get("message") or "").strip()
                    if isinstance(payload.get("head_commit"), dict)
                    else None
                ),
            }
        )
    elif event == "pull_request":
        pr = payload.get("pull_request") if isinstance(payload.get("pull_request"), dict) else {}
        parsed.update(
            {
                "pull_request_number": pr.get("number") or payload.get("number"),
                "title": str(pr.get("title") or "").strip() or None,
                "url": str(pr.get("html_url") or "").strip() or None,
                "base_ref": str((pr.get("base") or {}).get("ref") or "").strip() if isinstance(pr.get("base"), dict) else None,
                "head_ref": str((pr.get("head") or {}).get("ref") or "").strip() if isinstance(pr.get("head"), dict) else None,
            }
        )
    elif event == "issues":
        issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
        parsed.update(
            {
                "issue_number": issue.get("number") or payload.get("number"),
                "title": str(issue.get("title") or "").strip() or None,
                "url": str(issue.get("html_url") or "").strip() or None,
            }
        )
    return parsed


def event_matches_connector(
    parsed: Dict[str, Any],
    credentials: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    if not isinstance(parsed, dict) or str(parsed.get("kind") or "").strip() != "event":
        return False
    metadata = metadata if isinstance(metadata, dict) else {}
    owner = str(parsed.get("owner") or "").strip().lower()
    repo = str(parsed.get("repository") or "").strip().lower()
    owner_candidates = (
        credentials.get("owner"),
        credentials.get("username"),
        metadata.get("owner"),
        metadata.get("username"),
        metadata.get("login"),
    )
    configured_owner = next((str(value or "").strip().lower() for value in owner_candidates if str(value or "").strip()), "")
    if owner and configured_owner and owner != configured_owner:
        return False
    repo_candidates = (
        credentials.get("repository"),
        credentials.get("repo_full_name"),
        metadata.get("repository"),
        metadata.get("repo_full_name"),
    )
    configured_repo = next((str(value or "").strip().lower() for value in repo_candidates if str(value or "").strip()), "")
    if repo and configured_repo and repo != configured_repo:
        return False
    return True


def should_trigger_agent_run(parsed: Dict[str, Any], *_args: Any, **_kwargs: Any) -> bool:
    if not isinstance(parsed, dict):
        return False
    if str(parsed.get("kind") or "").strip() != "event":
        return False
    return str(parsed.get("event_type") or "").strip().lower() in {"issues", "pull_request", "push"}


def build_run_goal_from_event(parsed: Dict[str, Any]) -> str:
    event_type = str(parsed.get("event_type") or "").strip().lower()
    repository = str(parsed.get("repository") or "").strip() or "GitHub"
    action = str(parsed.get("action") or "").strip()
    sender = str(parsed.get("sender") or "").strip()
    if event_type == "push":
        commit_count = parsed.get("commit_count")
        head_commit = str(parsed.get("head_commit") or "").strip()
        ref = str(parsed.get("ref") or "").strip()
        return (
            f"GitHub push in {repository}"
            f"{f' on {ref}' if ref else ''}"
            f"{f' by {sender}' if sender else ''}: "
            f"{commit_count or 0} commits. {head_commit}".strip()
        ).strip()
    if event_type == "pull_request":
        number = parsed.get("pull_request_number")
        title = str(parsed.get("title") or "").strip()
        url = str(parsed.get("url") or "").strip()
        return (
            f"GitHub pull request {action or 'updated'} in {repository}"
            f"{f' #{number}' if number else ''}: {title}"
            f"{f' ({url})' if url else ''}"
        ).strip()
    if event_type == "issues":
        number = parsed.get("issue_number")
        title = str(parsed.get("title") or "").strip()
        url = str(parsed.get("url") or "").strip()
        return (
            f"GitHub issue {action or 'updated'} in {repository}"
            f"{f' #{number}' if number else ''}: {title}"
            f"{f' ({url})' if url else ''}"
        ).strip()
    return ""
