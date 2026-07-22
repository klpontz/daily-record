# Slack Attribution + Network Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the daily pull survive transient network drops, and capture/attribute what coworkers said in Slack threads and DMs the user participated in.

**Architecture:** Two independent concerns. (1) Resilience: an app-level retry helper wraps the request+body read for Asana (`requests`), and explicit `error_types` on the slack-sdk retry handler fix Slack — both target the same transport-error class. (2) Attribution: the Slack fetcher stops discarding coworkers' words, a disk-cached `SlackUserDirectory` resolves user IDs to names, and the formatter + prompt render attributed prose.

**Tech Stack:** Python 3, `requests` + `urllib3` (Asana), `slack_sdk` (Slack), `anthropic` (Haiku summarizer), `pytest`.

## Global Constraints

- Installed versions (verified): `urllib3` 2.6.3, `requests` 2.32.3, `slack-sdk` 3.35.0, `anthropic` present. Do not assume newer APIs.
- Slack app scopes present: `users:read`, `channels:history`, `channels:read`, `groups:history`, `groups:read`, `im:history`, `im:read`.
- **Retry targets exactly this transport-error class:** `http.client.IncompleteRead`, `ssl.SSLError` (covers `SSLEOFError`), `requests.exceptions.ChunkedEncodingError`, `requests.exceptions.ConnectionError`, `requests.exceptions.Timeout`. Backoff exponential ~1·2·4·8s, `max_retries=4`.
- **Partial-write behavior is correct and unchanged:** `pull_daily.run()` writes whichever source succeeds; only both-failed skips. Do not touch it.
- **Attribution scope unchanged:** only threads/DMs the user participated in. Capture everyone's words *within* those, not all channel chatter.
- TDD throughout: failing test first, minimal code, commit per task. All tests green before a task is done.
- Run tests from the project root: `cd /Users/pontz/Projects/daily-record && source venv/bin/activate`.
- Attribution output is **prose** (phase 1). No structured/graph output. No Asana attribution.

---

### Task 1: Shared retry helper (`retry_util.py`)

**Files:**
- Create: `retry_util.py`
- Test: `tests/test_retry_util.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `retry_util.TRANSPORT_ERRORS: tuple` — the transient error classes.
  - `retry_util.with_retries(fn, *, max_retries=4, base_delay=1.0, errors=TRANSPORT_ERRORS, sleep=time.sleep) -> Any` — calls `fn()`, retries on `errors` with exponential backoff, re-raises the last error after `max_retries` retries (so `max_retries+1` attempts total). `sleep` is injectable for tests.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_retry_util.py
import ssl
import pytest
import requests
from retry_util import with_retries, TRANSPORT_ERRORS


def _noop_sleep(_):
    pass


def test_returns_value_on_first_success():
    assert with_retries(lambda: "ok", sleep=_noop_sleep) == "ok"


def test_retries_then_succeeds():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ChunkedEncodingError("truncated body")
        return "ok"

    assert with_retries(fn, sleep=_noop_sleep) == "ok"
    assert calls["n"] == 3


def test_exhausts_and_reraises_last_error():
    def fn():
        raise ssl.SSLError("UNEXPECTED_EOF_WHILE_READING")

    with pytest.raises(ssl.SSLError):
        with_retries(fn, max_retries=2, sleep=_noop_sleep)


def test_does_not_catch_unrelated_errors():
    def fn():
        raise ValueError("not transient")

    with pytest.raises(ValueError):
        with_retries(fn, sleep=_noop_sleep)


def test_transport_errors_includes_the_observed_classes():
    from http.client import IncompleteRead
    assert IncompleteRead in TRANSPORT_ERRORS
    assert ssl.SSLError in TRANSPORT_ERRORS
    assert requests.exceptions.ChunkedEncodingError in TRANSPORT_ERRORS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_retry_util.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'retry_util'`.

- [ ] **Step 3: Write the implementation**

```python
# retry_util.py
"""Application-level retry for transient transport failures.

Needed because neither slack_sdk's default handlers nor requests' HTTPAdapter
retry the mid-body read errors we actually hit (IncompleteRead / SSL EOF).
This wraps the full call — including the body read — so the retry sees them.
"""
import ssl
import time
from http.client import IncompleteRead

import requests

TRANSPORT_ERRORS = (
    IncompleteRead,
    ssl.SSLError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)


def with_retries(fn, *, max_retries=4, base_delay=1.0, errors=TRANSPORT_ERRORS, sleep=time.sleep):
    """Call fn(); retry on transient transport errors with exponential backoff.

    Retries up to max_retries times (max_retries + 1 attempts total). Re-raises
    the last error if all attempts fail. `sleep` is injectable for tests.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except errors:
            attempt += 1
            if attempt > max_retries:
                raise
            sleep(base_delay * (2 ** (attempt - 1)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_retry_util.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add retry_util.py tests/test_retry_util.py
git commit -m "feat: add app-level retry helper for transient transport errors"
```

---

### Task 2: Asana resilience — session, retry, timeouts

**Files:**
- Modify: `asana_client.py`
- Modify: `requirements.txt` (pin urllib3)
- Test: `tests/test_asana_client.py` (repoint mocks from `requests.get` to `_session`)

