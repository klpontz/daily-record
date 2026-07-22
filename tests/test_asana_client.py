import ssl
import pytest
from unittest.mock import MagicMock, patch
import requests
from asana_client import fetch_today_asana, format_for_prompt


def _resp(payload):
    m = MagicMock()
    m.raise_for_status = MagicMock()
    m.status_code = 200
    m.json.return_value = payload
    return m


def _fake_session(side_effect):
    """A stand-in requests.Session whose .get dispatches on url."""
    session = MagicMock()
    session.get = MagicMock(side_effect=side_effect)
    return session


def _dispatch(mapping):
    def _get(url, **kwargs):
        # every call MUST pass a timeout (resilience contract)
        assert "timeout" in kwargs, "session.get called without timeout"
        for key, payload in mapping.items():
            if key in url:
                return _resp(payload)
        return _resp({"data": []})
    return _get


def test_fetch_returns_completed_tasks_and_comments_keys():
    with patch("asana_client._session", return_value=_fake_session(_dispatch({
        "users/me": {"data": {"gid": "U1"}},
    }))):
        result = fetch_today_asana("pat", "WS1")
    assert "completed_tasks" in result
    assert "comments" in result


def test_completed_tasks_filters_to_completed_only():
    mapping = {
        "users/me": {"data": {"gid": "U1"}},
        "stories": {"data": []},
        "tasks": {"data": [
            {"gid": "T1", "name": "Done task", "completed": True, "completed_at": "2026-04-10T15:00:00Z"},
            {"gid": "T2", "name": "Not done", "completed": False},
        ]},
    }
    with patch("asana_client._session", return_value=_fake_session(_dispatch(mapping))):
        result = fetch_today_asana("pat", "WS1")
    names = [t["name"] for t in result["completed_tasks"]]
    assert "Done task" in names
    assert "Not done" not in names


def test_completed_tasks_excludes_subtasks():
    mapping = {
        "users/me": {"data": {"gid": "U1"}},
        "stories": {"data": []},
        "tasks": {"data": [
            {"gid": "T1", "name": "Parent task", "completed": True, "parent": None},
            {"gid": "T2", "name": "Subtask", "completed": True, "parent": {"gid": "T1"}},
        ]},
    }
    with patch("asana_client._session", return_value=_fake_session(_dispatch(mapping))):
        result = fetch_today_asana("pat", "WS1")
    names = [t["name"] for t in result["completed_tasks"]]
    assert "Parent task" in names
    assert "Subtask" not in names


def test_comments_filters_to_user_and_today():
    today = "2026-04-10"
    mapping = {
        "users/me": {"data": {"gid": "U1"}},
        "stories": {"data": [
            {"type": "comment", "created_by": {"gid": "U1"}, "created_at": f"{today}T10:00:00Z", "text": "my comment"},
            {"type": "comment", "created_by": {"gid": "U999"}, "created_at": f"{today}T11:00:00Z", "text": "other"},
            {"type": "system", "created_by": {"gid": "U1"}, "created_at": f"{today}T12:00:00Z", "text": "moved task"},
        ]},
        "search": {"data": [{"gid": "T1", "name": "Some task"}]},
    }
    with patch("asana_client._session", return_value=_fake_session(_dispatch(mapping))):
        with patch("asana_client.date") as mock_date:
            mock_date.today.return_value.isoformat.return_value = today
            result = fetch_today_asana("pat", "WS1")
    texts = [c["comment"] for c in result["comments"]]
    assert "my comment" in texts
    assert "other" not in texts
    assert "moved task" not in texts


def test_transient_error_on_body_read_is_retried():
    """The C2 case: error at .json()/body-read time, not at get() time."""
    json_calls = {"n": 0}

    def flaky_json():
        json_calls["n"] += 1
        if json_calls["n"] == 1:
            raise requests.exceptions.ChunkedEncodingError("truncated body")
        return {"data": []}

    def get(url, **kwargs):
        assert "timeout" in kwargs, "session.get called without timeout"
        if "users/me" in url:
            return _resp({"data": {"gid": "U1"}})
        if "tasks" in url and "search" not in url:
            m = MagicMock()
            m.raise_for_status = MagicMock()
            m.status_code = 200
            m.json.side_effect = flaky_json
            return m
        return _resp({"data": []})

    with patch("asana_client._session", return_value=_fake_session(get)):
        with patch("retry_util.time.sleep", return_value=None):
            result = fetch_today_asana("pat", "WS1")
    assert json_calls["n"] == 2  # body read failed once, retried, succeeded
    assert result["completed_tasks"] == []


def test_bad_story_call_skips_task_not_whole_pull():
    def get(url, **kwargs):
        if "users/me" in url:
            return _resp({"data": {"gid": "U1"}})
        if "search" in url:
            return _resp({"data": [{"gid": "T1", "name": "Task"}]})
        if "stories" in url:
            raise requests.exceptions.ConnectionError("boom")
        return _resp({"data": []})

    with patch("asana_client._session", return_value=_fake_session(get)):
        with patch("retry_util.time.sleep", return_value=None):
            result = fetch_today_asana("pat", "WS1")
    assert result["comments"] == []  # story failure skipped, pull survived


def test_incomplete_read_on_story_skips_task_not_whole_pull():
    from http.client import IncompleteRead

    def get(url, **kwargs):
        if "users/me" in url:
            return _resp({"data": {"gid": "U1"}})
        if "search" in url:
            return _resp({"data": [{"gid": "T1", "name": "Task"}]})
        if "stories" in url:
            raise IncompleteRead(b"", 100)  # exhausts retries, re-raised
        return _resp({"data": []})

    with patch("asana_client._session", return_value=_fake_session(get)):
        with patch("retry_util.time.sleep", return_value=None):
            result = fetch_today_asana("pat", "WS1")  # must NOT raise
    assert result["comments"] == []


# --- format_for_prompt tests (unchanged behavior) ---

def test_format_for_prompt_includes_task_names():
    data = {"completed_tasks": [{"name": "Finished the thing", "gid": "T1"}],
            "comments": [{"task": "Some task", "project": None, "comment": "left a note"}]}
    result = format_for_prompt(data)
    assert "Finished the thing" in result
    assert "left a note" in result


def test_format_for_prompt_includes_project_name():
    data = {"completed_tasks": [{"name": "Contract Renewal 26/27", "gid": "T1",
            "memberships": [{"project": {"name": "San Ysidro"}}]}],
            "comments": [{"task": "Some task", "project": "San Ysidro", "comment": "left a note"}]}
    result = format_for_prompt(data)
    assert result.count("San Ysidro") == 2


def test_format_for_prompt_handles_task_with_no_project():
    data = {"completed_tasks": [{"name": "Orphan task", "gid": "T1", "memberships": []}], "comments": []}
    result = format_for_prompt(data)
    assert "Orphan task" in result
    assert "project:" not in result


def test_format_for_prompt_empty_returns_no_activity():
    assert format_for_prompt({"completed_tasks": [], "comments": []}) == "No Asana activity today."
