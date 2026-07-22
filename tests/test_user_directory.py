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