**Interfaces:**
- Consumes: `retry_util.with_retries`, `retry_util.TRANSPORT_ERRORS`.
- Produces:
  - `asana_client._session() -> requests.Session` — session with a mounted `HTTPAdapter(max_retries=Retry(...))`.
  - `asana_client._get_json(session, url, pat, params=None) -> dict` — GET with timeout, wrapped in `with_retries`, `raise_for_status`, returns parsed JSON.
  - `_fetch_completed_tasks(session, pat, workspace_gid, today)`, `_get_my_gid(session, pat)`, `_fetch_my_comments(session, pat, workspace_gid, today)` now take `session` as first arg.
  - `fetch_today_asana(pat, workspace_gid)` unchanged signature (builds the session internally).

- [ ] **Step 1: Write the failing tests** (replace the whole test file)

```python
# tests/test_asana_client.py
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
    """The C2 case: error at .json()/body time, not at get() time."""
    calls = {"n": 0}

    def flaky_get(url, **kwargs):
        if "users/me" in url:
            return _resp({"data": {"gid": "U1"}})
        if "tasks" in url and "search" not in url:
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.exceptions.ChunkedEncodingError("truncated body")
            return _resp({"data": []})
        return _resp({"data": []})

    with patch("asana_client._session", return_value=_fake_session(flaky_get)):
        with patch("retry_util.time.sleep", return_value=None):
            result = fetch_today_asana("pat", "WS1")
    assert calls["n"] == 2  # retried once
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_asana_client.py -v`
Expected: FAIL — `_session` does not exist / mocks don't match (`AttributeError` on `asana_client._session`).

- [ ] **Step 3: Rewrite `asana_client.py` fetch layer**

Replace the top of the file and the three fetch functions. Keep `_headers`, `_project_name`, `format_for_prompt` exactly as they are.

```python
import ssl
from datetime import date

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from retry_util import with_retries

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
```

Then the three fetchers (note the new `session` first parameter and `_get_json` usage):

```python
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
        except (requests.exceptions.RequestException, ssl.SSLError):
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
```

Note: the retry backoff sleep lives in `retry_util`, so the two retry tests in Step 1 patch `retry_util.time.sleep` (already written that way) — no `import time` is needed in `asana_client.py`.

- [ ] **Step 4: Pin urllib3 in `requirements.txt`**

Add a line pinning urllib3 to the 2.x line (locks `allowed_methods` / `Retry` semantics):

```
urllib3>=2,<3
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_asana_client.py -v`
Expected: PASS (all). If a retry test hangs, confirm `retry_util.time.sleep` is patched.

- [ ] **Step 6: Commit**

```bash
git add asana_client.py requirements.txt tests/test_asana_client.py
git commit -m "feat: retry + timeouts on Asana calls (fixes mid-body IncompleteRead)"
```

---

### Task 3: Slack client retry configuration

**Files:**
- Modify: `slack_client.py` (add `build_web_client`; use it in `fetch_today_slack`)
- Test: `tests/test_slack_client.py` (add one test; keep the rest for Task 5)

**Interfaces:**
- Consumes: nothing new.
- Produces: `slack_client.build_web_client(token) -> WebClient` — a `WebClient` whose `ConnectionErrorRetryHandler` includes `IncompleteRead` and `ssl.SSLError` in `error_types` (the C1 fix), plus a `RateLimitErrorRetryHandler`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_slack_client.py`)

```python
def test_build_web_client_retries_incomplete_read_and_ssl_error():
    import ssl
    from http.client import IncompleteRead
    from slack_sdk.http_retry.builtin_handlers import ConnectionErrorRetryHandler
    from slack_client import build_web_client

    client = build_web_client("xoxb-test")
    conn_handlers = [h for h in client.retry_handlers if isinstance(h, ConnectionErrorRetryHandler)]
    assert conn_handlers, "no ConnectionErrorRetryHandler configured"
    error_types = conn_handlers[0].error_types
    assert IncompleteRead in error_types
    assert ssl.SSLError in error_types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_slack_client.py::test_build_web_client_retries_incomplete_read_and_ssl_error -v`
Expected: FAIL with `ImportError: cannot import name 'build_web_client'`.

- [ ] **Step 3: Implement `build_web_client` and use it in `fetch_today_slack`**

Add near the top of `slack_client.py`:

```python
import ssl
from http.client import IncompleteRead
from http.client import RemoteDisconnected
from urllib.error import URLError

from slack_sdk.http_retry.builtin_handlers import (
    ConnectionErrorRetryHandler,
    RateLimitErrorRetryHandler,
)


def build_web_client(token: str) -> WebClient:
    """WebClient with retry handlers that actually cover the failures we hit.

    slack-sdk's default ConnectionErrorRetryHandler only retries
    URLError/ConnectionResetError/RemoteDisconnected — NOT IncompleteRead or
    SSLEOFError, which is what Slack drops on. Adding them to error_types makes
    the SDK's retry loop (which does wrap the body read) retry correctly.
    """
    conn_retry = ConnectionErrorRetryHandler(
        max_retry_count=4,
        error_types=[URLError, ConnectionResetError, RemoteDisconnected, IncompleteRead, ssl.SSLError],
    )
    rate_retry = RateLimitErrorRetryHandler(max_retry_count=3)
    return WebClient(token=token, retry_handlers=[conn_retry, rate_retry])
