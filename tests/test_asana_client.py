import pytest
from unittest.mock import patch, MagicMock
from asana_client import fetch_today_asana, format_for_prompt


def mock_get(url, **kwargs):
    """Helper: returns a mock requests.Response."""
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.status_code = 200
    if "users/me" in url:
        mock.json.return_value = {"data": {"gid": "U1"}}
    elif "stories" in url:
        mock.json.return_value = {"data": []}
    elif "tasks" in url:
        mock.json.return_value = {"data": []}
    else:
        mock.json.return_value = {"data": []}
    return mock


def test_fetch_returns_completed_tasks_and_comments_keys():
    with patch("asana_client.requests.get", side_effect=mock_get):
        result = fetch_today_asana("pat", "WS1")
    assert "completed_tasks" in result
    assert "comments" in result


def test_completed_tasks_filters_to_completed_only():
    def side_effect(url, **kwargs):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.status_code = 200
        if "users/me" in url:
            mock.json.return_value = {"data": {"gid": "U1"}}
        elif "stories" in url:
            mock.json.return_value = {"data": []}
        else:
            mock.json.return_value = {
                "data": [
                    {"gid": "T1", "name": "Done task", "completed": True, "completed_at": "2026-04-10T15:00:00Z"},
                    {"gid": "T2", "name": "Not done", "completed": False},
                ]
            }
        return mock

    with patch("asana_client.requests.get", side_effect=side_effect):
        result = fetch_today_asana("pat", "WS1")

    names = [t["name"] for t in result["completed_tasks"]]
    assert "Done task" in names
    assert "Not done" not in names


def test_comments_filters_to_user_and_today():
    today = "2026-04-10"

    def side_effect(url, **kwargs):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.status_code = 200
        if "users/me" in url:
            mock.json.return_value = {"data": {"gid": "U1"}}
        elif "stories" in url:
            mock.json.return_value = {
                "data": [
                    {"type": "comment", "created_by": {"gid": "U1"}, "created_at": f"{today}T10:00:00Z", "text": "my comment"},
                    {"type": "comment", "created_by": {"gid": "U999"}, "created_at": f"{today}T11:00:00Z", "text": "other"},
                    {"type": "system", "created_by": {"gid": "U1"}, "created_at": f"{today}T12:00:00Z", "text": "moved task"},
                ]
            }
        else:
            mock.json.return_value = {"data": [{"gid": "T1", "name": "Some task"}]}
        return mock

    with patch("asana_client.requests.get", side_effect=side_effect):
        with patch("asana_client.date") as mock_date:
            mock_date.today.return_value.isoformat.return_value = today
            result = fetch_today_asana("pat", "WS1")

    comment_texts = [c["comment"] for c in result["comments"]]
    assert "my comment" in comment_texts
    assert "other" not in comment_texts
    assert "moved task" not in comment_texts


def test_format_for_prompt_includes_task_names():
    data = {
        "completed_tasks": [{"name": "Finished the thing", "gid": "T1"}],
        "comments": [{"task": "Some task", "comment": "left a note"}]
    }
    result = format_for_prompt(data)
    assert "Finished the thing" in result
    assert "left a note" in result


def test_format_for_prompt_empty_returns_no_activity():
    result = format_for_prompt({"completed_tasks": [], "comments": []})
    assert result == "No Asana activity today."
