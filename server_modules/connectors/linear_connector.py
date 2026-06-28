from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional
from urllib import error as urlerror
from urllib import request as urlrequest


LinearHttpRequest = Callable[..., Dict[str, Any]]

LINEAR_API_URL = "https://api.linear.app/graphql"


from server_modules.channel_sdk import _http_json_request


def _token(credentials: Dict[str, Any]) -> str:
    token = str(
        credentials.get("api_key")
        or credentials.get("personal_api_key")
        or credentials.get("access_token")
        or credentials.get("oauth_access_token")
        or credentials.get("token")
        or ""
    ).strip()
    if not token:
        raise RuntimeError("Linear API token is required.")
    return token


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Empyralist-Linear-Connector",
    }


def _graphql(
    credentials: Dict[str, Any],
    query: str,
    variables: Optional[Dict[str, Any]] = None,
    *,
    http_json_request: Optional[LinearHttpRequest] = None,
) -> Dict[str, Any]:
    request_fn = http_json_request or _http_json_request
    response = request_fn(
        LINEAR_API_URL,
        method="POST",
        headers=_headers(_token(credentials)),
        payload={"query": query, "variables": variables or {}},
    )
    status = int(response.get("status") or 0)
    body = response.get("json") if isinstance(response.get("json"), dict) else {}
    if status < 200 or status >= 300:
        detail = str(body.get("message") or response.get("text") or "").strip()
        raise RuntimeError(detail or "Linear request failed.")
    errors = body.get("errors") if isinstance(body.get("errors"), list) else []
    if errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        detail = str(first.get("message") or "").strip()
        raise RuntimeError(detail or "Linear GraphQL request failed.")
    data = body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Linear GraphQL response was invalid.")
    return data


def validate_linear_credentials(
    credentials: Dict[str, Any],
    *,
    http_json_request: Optional[LinearHttpRequest] = None,
) -> Dict[str, Any]:
    data = _graphql(
        credentials,
        """
        query ViewerProfile {
          viewer {
            id
            name
            displayName
            email
            organization {
              id
              name
            }
          }
        }
        """,
        http_json_request=http_json_request,
    )
    viewer = data.get("viewer") if isinstance(data.get("viewer"), dict) else {}
    organization = viewer.get("organization") if isinstance(viewer.get("organization"), dict) else {}
    organization_name = str(organization.get("name") or "").strip()
    organization_id = str(organization.get("id") or "").strip()
    username = str(viewer.get("displayName") or viewer.get("name") or viewer.get("email") or "").strip()
    auth_mode = "oauth" if str(credentials.get("access_token") or credentials.get("oauth_access_token") or "").strip() else "api_key"
    normalized = dict(credentials)
    normalized["auth_mode"] = auth_mode
    if organization_name:
        normalized["organization_name"] = organization_name
    if organization_id:
        normalized["organization_id"] = organization_id
    if username:
        normalized["username"] = username
    return {
        "ok": True,
        "status": 200,
        "message": "Linear connector is valid.",
        "auth_mode": auth_mode,
        "organization_name": organization_name or None,
        "organization_id": organization_id or None,
        "username": username or None,
        "profile": {
            "name": username or None,
            "organization_name": organization_name or None,
            "organization_id": organization_id or None,
            "viewer_id": str(viewer.get("id") or "").strip() or None,
        },
        "credentials": normalized,
    }


def list_teams(
    credentials: Dict[str, Any],
    *,
    http_json_request: Optional[LinearHttpRequest] = None,
) -> List[Dict[str, Any]]:
    data = _graphql(
        credentials,
        """
        query Teams {
          teams {
            nodes {
              id
              key
              name
            }
          }
        }
        """,
        http_json_request=http_json_request,
    )
    teams = data.get("teams") if isinstance(data.get("teams"), dict) else {}
    nodes = teams.get("nodes") if isinstance(teams.get("nodes"), list) else []
    return [item for item in nodes if isinstance(item, dict)]