```

In `fetch_today_slack`, replace `client = WebClient(token=token)` with:

```python
    client = build_web_client(token)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_slack_client.py -v`
Expected: the new test PASSES. Existing fetch tests still pass (they patch `slack_client.WebClient`, which `build_web_client` calls). If any existing fetch test fails, it is because of Task 5's shape change — but Task 5 hasn't run yet, so they must still pass here.

- [ ] **Step 5: Commit**

```bash
git add slack_client.py tests/test_slack_client.py
git commit -m "feat: Slack retry handler covers IncompleteRead/SSL EOF"
```

---

### Task 4: User directory (`user_directory.py`)

**Files:**
- Create: `user_directory.py`
- Modify: `.gitignore` (add `.cache/`)
- Test: `tests/test_user_directory.py`

**Interfaces:**
- Consumes: a Slack client exposing `users_list(**params)` and `users_info(user=...)` (real `WebClient` or a fake).
- Produces: `user_directory.SlackUserDirectory(client, own_id, cache_path=DEFAULT_CACHE)` with `.resolve_many(ids: Iterable[str]) -> dict[str, str]`. Own id → `"You"`. Missing scope / API error → raw IDs (never raises). Cache is atomic and corruption-tolerant.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_user_directory.py
import json
import time
from unittest.mock import MagicMock
from slack_sdk.errors import SlackApiError
from user_directory import SlackUserDirectory


def _client(members=None, info=None, list_error=None, info_error=None):
    c = MagicMock()
    if list_error:
        c.users_list.side_effect = list_error
    else:
        c.users_list.return_value = {"members": members or [], "response_metadata": {"next_cursor": ""}}
    if info_error:
        c.users_info.side_effect = info_error
    else:
        c.users_info.return_value = {"user": info or {}}
    return c


def test_resolves_names_from_bulk_list(tmp_path):
    c = _client(members=[
        {"id": "U1", "profile": {"real_name": "Helen Wang"}},
        {"id": "U2", "profile": {"real_name": "Juan Chen"}},
    ])
    d = SlackUserDirectory(c, own_id="U9", cache_path=tmp_path / "u.json")
    assert d.resolve_many(["U1", "U2"]) == {"U1": "Helen Wang", "U2": "Juan Chen"}


def test_own_id_renders_as_you(tmp_path):
    c = _client(members=[])
    d = SlackUserDirectory(c, own_id="U9", cache_path=tmp_path / "u.json")
    assert d.resolve_many(["U9"]) == {"U9": "You"}


def test_name_preference_chain(tmp_path):
    c = _client(members=[
        {"id": "U1", "profile": {"display_name": "hwang"}},          # no real_name
        {"id": "U2", "name": "juan"},                                 # no profile
        {"id": "U3"},                                                 # nothing -> id
    ])
    d = SlackUserDirectory(c, own_id="U9", cache_path=tmp_path / "u.json")
    assert d.resolve_many(["U1", "U2", "U3"]) == {"U1": "hwang", "U2": "juan", "U3": "U3"}


def test_lazy_lookup_for_id_absent_from_bulk(tmp_path):
    c = _client(members=[{"id": "U1", "profile": {"real_name": "Helen"}}],
                info={"id": "U2", "profile": {"real_name": "New Hire"}})
    d = SlackUserDirectory(c, own_id="U9", cache_path=tmp_path / "u.json")
    assert d.resolve_many(["U2"]) == {"U2": "New Hire"}
    c.users_info.assert_called_once_with(user="U2")


def test_missing_scope_degrades_to_raw_ids(tmp_path, capsys):
    err = SlackApiError("missing_scope", {"error": "missing_scope", "needed": "users:read"})
    c = _client(list_error=err)
    d = SlackUserDirectory(c, own_id="U9", cache_path=tmp_path / "u.json")
    assert d.resolve_many(["U1"]) == {"U1": "U1"}  # raw id, no raise
    assert "⚠" in capsys.readouterr().out


def test_uses_fresh_cache_without_calling_api(tmp_path):
    cache = tmp_path / "u.json"
    cache.write_text(json.dumps({"U1": "Cached Name"}))
    c = _client(members=[{"id": "U1", "profile": {"real_name": "Should Not Be Used"}}])
    d = SlackUserDirectory(c, own_id="U9", cache_path=cache)
    assert d.resolve_many(["U1"]) == {"U1": "Cached Name"}
    c.users_list.assert_not_called()


def test_stale_cache_triggers_refresh(tmp_path):
    cache = tmp_path / "u.json"
    cache.write_text(json.dumps({"U1": "Old Name"}))
    old = time.time() - (8 * 24 * 3600)
    import os
    os.utime(cache, (old, old))
    c = _client(members=[{"id": "U1", "profile": {"real_name": "Fresh Name"}}])
    d = SlackUserDirectory(c, own_id="U9", cache_path=cache)
    assert d.resolve_many(["U1"]) == {"U1": "Fresh Name"}
    c.users_list.assert_called()


def test_corrupt_cache_treated_as_miss(tmp_path):
    cache = tmp_path / "u.json"
    cache.write_text("{not valid json")
    c = _client(members=[{"id": "U1", "profile": {"real_name": "Recovered"}}])
    d = SlackUserDirectory(c, own_id="U9", cache_path=cache)
    assert d.resolve_many(["U1"]) == {"U1": "Recovered"}


def test_bulk_load_writes_cache_atomically(tmp_path):
    cache = tmp_path / "u.json"
    c = _client(members=[{"id": "U1", "profile": {"real_name": "Helen"}}])
    d = SlackUserDirectory(c, own_id="U9", cache_path=cache)
    d.resolve_many(["U1"])
    assert json.loads(cache.read_text())["U1"] == "Helen"
    assert not (tmp_path / "u.tmp").exists()  # temp file cleaned up by os.replace
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_user_directory.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'user_directory'`.

