import requests
from datetime import date

ASANA_BASE = "https://app.asana.com/api/1.0"


def _headers(pat: str) -> dict:
    return {"Authorization": f"Bearer {pat}"}


def _project_name(task: dict) -> str | None:
    """Return the first project name a task belongs to, or None."""
    memberships = task.get("memberships") or []
    if memberships:
        return memberships[0].get("project", {}).get("name")
    return None


def _fetch_completed_tasks(pat: str, workspace_gid: str, today: str) -> list:
    """Fetch tasks assigned to me and completed today."""
    params = {
        "workspace": workspace_gid,
        "assignee": "me",
        "completed_since": f"{today}T00:00:00.000Z",
        "opt_fields": "name,completed,completed_at,gid,parent,memberships.project.name",
        "limit": 100,
    }
    resp = requests.get(f"{ASANA_BASE}/tasks", headers=_headers(pat), params=params)
    resp.raise_for_status()
    tasks = resp.json().get("data", [])
    # Exclude subtasks (parent is not None) — they're too granular for the daily log
    return [t for t in tasks if t.get("completed") and t.get("parent") is None]


def _get_my_gid(pat: str) -> str:
    """Fetch the authenticated user's GID."""
    resp = requests.get(f"{ASANA_BASE}/users/me", headers=_headers(pat))
    resp.raise_for_status()
    return resp.json()["data"]["gid"]


def _fetch_my_comments(pat: str, workspace_gid: str, today: str) -> list:
    """Fetch comments I made today on any task modified today across the workspace.

    No assignee filter so comments on tasks assigned to others are captured.
    N+1 calls (one /stories per task) are unavoidable; Asana has no bulk stories endpoint.
    """
    user_gid = _get_my_gid(pat)

    # Use workspace search to find tasks modified today (no assignee restriction)
    params = {
        "modified_on.after": today,
        "opt_fields": "name,gid,memberships.project.name",
        "limit": 100,
    }
    resp = requests.get(f"{ASANA_BASE}/workspaces/{workspace_gid}/tasks/search", headers=_headers(pat), params=params)
    resp.raise_for_status()
    tasks = resp.json().get("data", [])

    comments = []
    for task in tasks:
        stories_resp = requests.get(
            f"{ASANA_BASE}/tasks/{task['gid']}/stories",
            headers=_headers(pat),
            params={"opt_fields": "created_at,created_by.gid,type,text,resource_subtype"}
        )
        if stories_resp.status_code != 200:
            continue
        for story in stories_resp.json().get("data", []):
            if (
                story.get("type") == "comment"
                and story.get("created_by", {}).get("gid") == user_gid
                and story.get("created_at", "").startswith(today)
            ):
                comments.append({
                    "task": task["name"],
                    "project": _project_name(task),
                    "comment": story.get("text", ""),
                })

    return comments


def fetch_today_asana(pat: str, workspace_gid: str) -> dict:
    """Fetch today's Asana activity.

    Returns:
        {
            "completed_tasks": [{"name": str, "gid": str, ...}, ...],
            "comments": [{"task": str, "comment": str}, ...]
        }
    """
    today = date.today().isoformat()
    return {
        "completed_tasks": _fetch_completed_tasks(pat, workspace_gid, today),
        "comments": _fetch_my_comments(pat, workspace_gid, today),
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