def list_issues(
    credentials: Dict[str, Any],
    team_id: str,
    *,
    state: Optional[str] = None,
    assignee: Optional[str] = None,
    limit: int = 20,
    http_json_request: Optional[LinearHttpRequest] = None,
) -> List[Dict[str, Any]]:
    filter_payload: Dict[str, Any] = {"team": {"id": {"eq": str(team_id or "").strip()}}}
    if str(state or "").strip():
        filter_payload["state"] = {"name": {"eq": str(state).strip()}}
    if str(assignee or "").strip():
        filter_payload["assignee"] = {"id": {"eq": str(assignee).strip()}}
    data = _graphql(
        credentials,
        """
        query Issues($filter: IssueFilter, $first: Int!) {
          issues(filter: $filter, first: $first) {
            nodes {
              id
              identifier
              title
              description
              priority
              url
              state {
                id
                name
              }
              assignee {
                id
                name
              }
            }
          }
        }
        """,
        variables={"filter": filter_payload, "first": max(1, min(int(limit or 20), 100))},
        http_json_request=http_json_request,
    )
    issues = data.get("issues") if isinstance(data.get("issues"), dict) else {}
    nodes = issues.get("nodes") if isinstance(issues.get("nodes"), list) else []
    return [item for item in nodes if isinstance(item, dict)]


def get_issue(
    credentials: Dict[str, Any],
    issue_id: str,
    *,
    http_json_request: Optional[LinearHttpRequest] = None,
) -> Dict[str, Any]:
    data = _graphql(
        credentials,
        """
        query Issue($id: String!) {
          issue(id: $id) {
            id
            identifier
            title
            description
            priority
            url
            state {
              id
              name
            }
            assignee {
              id
              name
            }
          }
        }
        """,
        variables={"id": str(issue_id or "").strip()},
        http_json_request=http_json_request,
    )
    issue = data.get("issue")
    return issue if isinstance(issue, dict) else {}


def create_issue(
    credentials: Dict[str, Any],
    team_id: str,
    title: str,
    description: str,
    *,
    priority: Optional[int] = None,
    assignee_id: Optional[str] = None,
    http_json_request: Optional[LinearHttpRequest] = None,
) -> Dict[str, Any]:
    input_payload: Dict[str, Any] = {
        "teamId": str(team_id or "").strip(),
        "title": str(title or "").strip(),
        "description": str(description or ""),
    }
    if priority is not None:
        input_payload["priority"] = int(priority)
    if str(assignee_id or "").strip():
        input_payload["assigneeId"] = str(assignee_id).strip()
    data = _graphql(
        credentials,
        """
        mutation IssueCreate($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue {
              id
              identifier
              title
              url
            }
          }
        }
        """,
        variables={"input": input_payload},
        http_json_request=http_json_request,
    )
    result = data.get("issueCreate")
    return result if isinstance(result, dict) else {}


def update_issue(
    credentials: Dict[str, Any],
    issue_id: str,
    *,
    state: Optional[str] = None,
    priority: Optional[int] = None,
    assignee_id: Optional[str] = None,
    http_json_request: Optional[LinearHttpRequest] = None,
) -> Dict[str, Any]:
    input_payload: Dict[str, Any] = {}
    if str(state or "").strip():
        input_payload["stateId"] = str(state).strip()
    if priority is not None:
        input_payload["priority"] = int(priority)
    if str(assignee_id or "").strip():
        input_payload["assigneeId"] = str(assignee_id).strip()
    data = _graphql(
        credentials,
        """
        mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) {
            success
            issue {
              id
              identifier
              title
              url
            }
          }
        }
        """,
        variables={"id": str(issue_id or "").strip(), "input": input_payload},
        http_json_request=http_json_request,
    )
    result = data.get("issueUpdate")
    return result if isinstance(result, dict) else {}


def list_projects(
    credentials: Dict[str, Any],
    team_id: str,
    *,
    http_json_request: Optional[LinearHttpRequest] = None,
) -> List[Dict[str, Any]]:
    data = _graphql(
        credentials,
        """
        query Projects($teamId: String!) {
          projects(filter: { teams: { some: { id: { eq: $teamId } } } }) {
            nodes {
              id
              name
              state
              progress
              url
            }
          }
        }
        """,
        variables={"teamId": str(team_id or "").strip()},
        http_json_request=http_json_request,
    )
    projects = data.get("projects") if isinstance(data.get("projects"), dict) else {}
    nodes = projects.get("nodes") if isinstance(projects.get("nodes"), list) else []
    return [item for item in nodes if isinstance(item, dict)]


def add_comment(
    credentials: Dict[str, Any],
    issue_id: str,
    body: str,
    *,
    http_json_request: Optional[LinearHttpRequest] = None,
) -> Dict[str, Any]:
    data = _graphql(
        credentials,
        """
        mutation CommentCreate($input: CommentCreateInput!) {
          commentCreate(input: $input) {
            success
            comment {
              id
              body
            }
          }
        }
        """,
        variables={"input": {"issueId": str(issue_id or "").strip(), "body": str(body or "")}},
        http_json_request=http_json_request,
    )
    result = data.get("commentCreate")
    return result if isinstance(result, dict) else {}