- [ ] **Step 3: Write the implementation**

```python
# user_directory.py
"""Resolve Slack user IDs to display names, disk-cached.

Degrades to raw IDs on any Slack error (e.g. missing users:read scope) so it
never takes down the Slack section of the pull.
"""
import json
import os
import time
from pathlib import Path

from slack_sdk.errors import SlackApiError

DEFAULT_CACHE = Path(__file__).parent / ".cache" / "slack_users.json"
CACHE_TTL_SECONDS = 7 * 24 * 3600


def _pick_name(u: dict) -> str:
    profile = u.get("profile") or {}
    return profile.get("real_name") or profile.get("display_name") or u.get("name") or u.get("id", "")


class SlackUserDirectory:
    def __init__(self, client, own_id, cache_path=DEFAULT_CACHE):
        self.client = client
        self.own_id = own_id
        self.cache_path = Path(cache_path)
        self._names = None  # loaded lazily

    def resolve_many(self, ids):
        self._ensure_loaded()
        out = {}
        for uid in ids:
            if uid == self.own_id:
                out[uid] = "You"
            elif uid in self._names:
                out[uid] = self._names[uid]
            else:
                out[uid] = self._lazy_lookup(uid)
        return out

    # --- internals ---

    def _ensure_loaded(self):
        if self._names is not None:
            return
        cached = self._load_cache()
        if cached is not None:
            self._names = cached
        else:
            self._names = self._bulk_load()
            if self._names:
                self._save_cache(self._names)

    def _load_cache(self):
        try:
            if self.cache_path.exists() and (time.time() - self.cache_path.stat().st_mtime) < CACHE_TTL_SECONDS:
                return json.loads(self.cache_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def _save_cache(self, names):
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(names))
            os.replace(tmp, self.cache_path)
        except OSError:
            pass  # cache is best-effort

    def _bulk_load(self):
        names = {}
        cursor = None
        try:
            while True:
                params = {"limit": 200}
                if cursor:
                    params["cursor"] = cursor
                resp = self.client.users_list(**params)
                for u in resp.get("members", []):
                    names[u["id"]] = _pick_name(u)
                cursor = (resp.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
        except SlackApiError as e:
            if e.response.get("error") == "missing_scope":
                needed = e.response.get("needed", "users:read")
                print(f"⚠ Slack user names unavailable: missing scope '{needed}'. Showing raw IDs.")
            else:
                print(f"⚠ Slack users.list failed: {e}. Showing raw IDs.")
        return names

    def _lazy_lookup(self, uid):
        try:
            resp = self.client.users_info(user=uid)
            name = _pick_name(resp["user"])
            self._names[uid] = name
            self._save_cache(self._names)
            return name
        except SlackApiError:
            return uid
```

- [ ] **Step 4: Add `.cache/` to `.gitignore`**

Append a line to `.gitignore`:

```
.cache/
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_user_directory.py -v`
Expected: PASS (9 tests).

- [ ] **Step 6: Commit**

```bash
git add user_directory.py tests/test_user_directory.py .gitignore
git commit -m "feat: disk-cached Slack user-id -> name resolver"
```

---

### Task 5: Slack fetch — capture coworkers' words + ordering

**Files:**
- Modify: `slack_client.py` (`_fetch_channel_messages`, `_fetch_dm_messages`, add `_fetch_thread`)
- Test: `tests/test_slack_client.py` (rewrite the ~6 fetch-shape tests)

**Interfaces:**
- Consumes: nothing new.
- Produces new `fetch_today_slack` return shape:
  - `channels`: `[{"channel_id": str, "parent": {"user_id": str, "text": str}, "replies": [{"user_id": str, "text": str}, ...], "is_mine": bool}, ...]`
  - `dms`: `[{"channel_id": str, "partner_id": str, "messages": [{"user_id": str, "text": str}, ...]}, ...]` (chronological)

- [ ] **Step 1: Rewrite the fetch-shape tests** (replace the six tests `test_user_messages_included` through `test_user_top_level_message_with_no_own_replies_has_null_thread`, and the two DM tests keep working; add ordering + coworker tests). Keep `test_today_oldest_ts_returns_local_midnight`, `test_fetch_returns_channels_and_dms_keys`, the two `format_for_prompt` tests (they move to Task 6), the error-logging tests, and the Task 3 test.

