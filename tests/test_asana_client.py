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


def test_comments_warns_when_search_hits_result_cap(capsys):
    # tasks/search returns at most 100 results and has no next page, so a
    # full page means comments on the remaining tasks were never fetched.
    today = "2026-04-10"
    mapping = {
        "users/me": {"data": {"gid": "U1"}},
        "search": {"data": [{"gid": f"T{i}", "name": f"Task {i}"} for i in range(100)]},
    }
    with patch("asana_client._session", return_value=_fake_session(_dispatch(mapping))):
        with patch("asana_client.date") as mock_date:
            mock_date.today.return_value.isoformat.return_value = today
            fetch_today_asana("pat", "WS1")
    assert "100-task cap" in capsys.readouterr().out


def test_comments_no_cap_warning_below_limit(capsys):
    today = "2026-04-10"
    mapping = {
        "users/me": {"data": {"gid": "U1"}},
        "search": {"data": [{"gid": f"T{i}", "name": f"Task {i}"} for i in range(99)]},
    }
    with patch("asana_client._session", return_value=_fake_session(_dispatch(mapping))):
        with patch("asana_client.date") as mock_date:
            mock_date.today.return_value.isoformat.return_value = today
            fetch_today_asana("pat", "WS1")
    assert "cap" not in capsys.readouterr().out


def _comment_fetch(stories, notes="Request form text"):
    today = "2026-04-10"
    mapping = {
        "users/me": {"data": {"gid": "U1"}},
        "stories": {"data": stories},
        "search": {"data": [{"gid": "T1", "name": "Some task", "notes": notes}]},
    }
    with patch("asana_client._session", return_value=_fake_session(_dispatch(mapping))):
        with patch("asana_client.date") as mock_date:
            mock_date.today.return_value.isoformat.return_value = today
            return fetch_today_asana("pat", "WS1")["comments"]


def _story(gid, text, day="2026-04-10", kind="comment"):
    return {"type": kind, "created_by": {"gid": gid}, "created_at": f"{day}T10:00:00Z", "text": text}


def test_comments_carry_task_description_clipped():
    comments = _comment_fetch([_story("U1", "mine")], notes="x" * 900)
    assert comments[0]["task_description"] == "x" * 800 + "…"


def test_comments_carry_up_to_four_prior_thread_comments_labeled_by_author():
    stories = [
        _story("U2", "oldest", day="2026-04-01"),
        _story("U2", "please review", day="2026-04-02"),
        _story("U1", "on it", day="2026-04-03"),
        _story("U2", "moved task", day="2026-04-04", kind="system"),
        _story("U2", "draft is ready", day="2026-04-05"),
        _story("U2", "any update?", day="2026-04-09"),
        _story("U1", "looks great, publish"),
        _story("U2", "later reply"),
    ]
    comments = _comment_fetch(stories)
    assert [c["comment"] for c in comments] == ["looks great, publish"]
    assert comments[0]["prior_comments"] == [
        {"author": "teammate", "comment": "please review"},
        {"author": "log owner", "comment": "on it"},
        {"author": "teammate", "comment": "draft is ready"},
        {"author": "teammate", "comment": "any update?"},
    ]


def test_prior_thread_comments_are_clipped_and_collapsed():
    stories = [_story("U2", "line one\n\n" + "y" * 400, day="2026-04-09"), _story("U1", "mine")]
    prior = _comment_fetch(stories)[0]["prior_comments"][0]["comment"]
    assert prior.startswith("line one y")
    assert len(prior) == 301 and prior.endswith("…")


# --- created tasks and scoped comment search ---

def _search_router(created=None, commented=None, completed=None):
    """Route the two tasks/search calls by their params, and record them."""
    calls = []

    def get(url, **kwargs):
        assert "timeout" in kwargs, "session.get called without timeout"
        params = kwargs.get("params") or {}
        if "users/me" in url:
            return _resp({"data": {"gid": "U1"}})
        if "search" in url:
            calls.append(params)
            if "created_by.any" in params:
                return _resp({"data": created or []})
            return _resp({"data": commented or []})
        if "stories" in url:
            return _resp({"data": []})
        if url.endswith("/tasks"):
            return _resp({"data": completed or []})
        return _resp({"data": []})

    return get, calls


def _fetch(get, today="2026-04-10"):
    with patch("asana_client._session", return_value=_fake_session(get)):
        with patch("asana_client.date") as mock_date:
            mock_date.today.return_value.isoformat.return_value = today
            return fetch_today_asana("pat", "WS1")


def test_created_tasks_searched_by_me_since_local_midnight():
    get, calls = _search_router(created=[{"gid": "T9", "name": "Feature request", "parent": None}])
    result = _fetch(get)
    created_call = next(c for c in calls if "created_by.any" in c)
    assert created_call["created_by.any"] == "U1"
    # Local midnight as an aware timestamp, so evening work isn't lost to UTC.
    assert created_call["created_at.after"].startswith("2026-04-10T00:00:00")
    assert created_call["created_at.after"][19:] != ""
    assert [t["name"] for t in result["created_tasks"]] == ["Feature request"]


def test_created_tasks_exclude_subtasks():
    get, _ = _search_router(created=[
        {"gid": "T1", "name": "Parent", "parent": None},
        {"gid": "T2", "name": "Sub", "parent": {"gid": "T1"}},
    ])
    assert [t["name"] for t in _fetch(get)["created_tasks"]] == ["Parent"]


def test_created_task_also_completed_today_listed_once_as_completed():
    get, _ = _search_router(
        created=[{"gid": "T1", "name": "Quick one", "parent": None}],
        completed=[{"gid": "T1", "name": "Quick one", "completed": True, "parent": None}],
    )
    result = _fetch(get)
    assert [t["name"] for t in result["completed_tasks"]] == ["Quick one"]
    assert result["created_tasks"] == []


def test_comment_search_scoped_to_tasks_i_commented_on():
    get, calls = _search_router()
    _fetch(get)
    comment_call = next(c for c in calls if "created_by.any" not in c)
    assert comment_call["commented_on_by.any"] == "U1"


def test_format_for_prompt_includes_created_tasks():
    data = {"completed_tasks": [], "comments": [],
            "created_tasks": [{"name": "Student ID on instructor rosters", "gid": "T1"}]}
    result = format_for_prompt(data)
    assert "## Created Tasks" in result
    assert "Student ID on instructor rosters" in result
