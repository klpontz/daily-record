from datetime import date

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from retry_util import with_retries, TRANSPORT_ERRORS

ASANA_BASE = "https://app.asana.com/api/1.0"
_TIMEOUT = (10, 30)  # (connect, read) seconds


def _session() -> requests.Session:
    """Session whose adapter retries connect/header/5xx; body reads are
    retried at the application layer via _get_json + with_retries."""
    session = requests.Session()
    retry = Retry(
        total=4, connect=4, read=4, status=4,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _get_json(session, url, pat, params=None) -> dict:
    """GET with timeout; the body read is inside with_retries so a
    truncated-body IncompleteRead/ChunkedEncodingError is retried."""
    def _call():
        resp = session.get(url, headers=_headers(pat), params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    return with_retries(_call)


def _headers(pat: str) -> dict:
    return {"Authorization": f"Bearer {pat}"}


def _project_name(task: dict) -> str | None:
    """Return the first project name a task belongs to, or None."""
    memberships = task.get("memberships") or []
    if memberships:
        return memberships[0].get("project", {}).get("name")
    return None


def _fetch_completed_tasks(session, pat, workspace_gid, today) -> list:
    params = {
        "workspace": workspace_gid,
        "assignee": "me",
        "completed_since": f"{today}T00:00:00.000Z",
        "opt_fields": "name,completed,completed_at,gid,parent,memberships.project.name",
        "limit": 100,
    }
    data = _get_json(session, f"{ASANA_BASE}/tasks", pat, params)
    tasks = data.get("data", [])
    return [t for t in tasks if t.get("completed") and t.get("parent") is None]


def _get_my_gid(session, pat) -> str:
    return _get_json(session, f"{ASANA_BASE}/users/me", pat)["data"]["gid"]


def _fetch_my_comments(session, pat, workspace_gid, today) -> list:
    user_gid = _get_my_gid(session, pat)
    params = {"modified_on.after": today, "opt_fields": "name,gid,memberships.project.name", "limit": 100}
    data = _get_json(session, f"{ASANA_BASE}/workspaces/{workspace_gid}/tasks/search", pat, params)
    tasks = data.get("data", [])

    comments = []
    for task in tasks:
        try:
            stories = _get_json(
                session, f"{ASANA_BASE}/tasks/{task['gid']}/stories", pat,
                {"opt_fields": "created_at,created_by.gid,type,text,resource_subtype"},
            )
        except TRANSPORT_ERRORS:
            continue  # one bad task must not kill the whole pull
        for story in stories.get("data", []):
            if not story:
                continue
            if (
                story.get("type") == "comment"
                and (story.get("created_by") or {}).get("gid") == user_gid
                and story.get("created_at", "").startswith(today)
            ):
                comments.append({
                    "task": task["name"],
                    "project": _project_name(task),
                    "comment": story.get("text", ""),
                })
    return comments


def fetch_today_asana(pat, workspace_gid) -> dict:
    today = date.today().isoformat()
    session = _session()
    return {
        "completed_tasks": _fetch_completed_tasks(session, pat, workspace_gid, today),
        "comments": _fetch_my_comments(session, pat, workspace_gid, today),
    }


def format_for_prompt(data: dict) -> str:
    """Format Asana data as a readable string for the Claude prompt."""
    if not data["completed_tasks"] and not data["comments"]:
        return "No Asana activity today."

    lines = []

    if data["completed_tasks"]:
        lines.append("## Completed Tasks")
        for task in data["completed_tasks"]:
            project = _project_name(task)
            suffix = f" (project: {project})" if project else ""
            lines.append(f"- {task['name']}{suffix}")

    if data["comments"]:
        lines.append("## Comments Made")
        for item in data["comments"]:
            project = item.get("project")
            suffix = f" (project: {project})" if project else ""
            lines.append(f"- [{item['task']}]{suffix} {item['comment']}")

    return "\n".join(lines)