```python
def test_user_top_level_message_captures_full_thread_attributed():
    with patch("slack_client.WebClient"), \
         patch("slack_client.build_web_client") as mock_build, \
         patch("slack_client._today_oldest_ts", return_value="0"), \
         patch("slack_client._lookback_oldest_ts", return_value="0"):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [{"user": "U123", "text": "original post", "ts": "1000.0", "reply_count": 2}]
        }
        mock_client.conversations_replies.return_value = {
            "messages": [
                {"user": "U123", "text": "original post", "ts": "1000.0"},
                {"user": "U999", "text": "their response", "ts": "1001.0"},
                {"user": "U123", "text": "my follow-up", "ts": "1002.0"},
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}
        result = fetch_today_slack("token", "U123", ["C001"])

    ch = result["channels"][0]
    assert ch["parent"] == {"user_id": "U123", "text": "original post"}
    assert ch["replies"] == [
        {"user_id": "U999", "text": "their response"},
        {"user_id": "U123", "text": "my follow-up"},
    ]
    assert ch["is_mine"] is True


def test_thread_started_by_other_captures_everyone_when_user_replied():
    with patch("slack_client.build_web_client") as mock_build, \
         patch("slack_client._today_oldest_ts", return_value="0"), \
         patch("slack_client._lookback_oldest_ts", return_value="0"):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [{"user": "U999", "text": "parent", "ts": "1000.0", "reply_count": 3, "latest_reply": "1002.0"}]
        }
        mock_client.conversations_replies.return_value = {
            "messages": [
                {"user": "U999", "text": "parent", "ts": "1000.0"},
                {"user": "U123", "text": "my reply", "ts": "1001.0"},
                {"user": "U888", "text": "their reply", "ts": "1002.0"},
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}
        result = fetch_today_slack("token", "U123", ["C001"])

    ch = result["channels"][0]
    assert ch["parent"] == {"user_id": "U999", "text": "parent"}
    assert ch["replies"] == [
        {"user_id": "U123", "text": "my reply"},
        {"user_id": "U888", "text": "their reply"},
    ]
    assert ch["is_mine"] is False


def test_thread_where_user_never_participated_is_excluded():
    with patch("slack_client.build_web_client") as mock_build, \
         patch("slack_client._today_oldest_ts", return_value="0"), \
         patch("slack_client._lookback_oldest_ts", return_value="0"):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [{"user": "U999", "text": "parent", "ts": "1000.0", "reply_count": 1, "latest_reply": "1001.0"}]
        }
        mock_client.conversations_replies.return_value = {
            "messages": [
                {"user": "U999", "text": "parent", "ts": "1000.0"},
                {"user": "U888", "text": "a reply", "ts": "1001.0"},
            ]
        }
        mock_client.conversations_list.return_value = {"channels": []}
        result = fetch_today_slack("token", "U123", ["C001"])
    assert result["channels"] == []


def test_other_users_standalone_message_excluded():
    with patch("slack_client.build_web_client") as mock_build:
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.conversations_history.return_value = {
            "messages": [{"user": "U999", "text": "someone else", "ts": "1000.0", "reply_count": 0}]
        }
        mock_client.conversations_list.return_value = {"channels": []}
        result = fetch_today_slack("token", "U123", ["C001"])
    assert result["channels"] == []


def test_dm_captures_both_sides_in_chronological_order():
    with patch("slack_client.build_web_client") as mock_build, \
         patch("slack_client._today_oldest_ts", return_value="0"):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        # conversations_history returns NEWEST first
        mock_client.conversations_list.return_value = {"channels": [{"id": "D1", "user": "U777"}]}
        mock_client.conversations_history.return_value = {
            "messages": [
                {"user": "U123", "text": "second (mine)", "ts": "1002.0"},
                {"user": "U777", "text": "first (theirs)", "ts": "1001.0"},
            ]
        }
        result = fetch_today_slack("token", "U123", [])
    dm = result["dms"][0]
    assert dm["partner_id"] == "U777"
    assert dm["messages"] == [
        {"user_id": "U777", "text": "first (theirs)"},
        {"user_id": "U123", "text": "second (mine)"},
    ]


def test_dm_without_user_message_excluded():
    with patch("slack_client.build_web_client") as mock_build, \
         patch("slack_client._today_oldest_ts", return_value="0"):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_client.conversations_list.return_value = {"channels": [{"id": "D1", "user": "U777"}]}
        mock_client.conversations_history.return_value = {
            "messages": [{"user": "U777", "text": "only them", "ts": "1001.0"}]
        }
        result = fetch_today_slack("token", "U123", [])
    assert result["dms"] == []
```

Also update the two existing error-logging tests (`test_channel_api_error_logs_warning_and_returns_empty`, `test_dm_list_api_error_logs_warning_and_returns_empty`, `test_dm_missing_scope_prints_once_and_stops`) to patch `slack_client.build_web_client` instead of `slack_client.WebClient` (they otherwise pass unchanged).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_slack_client.py -v`
Expected: the new shape tests FAIL (`KeyError: 'parent'` / old code returns `text`/`thread`).

- [ ] **Step 3: Rewrite the fetch functions**

Add helper and replace `_fetch_channel_messages` and `_fetch_dm_messages`:

```python
def _fetch_thread(client, channel_id, ts, oldest):
    """Full thread as ordered [{user_id, text}], parent first.
    conversations_replies returns oldest-first."""
    try:
        thread = client.conversations_replies(channel=channel_id, ts=ts, oldest=oldest, limit=100)
        return [{"user_id": m.get("user", ""), "text": m.get("text", "")} for m in thread.get("messages", [])]
    except SlackApiError:
        return []


