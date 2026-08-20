"""Deep links: the ONE function that turns an object into a URL a person can tap.

WHY THIS EXISTS. Chat left the platform (CLAUDE.md, founder 2026-08-20 --
"messaging would never be done inside this platform... go to Telegram and
speak with the agent inside that channel"). So the only way an agent can hand
someone the thing it just did is a URL in the channel message: "I created
GEN-12 for you" has to be tappable, or the work it names is unreachable from
where the conversation actually happens.

ONE BUILDER, and it is Python-side only. The frontend never needs an absolute
URL -- it builds relative hrefs off `projectHref` and always has -- so there is
no second copy to drift against inside the app. What DOES cross a language
boundary is the ROUTE SHAPE: these templates have to keep naming a real
Next.js route. That is asserted against the real
frontend/app/(account)/w/[workspaceId]/... directory tree in
tests/test_deep_link_service.py, so the expected set and the actual set come
from different places (CLAUDE.md: "a check that derives its own expectations
from the thing it checks is blind").

THE ORIGIN IS NOT RESOLVED HERE, ON PURPOSE. It comes from
cloud_cutover_config.resolve_public_frontend_origin -- the same resolution
workspace_invite_email_service already uses for the /join/{token} link that
demonstrably works in production. A fourth env-var list (webhook_base_url's
ORION_TELEGRAM_AUTOPILOT_PUBLIC_BASE_URL / EMPYRALIS_PUBLIC_BASE_URL /
PUBLIC_BASE_URL is a THIRD, and connectors_actions has a fourth) is exactly
the "channel list copied into a third place" defect this codebase already
records once.

`allow_dev_fallback=False`, and that is the load-bearing argument. The shared
resolver's dev fallback is `http://127.0.0.1:3000`, which is correct for a
developer's browser and a LIE in a Telegram message -- a link nobody who
receives it can open. So a deployment that has not declared where the app
lives gets NO LINK, never a broken one. Every builder here returns "" for
that case and for any missing id, and every caller must treat "" as "say
nothing about a link" rather than interpolating it.

Measured, not assumed (2026-08-20, secret_redaction_service.redact_text --
the guard every visible reply and every persisted assistant turn crosses):

    https://empyralis.ai/w/ws_.../projects/proj_.../tasks/task_69d6...
        -> survives intact (_URLISH_TOKEN_PATTERN rescues it: the host
           carries a dot, so the whole run is recognised as URL-ish)
    http://localhost:3000/w/...  -> 'http://localhost:[redacted-secret]'
           a dotless host is NOT URL-ish, so the high-entropy sweep eats the
           entire path. One more reason the loopback fallback must never
           reach a channel; there is a test pinning both halves.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional
from urllib.parse import quote

from server_modules.cloud_cutover_config import (
    CloudCutoverConfigError,
    resolve_public_frontend_origin,
)


# The route shape, as a template rather than an f-string at each call site, so
# the drift test has something to assert against and there is exactly one
# place to change if a route ever moves.
WORKSPACE_PATH_TEMPLATE = "/w/{workspace_id}"
PROJECT_PATH_TEMPLATE = "/w/{workspace_id}/projects/{project_id}"
TASK_PATH_TEMPLATE = "/w/{workspace_id}/projects/{project_id}/tasks/{task_id}"
DOCUMENT_PATH_TEMPLATE = "/w/{workspace_id}/projects/{project_id}/documents/{document_id}"


def _segment(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    return quote(token, safe="")


def resolve_app_origin(env: Optional[Mapping[str, str | None]] = None) -> str:
    """The public origin the app is served from, or "" when none is declared.

    Never raises and never guesses. A CloudCutoverConfigError here means one
    of exactly two things -- a cloud deployment with no configured origin, or
    any deployment with none and the dev fallback refused -- and both mean the
    same thing to a caller: there is no address to hand somebody.
    """
    try:
        origin = resolve_public_frontend_origin(
            env if env is not None else os.environ,
            allow_dev_fallback=False,
        )
    except CloudCutoverConfigError:
        return ""
    return str(origin or "").strip().rstrip("/")


def _build(template: str, *, env: Optional[Mapping[str, str | None]], **parts: Any) -> str:
    origin = resolve_app_origin(env)
    if not origin:
        return ""
    encoded = {key: _segment(value) for key, value in parts.items()}
    if not all(encoded.values()):
        # A URL missing any id would resolve to a list page (or a 404) while
        # LOOKING like a link to the thing named beside it -- the same class
        # of lie as reporting failure on success. Say nothing instead.
        return ""
    return f"{origin}{template.format(**encoded)}"


def build_project_url(
    *, workspace_id: Any, project_id: Any, env: Optional[Mapping[str, str | None]] = None,
) -> str:
    return _build(PROJECT_PATH_TEMPLATE, env=env, workspace_id=workspace_id, project_id=project_id)


def build_task_url(
    *, workspace_id: Any, project_id: Any, task_id: Any, env: Optional[Mapping[str, str | None]] = None,
) -> str:
    return _build(
        TASK_PATH_TEMPLATE,
        env=env,
        workspace_id=workspace_id,
        project_id=project_id,
        task_id=task_id,
    )


def build_document_url(
    *, workspace_id: Any, project_id: Any, document_id: Any, env: Optional[Mapping[str, str | None]] = None,
) -> str:
    return _build(
        DOCUMENT_PATH_TEMPLATE,
        env=env,
        workspace_id=workspace_id,
        project_id=project_id,
        document_id=document_id,
    )


def task_display_id(task: Mapping[str, Any]) -> str:
    """`GEN-12` when the project sequence has one, "" when it does not.

    Deliberately a cross-language mirror of frontend/lib/workspace/fleet/
    task-status.tsx's `taskDisplayId`, MINUS its hex-slice fallback. That
    fallback exists so a table cell is never blank; here the value is going
    into a sentence an agent writes to a person, and "task 69D656" is a
    fragment of a uuid dressed up as an identifier. Empty means "you have no
    name for this one", and the caller says nothing rather than something
    meaningless.
    """
    number = task.get("number")
    key = str(task.get("project_task_key") or "").strip()
    if number is None or not key:
        return ""
    try:
        return f"{key}-{int(number)}"
    except (TypeError, ValueError):
        return ""


def annotate_task(
    task: Optional[Dict[str, Any]],
    *,
    workspace_id: Any,
    env: Optional[Mapping[str, str | None]] = None,
) -> Optional[Dict[str, Any]]:
    """A shallow COPY of the task carrying `url` and `display_id` when real.

    A copy, not a mutation: these rows come straight out of the repository and
    a caller further up may hold the same object. Absent keys rather than
    null/empty ones, so a model reading the tool result cannot interpolate an
    empty string into a sentence -- the field either names something or is not
    there at all.
    """
    if not isinstance(task, dict):
        return task
    enriched = dict(task)
    url = build_task_url(
        workspace_id=workspace_id,
        project_id=task.get("project_id"),
        task_id=task.get("id"),
        env=env,
    )
    if url:
        enriched["url"] = url
    display_id = task_display_id(task)
    if display_id:
        enriched["display_id"] = display_id
    return enriched


def annotate_document(
    document: Optional[Dict[str, Any]],
    *,
    workspace_id: Any,
    env: Optional[Mapping[str, str | None]] = None,
) -> Optional[Dict[str, Any]]:
    """A shallow COPY of the document carrying `url` when one is real."""
    if not isinstance(document, dict):
        return document
    enriched = dict(document)
    url = build_document_url(
        workspace_id=workspace_id,
        project_id=document.get("project_id"),
        document_id=document.get("id"),
        env=env,
    )
    if url:
        enriched["url"] = url
    return enriched