def _fetch_channel_messages(client, channel_id, user_id, oldest):
    results = []
    lookback = _lookback_oldest_ts()
    try:
        response = client.conversations_history(channel=channel_id, oldest=lookback, limit=200)
        for msg in response.get("messages", []):
            msg_ts = msg.get("ts", "0")
            latest_reply = msg.get("latest_reply", msg_ts)
            has_thread_activity_today = msg.get("reply_count", 0) > 0 and float(latest_reply) >= float(oldest)
            msg_is_today = float(msg_ts) >= float(oldest)
            user_started = msg.get("user") == user_id

            if user_started and msg_is_today:
                thread = _fetch_thread(client, channel_id, msg_ts, oldest) if msg.get("reply_count", 0) > 0 else []
                parent = thread[0] if thread else {"user_id": msg.get("user", ""), "text": msg.get("text", "")}
                replies = thread[1:] if thread else []
                results.append({"channel_id": channel_id, "parent": parent, "replies": replies, "is_mine": True})
            elif has_thread_activity_today:
                thread = _fetch_thread(client, channel_id, msg_ts, oldest)
                if not thread:
                    continue
                parent, replies = thread[0], thread[1:]
                if user_started or any(r["user_id"] == user_id for r in replies):
                    results.append({"channel_id": channel_id, "parent": parent, "replies": replies, "is_mine": user_started})
    except SlackApiError as e:
        print(f"⚠ Slack API error fetching channel {channel_id}: {e}")
    # conversations_history is newest-first; present threads oldest-first
    return list(reversed(results))


def _fetch_dm_messages(client, user_id, oldest):
    results = []
    try:
        dms = client.conversations_list(types="im", limit=50).get("channels", [])
        for dm in dms:
            try:
                history = client.conversations_history(channel=dm["id"], oldest=oldest, limit=100)
                ordered = list(reversed(history.get("messages", [])))  # newest-first -> chronological
                if any(m.get("user") == user_id for m in ordered):
                    messages = [{"user_id": m.get("user", ""), "text": m.get("text", "")} for m in ordered]
                    results.append({"channel_id": dm["id"], "partner_id": dm.get("user", ""), "messages": messages})
            except SlackApiError as e:
                if e.response.get("error") == "missing_scope":
                    needed = e.response.get("needed", "unknown scope")
                    print(f"⚠ Slack DMs skipped: missing scope '{needed}'. Add it at api.slack.com/apps.")
                    break
                print(f"⚠ Slack API error fetching DM {dm.get('id')}: {e}")
    except SlackApiError as e:
        print(f"⚠ Slack API error listing DMs: {e}")
    return results
```

Update the `fetch_today_slack` docstring's Returns block to the new shape (cosmetic).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_slack_client.py -v`
Expected: all fetch tests PASS. (The two `format_for_prompt` tests may still pass against old formatter if unchanged — they get rewritten in Task 6.)

- [ ] **Step 5: Commit**

```bash
git add slack_client.py tests/test_slack_client.py
git commit -m "feat: capture coworkers' words + chronological order in Slack fetch"
```

---

### Task 6: Formatter attribution

**Files:**
- Modify: `slack_client.py` (`format_for_prompt`)
- Test: `tests/test_slack_client.py` (rewrite the two `format_for_prompt` tests)

**Interfaces:**
- Consumes: `SlackUserDirectory.resolve_many` (Task 4), the new fetch shape (Task 5).
- Produces: `format_for_prompt(data, channel_names=None, directory=None) -> str` — attributed lines; `directory` optional (falls back to raw IDs / `unknown`).

- [ ] **Step 1: Rewrite the formatter tests**

```python
def test_format_attributes_names_via_directory():
    from unittest.mock import MagicMock
    directory = MagicMock()
    directory.resolve_many.return_value = {"U123": "You", "U999": "Helen Wang"}
    data = {
        "channels": [{
            "channel_id": "C001",
            "parent": {"user_id": "U999", "text": "next-grade isn't useful"},
            "replies": [{"user_id": "U123", "text": "agreed, it's not a reference point"}],
            "is_mine": False,
        }],
        "dms": [],
    }
    result = format_for_prompt(data, channel_names={"C001": "training"}, directory=directory)
    assert "[#training] Helen Wang: next-grade isn't useful" in result
    assert "You: agreed, it's not a reference point" in result


def test_format_dm_attributes_both_sides():
    from unittest.mock import MagicMock
    directory = MagicMock()
    directory.resolve_many.return_value = {"U123": "You", "U777": "Anand Puthumanappully"}
    data = {
        "channels": [],
        "dms": [{
            "channel_id": "D1",
            "partner_id": "U777",
            "messages": [
                {"user_id": "U777", "text": "Fresno wants bulk deletes"},
                {"user_id": "U123", "text": "confirming with the team"},
            ],
        }],
    }
    result = format_for_prompt(data, directory=directory)
    assert "[DM with Anand Puthumanappully]" in result
    assert "Anand Puthumanappully: Fresno wants bulk deletes" in result
    assert "You: confirming with the team" in result


def test_format_without_directory_falls_back_to_ids():
    data = {"channels": [{"channel_id": "C001",
            "parent": {"user_id": "U999", "text": "hi"}, "replies": [], "is_mine": False}], "dms": []}
    result = format_for_prompt(data)
    assert "U999: hi" in result


def test_format_empty_returns_no_activity():
    assert format_for_prompt({"channels": [], "dms": []}) == "No Slack activity today."
```

(Delete the old `test_format_for_prompt_produces_string`.)

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_slack_client.py -k format -v`
Expected: FAIL (old formatter reads `item["text"]`, KeyError on `parent`).

- [ ] **Step 3: Rewrite `format_for_prompt`**

```python
def format_for_prompt(data, channel_names=None, directory=None):
    """Render fetched Slack data as attributed lines for the summarizer prompt."""
    if not data["channels"] and not data["dms"]:
        return "No Slack activity today."

    names = channel_names or {}

    ids = set()
    for item in data["channels"]:
        ids.add(item["parent"]["user_id"])
        ids.update(r["user_id"] for r in item["replies"])
    for dm in data["dms"]:
        ids.add(dm.get("partner_id", ""))
        ids.update(m["user_id"] for m in dm["messages"])
    ids.discard("")

    resolved = directory.resolve_many(ids) if directory else {}

    def who(uid):
        return resolved.get(uid, uid or "unknown")

    def _channel_label(channel_id):
        name = names.get(channel_id, channel_id)
        return f"#{name}" if not name.startswith("#") else name

    lines = []
    if data["channels"]:
        lines.append("## Channel Threads")
        for item in data["channels"]:
            label = _channel_label(item["channel_id"])
            p = item["parent"]
            lines.append(f"[{label}] {who(p['user_id'])}: {p['text']}")
            for r in item["replies"]:
                lines.append(f"  {who(r['user_id'])}: {r['text']}")

    if data["dms"]:
        lines.append("## Direct Messages")
        for dm in data["dms"]:
            lines.append(f"[DM with {who(dm.get('partner_id', ''))}]")
            for m in dm["messages"]:
                lines.append(f"  {who(m['user_id'])}: {m['text']}")

    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_slack_client.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add slack_client.py tests/test_slack_client.py
git commit -m "feat: attribute Slack messages to real names in formatter"
```

---

### Task 7: Wire the directory into the pull

**Files:**
- Modify: `pull_daily.py` (`run()` builds a directory and passes it to `slack_format`)
- Test: `tests/test_pull_daily.py` (confirm existing tests still pass; add one wiring test)

**Interfaces:**
- Consumes: `slack_client.build_web_client`, `user_directory.SlackUserDirectory`, `format_for_prompt(data, channel_names, directory)`.
- Produces: no signature change to `run()`.

- [ ] **Step 1: Add the wiring test**

```python
def test_run_passes_directory_to_slack_format(tmp_path):
    log_dir = tmp_path / "logs"; log_dir.mkdir()
    template = tmp_path / "template.md"; template.write_text("### Slack\n-\n\n### Asana\n-\n")
    config = {
        "anthropic_api_key": "key", "slack_bot_token": "token", "slack_user_id": "U1",
        "slack_channels": ["C1"], "slack_channel_names": {}, "asana_pat": "pat",
        "asana_workspace_gid": "WS1", "log_dir": str(log_dir), "template_path": str(template),
        "obsidian_vault_path": "",
    }
    with patch("pull_daily.fetch_today_slack", return_value={"channels": [], "dms": []}), \
         patch("pull_daily.fetch_today_asana", return_value={"completed_tasks": [], "comments": []}), \
         patch("pull_daily.summarize", return_value="- ok"), \
         patch("pull_daily.slack_format") as mock_slack_format, \
         patch("pull_daily.asana_format", return_value="raw"), \
         patch("pull_daily.build_web_client"), \
         patch("pull_daily.SlackUserDirectory") as mock_dir:
        mock_slack_format.return_value = "raw slack"
        import pull_daily
        pull_daily.run(config, dry_run=True)

    # directory instance was passed as the third arg to slack_format
    assert mock_slack_format.call_args.args[2] is mock_dir.return_value
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_pull_daily.py::test_run_passes_directory_to_slack_format -v`
Expected: FAIL (`slack_format` currently called with 2 args; `call_args.args[2]` IndexError).

- [ ] **Step 3: Wire it up in `pull_daily.py`**

Update the import line:

```python
from slack_client import fetch_today_slack, format_for_prompt as slack_format, build_web_client
from user_directory import SlackUserDirectory
```

In `run()`, inside the Slack `try`, after `raw = slack_format(...)` is computed — replace the existing formatting line:

```python
        slack_count = len(slack_data["channels"]) + len(slack_data["dms"])
        directory = SlackUserDirectory(build_web_client(config["slack_bot_token"]), config["slack_user_id"])
        raw = slack_format(slack_data, config.get("slack_channel_names"), directory)
```

- [ ] **Step 4: Run the full pull_daily suite**

Run: `pytest tests/test_pull_daily.py -v`
Expected: PASS (all, including the existing partial-write/failure tests — they patch `slack_format` and `fetch_today_slack`, so `build_web_client`/`SlackUserDirectory` construct but do no network).

- [ ] **Step 5: Commit**

```bash
git add pull_daily.py tests/test_pull_daily.py
git commit -m "feat: build user directory and pass it to the Slack formatter"
```

---

### Task 8: Rewrite the summarizer prompt

**Files:**
- Modify: `prompts/slack_summarize.md`
- Test: `tests/test_slack_summarize_prompt.py` (new, lightweight)

**Interfaces:**
- Consumes: the attributed input format from Task 6.
- Produces: a prompt that instructs attributed prose and matches the new input labels.

- [ ] **Step 1: Write the lightweight guard test**

```python
# tests/test_slack_summarize_prompt.py
from pathlib import Path

PROMPT = Path(__file__).parent.parent / "prompts" / "slack_summarize.md"


def test_prompt_asks_for_attribution_and_drops_old_labels():
    text = PROMPT.read_text().lower()
    # instructs naming participants
    assert "name" in text and ("who" in text or "participant" in text)
    # no longer references the retired YOU:/CONTEXT: input labels
    assert "context:" not in text
    assert "> you:" not in text


def test_prompt_keeps_no_activity_sentinel():
    assert "_No notable Slack activity today_" in PROMPT.read_text()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_slack_summarize_prompt.py -v`
Expected: FAIL (current prompt contains `CONTEXT:` and `> YOU:`).

- [ ] **Step 3: Rewrite `prompts/slack_summarize.md`**

```markdown
You are summarizing a day's worth of Slack activity for a personal career log that also feeds a coworker knowledge graph.

The input is Slack threads and DMs the user took part in. Each line is attributed to a named person, `Name: message`. The user appears as `You`. Names may occasionally be a raw ID like `U0123ABC` when a name could not be resolved — pass those through unchanged; never invent a name.

Your job:
- Output a concise bullet list of meaningful Slack activity, grouped by thread or topic (one bullet each).
- **Name who was involved and what each person said, asked, or decided** — this attribution is the point. Preserve who raised an issue, who pushed back, who agreed, who committed to what.
- Attribute only names present in the input. Do not merge two people or guess an unnamed speaker.
- Keep the user's contribution as `you` (lowercase) in the prose.
- Preserve: decisions, questions raised, positions taken, work discussed, cross-team coordination.
- Drop: bare acknowledgements ("sounds good", "thanks", "ok"), emoji-only messages, content-free lines.
- Use plain, factual language — no interpretation or coaching.

Format:
- **channel or DM context**: who did/said what (lowercase, period at end, no brackets, no links).

Example output:
- **#training**: helen wang questioned whether next-grade helps pre-enrolled students; you traced the mechanism, conceded it doesn't fix the year-mismatch, and redirected to district enter date. juan chen asked about summer-session handling.
- **DM with anand puthumanappully**: anand flagged fresno's request to bulk-delete 300+ sign-ins; you said you'd confirm with the expanded-learning team first.
- **#client-success**: nicole duran signed off on pausing the all-sites sync until the july 30 rollover.

If there is no meaningful activity, output exactly: _No notable Slack activity today_
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_slack_summarize_prompt.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add prompts/slack_summarize.md tests/test_slack_summarize_prompt.py
git commit -m "feat: prompt rewrite for attributed Slack summaries"
```

---

### Task 9: Full-suite green + manual dry-run

**Files:** none (verification only).

- [ ] **Step 1: Run the whole suite**

Run: `pytest -v`
Expected: all tests PASS across `test_retry_util`, `test_asana_client`, `test_slack_client`, `test_user_directory`, `test_pull_daily`, `test_slack_summarize_prompt`, `test_log_writer`, `test_summarizer`.

- [ ] **Step 2: Manual dry-run against real APIs**

Run: `python pull_daily.py --dry-run`
Expected: prints a `### Slack` section with attributed names (real coworker names, "You" for the user) and no crash. If `users:read` were missing it would degrade to raw IDs — but it is present, so names should resolve.

- [ ] **Step 3: Final commit if any doc/notes changed**

```bash
git add -A
git commit -m "chore: verify full suite + dry-run for attribution + resilience" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage:**
- Part 1 Asana C2 → Task 1 (helper) + Task 2 (app-level retry around body read). ✔
- Part 1 Slack C1 → Task 3 (`error_types` on handler). ✔
- H3 ordering → Task 5 (`reversed` on history for DMs and channel results). ✔
- (a) directory + disk cache + atomic write + scope degrade + stale-name limit → Task 4. ✔
- (b) fetch coworkers + DM both sides → Task 5. ✔
- (c) formatter attribution → Task 6. ✔
- (d) prompt rewrite → Task 8. ✔
- M4 scope degrade tested → Task 4 `test_missing_scope_degrades_to_raw_ids`. ✔
- M5 atomic cache tested → Task 4 `test_bulk_load_writes_cache_atomically`, `test_corrupt_cache_treated_as_miss`. ✔
- M7 retry-behavior tests → Task 1 (unit), Task 2 (`test_transient_error_on_body_read_is_retried`), Task 3 (`error_types` assertion). ✔
- L9 single resolver interface → Task 4 (`resolve_many` only). ✔
- L10 urllib3 pin → Task 2 Step 4. ✔
- Wiring → Task 7. ✔

**Type consistency:** fetch produces `parent`/`replies`/`is_mine` (Task 5) consumed verbatim by the formatter (Task 6); `resolve_many(ids) -> dict` (Task 4) called by the formatter (Task 6); `build_web_client`/`SlackUserDirectory` (Tasks 3/4) consumed by `run()` (Task 7). Names match across tasks.

**Placeholder scan:** none — every code and test step is complete. One inline correction is called out explicitly in Task 2 Step 3 (patch `retry_util.time.sleep`, not `asana_client.time.sleep`).
